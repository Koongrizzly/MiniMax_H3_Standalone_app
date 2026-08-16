from __future__ import annotations

import math
from dataclasses import dataclass
import torch

_GIB = 1024 ** 3
_MIB = 1024 ** 2

VRAM_MANAGER_SIGNATURE = "V11_2_QWEN_ADMISSION_20260816C"
VRAM_MANAGER_VERSION = "V11.2"


@dataclass
class VRAMManagerConfig:
    runtime_free_gb: float = 0.50
    text_load_headroom_gb: float = 2.0
    diffusion_load_headroom_gb: float = 4.0
    vae_load_headroom_gb: float = 6.0
    offload_chunk_mb: int = 512
    max_resident_weights_gb: float = 0.0
    block_check_interval: int = 1
    residency_fill_enabled: bool = True
    residency_target_free_gb: float = 0.50
    residency_warmup_blocks: int = 2
    residency_refill_interval: int = 1
    allocator_memory_fraction: float = 0.94
    text_activation_free_fraction: float = 0.67
    cache_trim_slack_gb: float = 2.0
    disable_comfy_pinned_offload: bool = True
    managed_stages: tuple[str, ...] | None = None


class VRAMManager:
    """MiniMax/Comfy VRAM residency controller.

    V9 keeps V7's CUDA allocator ceiling and cache-reuse behavior, but stops trimming the CUDA cache after
    every DiT block. The V6 1080p/243f test held the peak reservation to 21.16 GiB,
    but released roughly 14.86 GiB after nearly every block, forcing CUDA to rebuild
    the same workspace over and over and making a denoise step slightly slower than
    V5. V7 lets reusable workspace stay cached during diffusion and only performs
    forced cleanup at stage/offload boundaries. The 22.56 GiB allocator ceiling
    remains the hard guard against the old 25+ GiB reservation/shared-memory spill.

    V5 keeps the static-partial/DynamicVRAM choices, but changes how impossible
    MiniMax sampling estimates are handled. V4 clamped a 30-60+ GiB sampler
    request to zero, which made Comfy fully resident-load the 11.7 GiB model.
    At 1080p the first DiT activation then pushed the CUDA working set above the
    24 GiB card and into Windows shared GPU memory. V5 converts impossible hints
    into a realistic *minimum free activation budget* instead. It also expects
    sampling to run under torch.no_grad(), not torch.inference_mode(), so Comfy
    can legally move/restore partially resident weights during the forward pass.
    """

    def __init__(self, comfy, config: VRAMManagerConfig, verbose: bool = False):
        self.comfy = comfy
        self.mm = comfy.model_management
        self.cfg = config
        self.verbose = bool(verbose)
        self.stage = "text"
        self._orig_load_models_gpu = None
        self._seen = set()
        self._hook_handles = []
        self._block_calls = 0
        self._diffusion_post_calls = 0
        self._events = 0
        self._fill_events = 0
        self._fill_bytes = 0
        self._sampling_target_free_bytes = 0
        self._orig_memory_fraction = None
        self._cache_trim_events = 0
        self._orig_max_pinned_memory = None
        self._orig_extra_reserved_vram = getattr(self.mm, "EXTRA_RESERVED_VRAM", 0)
        self._allocator_guard_active = False
        self._pinned_guard_active = False
        self._text_conditioning_target_bytes = 0

    @staticmethod
    def _gb(n: int) -> str:
        return f"{float(n) / _GIB:.2f} GiB"

    def _log(self, message: str, force: bool = False):
        if force or self.verbose:
            print(f"[VRAM-MGR] {message}", flush=True)

    def runtime_floor_bytes(self) -> int:
        return max(0, int(float(self.cfg.runtime_free_gb) * _GIB))

    def residency_target_free_bytes(self) -> int:
        return max(self.runtime_floor_bytes(), int(float(self.cfg.residency_target_free_gb) * _GIB))

    def load_headroom_bytes(self) -> int:
        if self.stage == "diffusion":
            gb = self.cfg.diffusion_load_headroom_gb
        elif self.stage == "reference":
            gb = self.cfg.vae_load_headroom_gb
        else:
            gb = self.cfg.text_load_headroom_gb
        return max(self.runtime_floor_bytes(), int(float(gb) * _GIB))

    @staticmethod
    def _normalize_stage(stage: str) -> str:
        value = str(stage).lower().strip()
        if value.startswith("diff"):
            return "diffusion"
        if value.startswith("ref") or value.startswith("vae") or value.startswith("key"):
            return "reference"
        return "text"

    def is_stage_managed(self, stage: str | None = None) -> bool:
        stages = self.cfg.managed_stages
        if not stages:
            return True
        value = self._normalize_stage(stage if stage is not None else self.stage)
        return value in {self._normalize_stage(x) for x in stages}

    def _set_comfy_reserve(self, nbytes: int):
        try:
            self.mm.EXTRA_RESERVED_VRAM = int(max(0, nbytes))
        except Exception:
            pass

    def set_stage(self, stage: str):
        self.stage = self._normalize_stage(stage)
        managed = self.is_stage_managed(self.stage)
        if managed:
            self._set_comfy_reserve(self.load_headroom_bytes())
        else:
            self._set_comfy_reserve(int(self._orig_extra_reserved_vram or 0))
        self._sync_stage_guards(managed)
        self._log(
            f"stage={self.stage} | mode={'managed' if managed else 'native'} | "
            f"load headroom={self._gb(self.load_headroom_bytes()) if managed else 'original/default'} | "
            f"runtime floor={self._gb(self.runtime_floor_bytes())}",
            force=True,
        )

    def _install_allocator_guard(self):
        if self._allocator_guard_active or not torch.cuda.is_available():
            return
        frac = float(getattr(self.cfg, "allocator_memory_fraction", 0.94) or 0.94)
        frac = min(0.99, max(0.50, frac))
        try:
            if hasattr(torch.cuda, "get_per_process_memory_fraction"):
                self._orig_memory_fraction = float(torch.cuda.get_per_process_memory_fraction())
        except Exception:
            self._orig_memory_fraction = None
        try:
            torch.cuda.set_per_process_memory_fraction(frac)
            self._allocator_guard_active = True
            _, total = self._cuda_free()
            limit = int(float(total) * frac) if total is not None else 0
            self._log(
                f"V9 CUDA allocator guard active | fraction={frac:.3f} | "
                f"allocator ceiling={self._gb(limit) if limit else 'n/a'}",
                force=True,
            )
        except Exception as exc:
            self._log(f"V9 CUDA allocator guard unavailable: {exc}", force=True)

    def trim_cuda_cache(self, reason: str = "runtime", force: bool = False) -> int:
        if not self.is_stage_managed() or not torch.cuda.is_available():
            return 0
        try:
            allocated = int(torch.cuda.memory_allocated())
            reserved = int(torch.cuda.memory_reserved())
            free, total = self._cuda_free()
        except Exception:
            return 0
        slack = max(0, reserved - allocated)
        threshold = int(max(0.25, float(getattr(self.cfg, "cache_trim_slack_gb", 2.0))) * _GIB)
        near_limit = bool(total and reserved >= int(total * 0.88))
        starved = bool(free is not None and free < max(self.runtime_floor_bytes(), int(1.0 * _GIB)))
        if not force and slack < threshold and not near_limit and not starved:
            return 0
        before = reserved
        try:
            # Only releases completely unused cached blocks; live tensors are untouched.
            torch.cuda.empty_cache()
        except Exception as exc:
            self._log(f"cache trim skipped ({reason}): {exc}", force=True)
            return 0
        try:
            after = int(torch.cuda.memory_reserved())
            free2, _ = self._cuda_free()
        except Exception:
            after = before
            free2 = None
        released = max(0, before - after)
        if released > 0:
            self._cache_trim_events += 1
            self._log(
                f"V9 cache trim ({reason}) | released={self._gb(released)} | "
                f"allocated={self._gb(allocated)} reserved={self._gb(before)}->{self._gb(after)} | "
                f"CUDA free={self._gb(free2) if free2 is not None else 'n/a'}",
                force=True,
            )
        return released

    def _install_pinned_offload_guard(self):
        if self._pinned_guard_active or not bool(getattr(self.cfg, "disable_comfy_pinned_offload", True)):
            return
        try:
            self._orig_max_pinned_memory = getattr(self.mm, "MAX_PINNED_MEMORY", None)
            total_pinned = int(getattr(self.mm, "TOTAL_PINNED_MEMORY", 0) or 0)
            self.mm.MAX_PINNED_MEMORY = 0
            self._pinned_guard_active = True
            self._log(
                "V9 Comfy host pinning disabled for MiniMax worker | "
                f"previous pin budget={self._gb(int(self._orig_max_pinned_memory)) if isinstance(self._orig_max_pinned_memory, (int, float)) and self._orig_max_pinned_memory > 0 else self._orig_max_pinned_memory} | "
                f"already registered={self._gb(total_pinned)} | offloaded weights stay pageable CPU RAM",
                force=True,
            )
        except Exception as exc:
            self._log(f"V9 Comfy host-pinning guard unavailable: {exc}", force=True)

    def _restore_allocator_guard(self):
        if not self._allocator_guard_active or not torch.cuda.is_available():
            return
        try:
            if self._orig_memory_fraction is not None:
                torch.cuda.set_per_process_memory_fraction(self._orig_memory_fraction)
        except Exception:
            pass
        self._allocator_guard_active = False

    def _restore_pinned_offload_guard(self):
        if not self._pinned_guard_active:
            return
        try:
            self.mm.MAX_PINNED_MEMORY = self._orig_max_pinned_memory
        except Exception:
            pass
        self._pinned_guard_active = False

    def _sync_stage_guards(self, managed: bool):
        # V11: Qwen/Ref2VA conditioning can create its largest transient before a
        # block-boundary hook has any chance to react.  The old V10 behaviour only
        # installed the hard CUDA ceiling for diffusion, which allowed managed text
        # runs to oversubscribe a 24 GiB card before the guard appeared in the log.
        # Install the ceiling *before loading* every managed compute-heavy stage.
        # Reference VAE work keeps the normal allocator because its tiled/serial
        # encoding path is materially smaller and benefits from allocator freedom.
        if managed and self.stage in ("text", "diffusion"):
            self._install_allocator_guard()
        else:
            self._restore_allocator_guard()
        if managed:
            self._install_pinned_offload_guard()
        else:
            self._restore_pinned_offload_guard()

    def text_activation_target_bytes(self) -> int:
        """Free-VRAM target used immediately before Qwen conditioning.

        Ref2VA jobs with several visual/audio references can add a ~14 GiB-class
        transient on top of resident Qwen weights.  Waiting for a Qwen pre-hook is
        too late for the first large allocation, so managed text stages proactively
        reduce resident weights before encode_from_tokens_scheduled().
        """
        floor = max(self.runtime_floor_bytes(), self.load_headroom_bytes())
        free, total = self._cuda_free()
        if total is None:
            return floor
        frac = float(getattr(self.cfg, "text_activation_free_fraction", 0.67) or 0.67)
        frac = min(0.85, max(0.40, frac))
        return max(floor, int(float(total) * frac))

    def begin_text_conditioning_admission(self) -> int:
        """Reserve Qwen activation runway before its first CUDA residency load.

        comfy.sd.load_clip() only creates the CLIP/Qwen object; the GPU residency
        load happens lazily from CLIP.load_model(tokens) inside encode.  V11.1 ran
        its preflight before that lazy load, so it always saw an almost-empty card.
        V11.2 arms Comfy's EXTRA_RESERVED_VRAM with the activation target first so
        the lazy loader chooses partial Qwen residency from the outset.
        """
        if not self.is_stage_managed("text") or self.stage != "text":
            self._text_conditioning_target_bytes = 0
            return 0
        self._sync_stage_guards(True)
        target = self.text_activation_target_bytes()
        self._text_conditioning_target_bytes = int(target)
        self._set_comfy_reserve(target)
        free_before, total = self._cuda_free()
        self._log(
            f"V11.2 Qwen admission armed | CUDA free={self._gb(free_before) if free_before is not None else 'n/a'} | "
            f"reserved activation runway={self._gb(target)} | card={self._gb(total) if total is not None else 'n/a'}",
            force=True,
        )
        return target

    def prepare_text_conditioning(self, reason: str = "pre-Qwen conditioning") -> int:
        """Verify/trim Qwen residency after the constrained lazy GPU load."""
        if not self.is_stage_managed("text") or self.stage != "text":
            return 0
        self._sync_stage_guards(True)
        target = int(self._text_conditioning_target_bytes or self.text_activation_target_bytes())
        self._text_conditioning_target_bytes = target
        self._set_comfy_reserve(target)
        free_before, total = self._cuda_free()
        self._log(
            f"V11.2 Qwen preflight after load | CUDA free={self._gb(free_before) if free_before is not None else 'n/a'} | "
            f"target free={self._gb(target)} | card={self._gb(total) if total is not None else 'n/a'}",
            force=True,
        )
        freed = self.enforce_loaded(reason=reason, target_free=target)
        self.trim_cuda_cache(reason=reason, force=True)
        free_after, _ = self._cuda_free()
        self._log(
            f"V11.2 Qwen preflight complete | model weights offloaded={self._gb(freed)} | "
            f"CUDA free={self._gb(free_after) if free_after is not None else 'n/a'} | target={self._gb(target)}",
            force=True,
        )
        return freed

    def end_text_conditioning_admission(self):
        """Release the temporary Qwen activation reserve after conditioning."""
        if self._text_conditioning_target_bytes:
            self._log(
                f"V11.2 Qwen admission released | activation runway was={self._gb(self._text_conditioning_target_bytes)}",
                force=True,
            )
        self._text_conditioning_target_bytes = 0
        if self.is_stage_managed(self.stage):
            self._set_comfy_reserve(self.runtime_floor_bytes())
        else:
            self._set_comfy_reserve(int(self._orig_extra_reserved_vram or 0))

    def install(self):
        if self._orig_load_models_gpu is not None:
            return
        self._orig_load_models_gpu = self.mm.load_models_gpu
        manager = self

        def managed_load_models_gpu(models, *args, **kwargs):
            if not manager.is_stage_managed():
                return manager._orig_load_models_gpu(models, *args, **kwargs)
            first_load = any(id(m) not in manager._seen for m in models)
            reserve = manager.load_headroom_bytes() if first_load else manager.runtime_floor_bytes()
            # V11.2: while Qwen conditioning admission is armed, keep the large
            # activation runway visible to *every* Comfy load call.  encode() calls
            # CLIP.load_model(tokens) again, so dropping back to the 0.5 GiB runtime
            # reserve here would simply pull the offloaded Qwen weights back in.
            if manager.stage == "text" and manager._text_conditioning_target_bytes > 0:
                reserve = max(reserve, int(manager._text_conditioning_target_bytes))
            manager._set_comfy_reserve(reserve)

            # V5: MiniMax can report a sampling requirement larger than the GPU.
            # Passing that literally is impossible, but V4's zero-clamp was also
            # wrong: zero tells Comfy it may fully resident-load the model. At
            # 1920x1088 the first DiT pass then peaked above 32 GiB CUDA allocation.
            # For impossible hints, reserve most of the card for activations and
            # deliberately force a partial model load. EXTRA_RESERVED_VRAM is added
            # by Comfy on top of these values, so subtract our explicit reserve from
            # the target before forwarding the effective memory hint.
            call_args = list(args)
            orig_required = kwargs.get('memory_required', call_args[0] if call_args else 0)
            orig_minimum = kwargs.get('minimum_memory_required', None)
            effective_required = orig_required
            effective_minimum = orig_minimum
            clamped = False
            if manager.stage == "diffusion" and torch.cuda.is_available():
                try:
                    _free, total = manager._cuda_free()
                    req = float(orig_required or 0)
                    min_req = float(orig_minimum or 0) if orig_minimum is not None else 0.0
                    # Anything larger than the physical card is not realizable.
                    # A 72%%-of-physical activation target plus Comfy's explicit
                    # reserve leaves only a few GiB of a 24 GiB card for resident
                    # weights, which matches the observed ~17 GiB transient DiT
                    # workspace much better than V4's full 11.68 GiB residency.
                    if total is not None and (req > float(total) or min_req > float(total) * 0.60):
                        explicit_reserve = manager.load_headroom_bytes()
                        desired_total_free = max(
                            explicit_reserve,
                            int(float(total) * 0.72),
                        )
                        forwarded = max(0, desired_total_free - explicit_reserve)
                        effective_required = forwarded
                        effective_minimum = forwarded
                        manager._sampling_target_free_bytes = desired_total_free
                        if 'memory_required' in kwargs:
                            kwargs['memory_required'] = forwarded
                        elif call_args:
                            call_args[0] = forwarded
                        else:
                            kwargs['memory_required'] = forwarded
                        if 'minimum_memory_required' in kwargs or orig_minimum is not None:
                            kwargs['minimum_memory_required'] = forwarded
                        clamped = True
                except Exception as exc:
                    manager._log(f"sampling request guard skipped: {exc}", force=True)

            if clamped:
                manager._log(
                    "V9 sampling admission guard | "
                    f"requested={manager._gb(int(float(orig_required or 0)))} "
                    f"minimum={manager._gb(int(float(orig_minimum or 0))) if orig_minimum is not None else 'auto'} -> "
                    f"effective hint={manager._gb(int(effective_required or 0))} | "
                    f"total activation target={manager._gb(manager._sampling_target_free_bytes)} | "
                    f"explicit diffusion reserve={manager._gb(reserve)} | force_full_load kept false",
                    force=True,
                )
            elif manager.verbose:
                manager._log(
                    "Comfy load request | "
                    f"models={len(models)} memory_required={orig_required} "
                    f"minimum_memory_required={orig_minimum if orig_minimum is not None else 'auto'} "
                    f"force_full_load={kwargs.get('force_full_load', False)}",
                    force=True,
                )
            # Comfy's CLIP constructor explicitly requests force_full_load=True.
            # On lower-VRAM GPUs that forces the entire ~17 GiB Qwen onto CUDA and
            # defeats partial residency. Override it only when the text stage was
            # independently selected for management.
            if manager.stage in ("text", "diffusion") and kwargs.get('force_full_load', False):
                kwargs['force_full_load'] = False
                if manager.stage == "text":
                    manager._log(
                        "Qwen admission guard: force_full_load=True overridden to False; "
                        "partial CUDA residency is allowed",
                        force=True,
                    )
                else:
                    manager._log("V9 sampling admission guard: force_full_load=True overridden to False", force=True)

            out = manager._orig_load_models_gpu(models, *call_args, **kwargs)
            if first_load:
                for m in models:
                    manager._seen.add(id(m))
                manager.log_residency("after Comfy initial load", force=True)
                # V2 does NOT force-fill here: at this point the first-step activation
                # footprint is still unknown. Residency fill begins only after warm-up.
                if manager.stage == "text" and manager._text_conditioning_target_bytes > 0:
                    manager._set_comfy_reserve(int(manager._text_conditioning_target_bytes))
                else:
                    manager._set_comfy_reserve(manager.runtime_floor_bytes())
            else:
                manager.enforce_loaded(reason=f"{manager.stage} reload")
            return out

        self.mm.load_models_gpu = managed_load_models_gpu
        self._log(
            f"enabled {VRAM_MANAGER_VERSION} stage-aware MiniMax residency control | "
            f"offload chunk={int(self.cfg.offload_chunk_mb)} MiB | "
            f"max resident weights={'auto' if self.cfg.max_resident_weights_gb <= 0 else f'{self.cfg.max_resident_weights_gb:.2f} GiB'} | "
            f"check every {max(1, int(self.cfg.block_check_interval))} block(s) | "
            f"residency fill={'on' if self.cfg.residency_fill_enabled else 'off'} "
            f"target free={self._gb(self.residency_target_free_bytes())} "
            f"warm-up={max(0, int(self.cfg.residency_warmup_blocks))} block(s) | "
            f"managed stages={','.join(self.cfg.managed_stages) if self.cfg.managed_stages else 'all'}",
            force=True,
        )

    def restore(self):
        for h in self._hook_handles:
            try:
                h.remove()
            except Exception:
                pass
        self._hook_handles.clear()
        if self._orig_load_models_gpu is not None:
            try:
                self.mm.load_models_gpu = self._orig_load_models_gpu
            except Exception:
                pass
            self._orig_load_models_gpu = None
        self._restore_pinned_offload_guard()
        self._restore_allocator_guard()
        self._set_comfy_reserve(int(self._orig_extra_reserved_vram or 0))
        self._log(
            f"disabled after {self._events} offload event(s), {self._fill_events} residency fill event(s), "
            f"{self._cache_trim_events} cache trim event(s), filled={self._gb(self._fill_bytes)}",
            force=True,
        )

    def _cuda_free(self):
        if not torch.cuda.is_available():
            return None, None
        try:
            return torch.cuda.mem_get_info()
        except Exception:
            return None, None

    def _round_chunk_up(self, nbytes: int) -> int:
        chunk = max(64, int(self.cfg.offload_chunk_mb)) * _MIB
        return int(math.ceil(max(0, nbytes) / chunk) * chunk) if nbytes > 0 else 0

    def _round_chunk_down(self, nbytes: int) -> int:
        chunk = max(64, int(self.cfg.offload_chunk_mb)) * _MIB
        return int(max(0, nbytes) // chunk * chunk)

    def _loaded_entries(self):
        try:
            return list(self.mm.current_loaded_models)
        except Exception:
            return []

    def residency_stats(self):
        rows=[]
        for entry in self._loaded_entries():
            patcher=getattr(entry, 'model', None)
            if patcher is None:
                continue
            try:
                size=int(patcher.model_size())
                loaded=int(patcher.loaded_size())
                off=max(0,size-loaded)
                cls=getattr(getattr(patcher,'model',None),'__class__',type(None)).__name__
                pcls=patcher.__class__.__name__
                dynamic=bool(getattr(patcher, "is_dynamic", lambda: False)())
                rows.append((cls,pcls,dynamic,size,loaded,off,entry,patcher))
            except Exception:
                continue
        return rows

    def log_residency(self, reason: str, force: bool = False):
        rows=self.residency_stats()
        if not rows:
            self._log(f"{reason}: no Comfy loaded-model residency entries", force=force)
            return
        free,total=self._cuda_free()
        for cls,pcls,dynamic,size,loaded,off,entry,patcher in rows:
            self._log(
                f"{reason}: {cls} patcher={pcls} dynamic={dynamic} total={self._gb(size)} resident={self._gb(loaded)} offloaded={self._gb(off)} | "
                f"CUDA free={self._gb(free) if free is not None else 'n/a'}",
                force=force,
            )

    def enforce_patcher(self, patcher, target_free: int | None = None, reason: str = "runtime") -> int:
        if patcher is None or not torch.cuda.is_available():
            return 0
        try:
            load_device = getattr(patcher, "load_device", None)
            if load_device is not None and getattr(load_device, "type", str(load_device).split(':')[0]) != "cuda":
                return 0
            loaded = int(patcher.loaded_size())
        except Exception:
            return 0
        if loaded <= 0:
            return 0

        need = 0
        if float(self.cfg.max_resident_weights_gb) > 0:
            cap = int(float(self.cfg.max_resident_weights_gb) * _GIB)
            need = max(need, loaded - cap)

        free, _ = self._cuda_free()
        if target_free is None:
            target_free = self.runtime_floor_bytes()
        if free is not None:
            need = max(need, int(target_free) - int(free))

        need = self._round_chunk_up(need)
        if need <= 0:
            return 0
        need = min(need, loaded)
        try:
            freed = int(patcher.partially_unload(patcher.offload_device, need) or 0)
        except Exception as exc:
            self._log(f"offload skipped ({reason}): {exc}", force=True)
            return 0
        if freed > 0:
            self._events += 1
            self.trim_cuda_cache(reason=f"after offload: {reason}", force=True)
            free2, _ = self._cuda_free()
            self._log(
                f"{reason}: freed {self._gb(freed)} model weights | loaded now={self._gb(max(0, loaded-freed))} | "
                f"CUDA free={self._gb(free2) if free2 is not None else 'n/a'}",
                force=True,
            )
        return freed

    def enforce_loaded(self, reason: str = "runtime", target_free: int | None = None) -> int:
        total_freed = 0
        for entry in self._loaded_entries():
            patcher = getattr(entry, "model", None)
            total_freed += self.enforce_patcher(patcher, target_free=target_free, reason=reason)
            free, _ = self._cuda_free()
            floor = self.runtime_floor_bytes() if target_free is None else target_free
            if free is not None and free >= floor:
                break
        return total_freed

    def fill_residency(self, reason: str = "diffusion residency fill") -> int:
        if not self.cfg.residency_fill_enabled or self.stage != "diffusion" or not torch.cuda.is_available():
            return 0
        free,_=self._cuda_free()
        if free is None:
            return 0
        target=max(self.residency_target_free_bytes(), int(self._sampling_target_free_bytes or 0))
        available=self._round_chunk_down(int(free)-int(target))
        if available <= 0:
            return 0

        rows=self.residency_stats()
        if not rows:
            return 0
        # Current/newest CUDA model first. Only ask for bytes that are actually offloaded.
        total_off=sum(off for _,_,_,_,_,off,_,_ in rows)
        if total_off <= 0:
            return 0
        request=min(available,total_off)

        # Respect optional absolute resident-weight cap across current models.
        if float(self.cfg.max_resident_weights_gb) > 0:
            cap=int(float(self.cfg.max_resident_weights_gb)*_GIB)
            resident=sum(loaded for _,_,_,_,loaded,_,_,_ in rows)
            request=min(request,max(0,cap-resident))
        request=self._round_chunk_down(request)
        if request <= 0:
            return 0

        before=sum(loaded for _,_,_,_,loaded,_,_,_ in rows)
        loaded_more=0
        try:
            # Use Comfy's own residency API so DynamicVRAM bookkeeping stays valid.
            models=[entry for _,_,_,_,_,_,entry,_ in rows]
            result=self.mm.use_more_memory(request, models, torch.device('cuda'))
            # use_more_memory has no useful return contract in every Comfy revision;
            # determine the real change from patcher.loaded_size().
            rows2=self.residency_stats()
            after=sum(loaded for _,_,_,_,loaded,_,_,_ in rows2)
            loaded_more=max(0,after-before)
        except Exception as exc:
            self._log(f"residency fill skipped ({reason}): {exc}", force=True)
            return 0
        if loaded_more > 0:
            self._fill_events += 1
            self._fill_bytes += loaded_more
            free2,_=self._cuda_free()
            self._log(
                f"{reason}: pulled {self._gb(loaded_more)} model weights back to CUDA | "
                f"CUDA free {self._gb(free)} -> {self._gb(free2) if free2 is not None else 'n/a'} | "
                f"target free={self._gb(target)}",
                force=True,
            )
            self.log_residency("residency after fill", force=self.verbose)
        return loaded_more

    def maybe_check_blocks(self, reason: str = "block"):
        if not self.is_stage_managed():
            return
        self._block_calls += 1
        interval = max(1, int(self.cfg.block_check_interval))
        if self._block_calls % interval == 0:
            target = self.runtime_floor_bytes()
            if self.stage == "diffusion" and self._sampling_target_free_bytes > 0:
                target = max(target, int(self._sampling_target_free_bytes))
            self.enforce_loaded(reason=reason, target_free=target)
            # V7: do not empty the CUDA cache at normal DiT boundaries. The
            # allocator ceiling is the spill guard; cached workspaces are kept
            # so the next block can reuse them instead of reallocating ~15 GiB.

    def _after_diffusion_block(self, name: str):
        self._diffusion_post_calls += 1
        warmup=max(0,int(self.cfg.residency_warmup_blocks))
        interval=max(1,int(self.cfg.residency_refill_interval))
        # V7 intentionally keeps the DiT workspace cache between blocks. V6
        # emptied ~14.86 GiB here every block and paid the allocation cost again.
        # The per-process allocator ceiling remains responsible for preventing
        # the cache from growing into the Windows shared-memory danger zone.
        if self._diffusion_post_calls <= warmup:
            if self._diffusion_post_calls == warmup:
                self.log_residency(f"warm-up complete at {name}", force=True)
            return
        if (self._diffusion_post_calls - warmup - 1) % interval == 0:
            self.fill_residency(reason=f"post-block {name}")

    def install_sampling_hooks(self, model):
        if not self.is_stage_managed("diffusion"):
            self._log("diffusion stage is native; sampling residency hooks not installed", force=True)
            return lambda: None
        try:
            modules = list(model.model.named_modules())
        except Exception:
            return lambda: None
        targets = [(name, m) for name, m in modules if m.__class__.__name__ in ("DiTBlock", "RefinerBlock")]
        for name, module in targets:
            def pre(mod, args, _name=name):
                self.maybe_check_blocks(reason=f"DiT boundary {_name}")
            self._hook_handles.append(module.register_forward_pre_hook(pre))
            # Only normal diffusion blocks establish the large video-token activation footprint.
            if module.__class__.__name__ == "DiTBlock":
                def post(mod, args, output, _name=name):
                    self._after_diffusion_block(_name)
                self._hook_handles.append(module.register_forward_hook(post))
        self._log(f"sampling guard attached to {len(targets)} MiniMax diffusion/refiner blocks", force=True)
        return lambda: None
