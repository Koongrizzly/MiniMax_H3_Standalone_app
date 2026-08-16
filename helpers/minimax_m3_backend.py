from __future__ import annotations

import gc
import inspect
import json
import os
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import numpy as np
import psutil
import soundfile as sf
import torch


@dataclass
class GenerationRequest:
    prompt: str
    lyrics: str
    duration: float
    seed: int
    output_path: Path
    vram_mode: str = "full"
    ar_top_k: int = 0
    flow_steps: int = 0
    flow_guidance: float = 0.0
    ar_weight_mode: str = "bf16"




class MiniMaxM3Engine:
    def __init__(self, model_dir: Path, log: Callable[[str], None] = print):
        self.model_dir = Path(model_dir)
        self.log = log
        self.pipe = None
        self.loaded_mode = None
        self.loaded_ar_weight_mode = None

    def unload(self):
        self.pipe = None
        self.loaded_mode = None
        self.loaded_ar_weight_mode = None
        gc.collect()
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
        self.log("Pipeline unloaded and CUDA cache released.")

    def _memory_text(self) -> str:
        vm = psutil.virtual_memory()
        ram_used = (vm.total - vm.available) / (1024 ** 3)
        ram_total = vm.total / (1024 ** 3)
        text = f"RAM {ram_used:.1f}/{ram_total:.1f} GiB"
        if torch.cuda.is_available():
            free, total = torch.cuda.mem_get_info()
            used = total - free
            text += f" | VRAM {used / (1024 ** 3):.1f}/{total / (1024 ** 3):.1f} GiB"
        return text

    def _run_with_heartbeat(self, label: str, fn, interval: float = 10.0):
        stop = threading.Event()
        started = time.monotonic()

        def heartbeat():
            while not stop.wait(interval):
                elapsed = time.monotonic() - started
                self.log(f"{label}... {elapsed:.0f}s | {self._memory_text()}")

        thread = threading.Thread(target=heartbeat, name="MiniMaxM3Heartbeat", daemon=True)
        thread.start()
        try:
            return fn()
        finally:
            stop.set()
            thread.join(timeout=1.0)

    def _prepare_local_component_index(self):
        """Rewrite only the component source paths so ModularPipeline stays local.

        MiniMax's modular_model_index.json points every component back to the
        Hugging Face repo ID. That is correct for online from_pretrained use,
        but it causes load_components() to download the weights again even
        after this standalone app has already downloaded them into model_dir.
        """
        index_path = self.model_dir / "modular_model_index.json"
        if not index_path.exists():
            raise FileNotFoundError(f"Missing model index: {index_path}")

        data = json.loads(index_path.read_text(encoding="utf-8"))
        local_root = str(self.model_dir.resolve())
        changed = False
        for name, value in data.items():
            if not isinstance(value, list) or len(value) < 3 or not isinstance(value[2], dict):
                continue
            source = value[2]
            if "pretrained_model_name_or_path" in source and source["pretrained_model_name_or_path"] != local_root:
                source["pretrained_model_name_or_path"] = local_root
                changed = True

        if changed:
            index_path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
            self.log("Prepared local-only component index (no model downloads during generation).")
        else:
            self.log("Local-only component index already prepared.")

    def _prepare_tokenizer_regex_fix(self):
        """Persist the Transformers Mistral-regex compatibility flag locally.

        Transformers reads tokenizer_config.json during from_pretrained().  Music 3's
        tokenizer triggers the known regex warning when loaded from the local model
        root unless fix_mistral_regex=True is present.  Store the flag in the local
        tokenizer config so Diffusers' internal component loader receives it without
        monkeypatching Transformers globally.
        """
        candidates = [
            self.model_dir / "tokenizer" / "tokenizer_config.json",
            self.model_dir / "tokenizer_config.json",
        ]
        found = False
        for config_path in candidates:
            if not config_path.exists():
                continue
            found = True
            try:
                data = json.loads(config_path.read_text(encoding="utf-8"))
            except Exception as exc:
                raise RuntimeError(f"Could not read tokenizer config {config_path}: {exc}") from exc
            if data.get("fix_mistral_regex") is True:
                self.log(f"Tokenizer regex fix already enabled: {config_path}")
                continue
            data["fix_mistral_regex"] = True
            config_path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
            self.log(f"Enabled Transformers tokenizer regex fix (fix_mistral_regex=True): {config_path}")
        if not found:
            self.log("WARNING: tokenizer_config.json not found; could not persist fix_mistral_regex=True.")

    def _restore_native_duration_behavior(self):
        """Remove the old app-added EOS guard and restore MiniMax Music 3 native stopping."""
        os.environ.pop("MINIMAX_M3_MIN_AUDIO_FRAMES", None)
        env_root = Path(sys.executable).resolve().parent
        source = env_root / "Lib" / "site-packages" / "diffusers" / "modular_pipelines" / "minimax_music3" / "encoders.py"
        if not source.exists():
            self.log(f"WARNING: Music 3 encoder source not found while checking native duration behavior: {source}")
            return
        text = source.read_text(encoding="utf-8")
        native = 'if int(sampled.item()) == _AUDIO_END_TOKEN_ID:'
        patched = (
            'if int(sampled.item()) == _AUDIO_END_TOKEN_ID and frame_index >= '
            'int(__import__("os").environ.get("MINIMAX_M3_MIN_AUDIO_FRAMES", "0")):'
        )
        if patched in text:
            source.write_text(text.replace(patched, native, 1), encoding="utf-8")
            self.log("Restored native MiniMax Music 3 EOS/duration behavior; app duration guard removed.")
        elif native in text:
            self.log("Native MiniMax Music 3 EOS/duration behavior active.")
        else:
            self.log("WARNING: Could not verify the native MiniMax Music 3 EOS line; source was left unchanged.")

    def _install_music3_top_k_override(self, requested_top_k: int):
        """Temporarily override MiniMax Music 3's native AR top-k sampler.

        A value <= 0 preserves the pinned Diffusers default exactly.  The wrapper
        intercepts the existing `_sample_top_k` helper, so this remains the same
        MiniMax/Diffusers autoregressive path rather than introducing another
        sampler implementation.
        """
        requested_top_k = int(requested_top_k or 0)
        if requested_top_k <= 0:
            self.log("AR Top-K: Auto (native Diffusers value)")
            return lambda: None
        try:
            import diffusers.modular_pipelines.minimax_music3.encoders as m3enc

            original = getattr(m3enc, "_sample_top_k", None)
            if original is None:
                self.log("WARNING: MiniMax Music 3 native _sample_top_k helper was not found; override ignored.")
                return lambda: None
            signature = inspect.signature(original)
            first_native = {"value": None}

            def wrapped(*args, **kwargs):
                bound = signature.bind_partial(*args, **kwargs)
                native_value = bound.arguments.get("top_k")
                if first_native["value"] is None:
                    try:
                        first_native["value"] = int(native_value)
                    except Exception:
                        first_native["value"] = native_value
                    self.log(
                        f"AR Top-K override active: requested={requested_top_k} | "
                        f"native first-call value={first_native['value']}"
                    )
                bound.arguments["top_k"] = requested_top_k
                return original(*bound.args, **bound.kwargs)

            m3enc._sample_top_k = wrapped

            def restore():
                if getattr(m3enc, "_sample_top_k", None) is wrapped:
                    m3enc._sample_top_k = original

            return restore
        except Exception as exc:
            self.log(f"WARNING: Could not install native AR Top-K override: {exc!r}")
            return lambda: None

    def _install_music3_flow_steps_override(self, requested_steps: int):
        """Temporarily override the native FlowMatch scheduler step count.

        Zero preserves the value requested by MiniMax's pinned Diffusers block.
        This only intercepts the existing scheduler.set_timesteps call; no
        alternate scheduler or sampler is introduced.
        """
        requested_steps = int(requested_steps or 0)
        if requested_steps <= 0:
            self.log("Flow steps: Auto (native MiniMax/Diffusers value)")
            return lambda: None
        scheduler = getattr(self.pipe, "scheduler", None)
        original = getattr(scheduler, "set_timesteps", None) if scheduler is not None else None
        if original is None:
            self.log("WARNING: Native FlowMatch scheduler.set_timesteps was not found; flow-step override ignored.")
            return lambda: None
        try:
            signature = inspect.signature(original)
            first_native = {"done": False}

            def wrapped(*args, **kwargs):
                bound = signature.bind_partial(*args, **kwargs)
                native_value = bound.arguments.get("num_inference_steps")
                if not first_native["done"]:
                    first_native["done"] = True
                    self.log(
                        f"Flow-step override active: requested={requested_steps} | "
                        f"native first-call value={native_value!r} | scheduler={type(scheduler).__name__}"
                    )
                if "num_inference_steps" in signature.parameters:
                    bound.arguments["num_inference_steps"] = requested_steps
                return original(*bound.args, **bound.kwargs)

            scheduler.set_timesteps = wrapped

            def restore():
                try:
                    scheduler.set_timesteps = original
                except Exception:
                    pass
            return restore
        except Exception as exc:
            self.log(f"WARNING: Could not install native flow-step override: {exc!r}")
            return lambda: None

    def _install_music3_flow_guidance_override(self, requested_scale: float):
        """Temporarily replace the existing native CFG guider with the same
        guider type/config but a different guidance_scale.

        Zero preserves the exact guider shipped by the pinned Music 3 modular
        pipeline. Uses Modular Diffusers' own component-spec/update API.
        """
        requested_scale = float(requested_scale or 0.0)
        if requested_scale <= 0.0:
            guider = getattr(self.pipe, "guider", None)
            cfg = getattr(guider, "config", None)
            native = getattr(cfg, "guidance_scale", None) if cfg is not None else None
            if native is None and isinstance(cfg, dict):
                native = cfg.get("guidance_scale")
            self.log(f"Flow CFG: Auto (native value={native!r})")
            return lambda: None
        original = getattr(self.pipe, "guider", None)
        if original is None:
            self.log("WARNING: Native MiniMax CFG guider was not found; flow-guidance override ignored.")
            return lambda: None
        try:
            spec = self.pipe.get_component_spec("guider")
            cfg = getattr(original, "config", None)
            native = getattr(cfg, "guidance_scale", None) if cfg is not None else None
            if native is None and isinstance(cfg, dict):
                native = cfg.get("guidance_scale")
            new_guider = spec.create(guidance_scale=requested_scale)
            self.pipe.update_components(guider=new_guider)
            self.log(
                f"Flow CFG override active: requested={requested_scale:g} | native={native!r} | "
                f"guider={type(original).__name__}"
            )

            def restore():
                try:
                    self.pipe.update_components(guider=original)
                except Exception as exc:
                    self.log(f"WARNING: Could not restore native CFG guider: {exc!r}")
            return restore
        except Exception as exc:
            self.log(f"WARNING: Could not install native Flow CFG override: {exc!r}")
            return lambda: None


    @staticmethod
    def _count_linear_weights(model):
        """Return (linear_count, logical_bytes) for nn.Linear weights."""
        count = 0
        total = 0
        for module in model.modules():
            if isinstance(module, torch.nn.Linear) and getattr(module, "weight", None) is not None:
                count += 1
                w = module.weight
                try:
                    total += int(w.numel()) * int(w.element_size())
                except Exception:
                    pass
        return count, total

    def _apply_ar_int8_weight_only(self, pipe):
        """Quantize only the two autoregressive Music 3 models.

        The flow transformer, vocoder/VAE path, tokenizer and condition encoder
        remain in their native BF16/FP32 formats. torchao replaces nn.Linear
        weights with an INT8 tensor subclass while matmul outputs stay in the
        model activation dtype. This deliberately does not touch embeddings,
        caches or the later flow-matching stage.
        """
        try:
            import torchao
            from torchao.quantization import Int8WeightOnlyConfig, quantize_
        except Exception as exc:
            raise RuntimeError(
                "AR INT8 was selected but torchao is not available in this standalone environment. "
                "Run install.bat again to install the optional AR INT8 support. "
                f"Original import error: {exc!r}"
            ) from exc

        self.log(f"AR weight mode: INT8 weight-only via torchao {getattr(torchao, '__version__', 'unknown')}")
        config = Int8WeightOnlyConfig(set_inductor_config=False)

        targets = []
        for name in ("language_model", "rvq_depth_decoder"):
            model = getattr(pipe, name, None)
            if model is not None:
                targets.append((name, model))

        if not targets:
            raise RuntimeError("MiniMax Music 3 AR components were not found for INT8 quantization.")

        for name, model in targets:
            linear_count, logical_before = self._count_linear_weights(model)
            self.log(
                f"Quantizing {name}: {linear_count} Linear layers "
                f"({logical_before / (1024**3):.2f} GiB logical BF16/FP weight payload before quantization)..."
            )
            self._run_with_heartbeat(
                f"Quantizing {name}",
                lambda m=model: quantize_(m, config),
                interval=10.0,
            )
            # Do not trust element_size() after tensor-subclass conversion as a
            # physical-storage meter; report what we changed instead.
            self.log(f"Quantized {name}: INT8 weight-only Linear weights active.")

        gc.collect()
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
        self.log(
            "AR INT8 ready. Embeddings and the growing KV cache remain native precision; "
            "flow matching and audio decode are unchanged."
        )

    def _load(self, mode: str, ar_weight_mode: str = "bf16"):
        ar_weight_mode = str(ar_weight_mode or "bf16").lower()
        if ar_weight_mode not in {"bf16", "int8"}:
            ar_weight_mode = "bf16"
        if (
            self.pipe is not None
            and self.loaded_mode == mode
            and self.loaded_ar_weight_mode == ar_weight_mode
        ):
            return self.pipe
        self.unload()
        # Undo the older app-added duration/EOS patch before importing the Music 3 block.
        self._restore_native_duration_behavior()
        from diffusers import ComponentsManager, ModularPipeline

        self.log(f"Loading MiniMax Music 3 from {self.model_dir}")
        self.log(f"VRAM mode: {mode}")
        self.log(f"AR weights: {'INT8 weight-only' if ar_weight_mode == 'int8' else 'BF16'}")
        self.log(f"Before load: {self._memory_text()}")
        self._prepare_local_component_index()
        self._prepare_tokenizer_regex_fix()

        # This standalone app must never fetch model data during inference.
        # The component index above resolves every component to model_dir.
        previous_offline = os.environ.get("HF_HUB_OFFLINE")
        os.environ["HF_HUB_OFFLINE"] = "1"
        started = time.monotonic()
        try:
            self.log("Reading local modular pipeline index...")
            if mode == "full":
                pipe = ModularPipeline.from_pretrained(str(self.model_dir), local_files_only=True)
                self.log("Loading local BF16 model components...")
                self._run_with_heartbeat(
                    "Loading components",
                    lambda: pipe.load_components(dtype=torch.bfloat16),
                )
                self.log("Moving pipeline to CUDA...")
                self._run_with_heartbeat("Moving pipeline to CUDA", lambda: pipe.to("cuda"))
            else:
                manager = ComponentsManager()
                # ComponentsManager already has a native reserve-margin control.
                # On a 24 GiB card, 8GB reserve makes the automatic strategy begin
                # freeing whole components around the 16 GiB residency region instead
                # of the default ~21 GiB region (3GB reserve).
                reserve_margin = "8GB" if mode == "offload" else "3GB"
                manager.enable_auto_cpu_offload(
                    device="cuda", memory_reserve_margin=reserve_margin
                )
                if mode == "offload":
                    self.log(
                        "Automatic CPU offload reserve: 8 GB "
                        "(targeting ~16 GB model residency headroom on a 24 GB GPU)."
                    )
                pipe = ModularPipeline.from_pretrained(
                    str(self.model_dir),
                    components_manager=manager,
                    local_files_only=True,
                )
                self.log("Loading local BF16 model components with CPU offload...")
                self._run_with_heartbeat(
                    "Loading components",
                    lambda: pipe.load_components(dtype=torch.bfloat16),
                )
            if ar_weight_mode == "int8":
                self._apply_ar_int8_weight_only(pipe)

            if mode == "streaming":
                # MiniMax Music 3 calls LM submodules such as embed_tokens directly.
                # Apply leaf-level streaming after optional quantization so hooks see
                # the final Linear weight objects.
                self.log("Applying low-VRAM layer streaming...")
                from diffusers.hooks import apply_group_offloading
                apply_group_offloading(
                    pipe.language_model,
                    onload_device=torch.device("cuda"),
                    offload_device=torch.device("cpu"),
                    offload_type="leaf_level",
                    use_stream=True,
                )
        finally:
            if previous_offline is None:
                os.environ.pop("HF_HUB_OFFLINE", None)
            else:
                os.environ["HF_HUB_OFFLINE"] = previous_offline

        self.pipe = pipe
        self.loaded_mode = mode
        self.loaded_ar_weight_mode = ar_weight_mode
        self.log(f"Pipeline loaded in {time.monotonic() - started:.1f}s | {self._memory_text()}")
        frame_rate = getattr(pipe, "frame_rate", None)
        reported_sr = getattr(pipe, "sampling_rate", None)
        self.log(f"Pipeline metadata: frame_rate={frame_rate!r} | reported sampling_rate={reported_sr!r}")
        if reported_sr:
            self.log(
                f"Audio output rate: {int(reported_sr)} Hz (native Diffusers vocoder output; "
                "MiniMax's reference server may resample this to 32000 Hz)."
            )
        return pipe

    @staticmethod
    def _audio_to_soundfile_array(audio) -> np.ndarray:
        if torch.is_tensor(audio):
            arr = audio.detach().to(dtype=torch.float32, device="cpu").numpy()
        else:
            arr = np.asarray(audio, dtype=np.float32)

        # MiniMax returns channel-first stereo. soundfile expects frames x channels.
        if arr.ndim == 1:
            return arr
        if arr.ndim == 2:
            if arr.shape[0] <= 8 and arr.shape[1] > arr.shape[0]:
                return arr.T
            return arr
        raise ValueError(f"Unexpected generated audio shape: {arr.shape}")

    def _log_generation_config(self, pipe):
        """Log the real LM generation limits exposed by the loaded pipeline."""
        lm = getattr(pipe, "language_model", None)
        if lm is None:
            self.log("LM diagnostics: pipe.language_model is not exposed by this Diffusers build.")
            return
        cfg = getattr(lm, "generation_config", None)
        model_cfg = getattr(lm, "config", None)
        keys = (
            "max_new_tokens", "max_length", "min_new_tokens", "min_length",
            "eos_token_id", "pad_token_id", "bos_token_id", "do_sample",
            "temperature", "top_p", "top_k",
        )
        vals = []
        for key in keys:
            value = getattr(cfg, key, None) if cfg is not None else None
            if value is None and model_cfg is not None:
                value = getattr(model_cfg, key, None)
            if value is not None:
                vals.append(f"{key}={value!r}")
        self.log("LM generation config before call: " + (", ".join(vals) if vals else "no common generation-limit fields exposed"))

    def _install_lm_generate_probe(self, pipe):
        """Wrap Transformers GenerationMixin.generate during one Music 3 call.

        Modular Diffusers can route the LM through context/component objects, so
        patching only pipe.language_model.generate can miss the real call.  This
        class-level diagnostic wrapper catches any Transformers autoregressive
        generate() invoked while this Music 3 request is running.  It is restored
        immediately afterwards and never changes generation arguments/results.
        """
        try:
            from transformers.generation.utils import GenerationMixin
        except Exception as exc:
            self.log(f"LM deep probe unavailable: {exc!r}")
            return lambda: None

        original = GenerationMixin.generate
        call_count = {"n": 0}
        interesting = (
            "max_new_tokens", "max_length", "min_new_tokens", "min_length",
            "eos_token_id", "pad_token_id", "bos_token_id", "do_sample",
            "temperature", "top_p", "top_k", "stopping_criteria",
        )

        def _shape(value):
            try:
                return tuple(value.shape)
            except Exception:
                return None

        def wrapped_generate(model_self, *args, **kwargs):
            call_count["n"] += 1
            n = call_count["n"]
            cls_name = f"{type(model_self).__module__}.{type(model_self).__name__}"

            # Determine the prompt/input length without dumping token contents.
            input_shape = None
            for key in ("input_ids", "inputs", "inputs_embeds"):
                if key in kwargs and kwargs[key] is not None:
                    input_shape = _shape(kwargs[key])
                    if input_shape is not None:
                        break
            if input_shape is None and args:
                input_shape = _shape(args[0])

            cfg = kwargs.get("generation_config") or getattr(model_self, "generation_config", None)
            shown = []
            for key in interesting:
                if key in kwargs:
                    value = kwargs.get(key)
                else:
                    value = getattr(cfg, key, None) if cfg is not None else None
                if value is None:
                    continue
                if key == "stopping_criteria":
                    try:
                        value = [type(x).__name__ for x in value]
                    except Exception:
                        value = type(value).__name__
                shown.append(f"{key}={value!r}")

            self.log(
                f"LM deep generate call #{n}: model={cls_name} | input_shape={input_shape!r} | "
                + (", ".join(shown) if shown else "no common generation-limit values exposed")
            )
            started = time.monotonic()
            result = original(model_self, *args, **kwargs)

            seq = getattr(result, "sequences", result)
            seq_shape = _shape(seq)
            input_len = input_shape[-1] if input_shape and len(input_shape) >= 2 else None
            output_len = seq_shape[-1] if seq_shape and len(seq_shape) >= 2 else None
            generated = (output_len - input_len) if (output_len is not None and input_len is not None) else None

            eos_ids = kwargs.get("eos_token_id")
            if eos_ids is None and cfg is not None:
                eos_ids = getattr(cfg, "eos_token_id", None)
            ended_with_eos = None
            try:
                if seq is not None and eos_ids is not None:
                    eos_set = {int(eos_ids)} if isinstance(eos_ids, int) else {int(x) for x in eos_ids}
                    last = int(seq[0, -1].item())
                    ended_with_eos = last in eos_set
            except Exception:
                pass

            self.log(
                f"LM deep generate call #{n} returned after {time.monotonic()-started:.1f}s | "
                f"sequence_shape={seq_shape!r} | generated_tokens={generated!r} | "
                f"ended_with_eos={ended_with_eos!r}"
            )
            return result

        GenerationMixin.generate = wrapped_generate

        def restore():
            try:
                GenerationMixin.generate = original
            except Exception:
                pass
            if call_count["n"] == 0:
                self.log(
                    "LM deep probe: no Transformers GenerationMixin.generate call was observed. "
                    "Music 3 is using a custom generation loop; the next diagnostic target is the modular LM block itself."
                )
        return restore


    def _log_music3_runtime_source_map(self, pipe):
        """Locate the actual installed Music 3 modular generation code.

        The previous diagnostics established that this pipeline bypasses
        Transformers GenerationMixin.generate().  Rather than guessing another
        hook point, inspect the exact Diffusers source installed in this
        environment and report files/lines that implement duration, acoustic
        frame limits and EOS/stop handling.  Diagnostic only: no generation
        behavior is changed.
        """
        try:
            import diffusers
            root = Path(diffusers.__file__).resolve().parent
            self.log(f"Music 3 runtime source root: {root}")

            # First expose the live modular objects so we know which concrete
            # classes own the active blocks/components.
            seen = set()
            candidates = []
            for name in dir(pipe):
                if name.startswith("_"):
                    continue
                try:
                    obj = getattr(pipe, name)
                except Exception:
                    continue
                cls = type(obj)
                mod = getattr(cls, "__module__", "")
                if "diffusers" not in mod:
                    continue
                key = (name, mod, getattr(cls, "__name__", ""))
                if key in seen:
                    continue
                seen.add(key)
                candidates.append(key)
            if candidates:
                self.log("Music 3 live Diffusers objects:")
                for name, mod, clsname in candidates[:40]:
                    self.log(f"  {name}: {mod}.{clsname}")

            needles = (
                "audio_duration", "max_new_tokens", "max_length",
                "eos_token", "eos_token_id", "9000",
                "frame_rate", "acoustic", "stop_token",
            )
            hits = []
            for py in root.rglob("*.py"):
                try:
                    text = py.read_text(encoding="utf-8", errors="ignore")
                except Exception:
                    continue
                low = text.lower()
                # Restrict to files that are plausibly part of Music 3 or that
                # explicitly handle audio_duration; this keeps logs readable.
                if "audio_duration" not in text and "minimax" not in low and "music3" not in low and "music_3" not in low:
                    continue
                lines = text.splitlines()
                matched = []
                for i, line in enumerate(lines, 1):
                    if any(n.lower() in line.lower() for n in needles):
                        matched.append((i, line.strip()))
                if matched:
                    hits.append((py, matched))

            if not hits:
                self.log("Music 3 runtime source map: no matching Diffusers source lines found.")
                return

            self.log(f"Music 3 runtime source map: {len(hits)} candidate file(s)")
            for py, matched in hits[:12]:
                try:
                    rel = py.relative_to(root)
                except Exception:
                    rel = py
                self.log(f"SOURCE FILE: {rel}")
                # Include nearby context around each important line, but cap it
                # so one diagnostic run cannot explode the log size.
                try:
                    lines = py.read_text(encoding="utf-8", errors="ignore").splitlines()
                except Exception:
                    lines = []
                emitted = set()
                for lineno, _ in matched[:16]:
                    for j in range(max(1, lineno - 2), min(len(lines), lineno + 2) + 1):
                        if j in emitted:
                            continue
                        emitted.add(j)
                        self.log(f"  L{j}: {lines[j-1].strip()}")
        except Exception as exc:
            self.log(f"Music 3 runtime source map failed: {exc!r}")


    def _log_music3_conditioning(self, pipe, prompt: str, lyrics: str):
        """Log the concrete tokenizer and token counts used by the live Music 3 pipeline."""
        tok = getattr(pipe, "tokenizer", None)
        if tok is None:
            # ModularPipeline may keep components in the manager rather than on the public object.
            for name in ("_components", "components"):
                obj = getattr(pipe, name, None)
                if isinstance(obj, dict) and obj.get("tokenizer") is not None:
                    tok = obj.get("tokenizer")
                    break
        if tok is None:
            self.log("Music 3 conditioning probe: tokenizer is not exposed on the live pipeline.")
            return
        try:
            cls = f"{type(tok).__module__}.{type(tok).__name__}"
            self.log(f"Music 3 tokenizer: {cls}")
            self.log(
                "Tokenizer config: "
                f"model_max_length={getattr(tok, 'model_max_length', None)!r} | "
                f"bos={getattr(tok, 'bos_token_id', None)!r} | eos={getattr(tok, 'eos_token_id', None)!r} | "
                f"pad={getattr(tok, 'pad_token_id', None)!r}"
            )
            for label, text, cap in (("prompt", prompt, 256), ("lyrics", lyrics, 2048)):
                try:
                    enc = tok(text, truncation=True, max_length=cap, return_tensors="pt")
                    ids = getattr(enc, "input_ids", None)
                    n = int(ids.shape[-1]) if ids is not None else None
                    self.log(f"Music 3 {label} token count: {n!r} (cap={cap})")
                except Exception as exc:
                    self.log(f"Music 3 {label} token-count probe failed: {exc!r}")
        except Exception as exc:
            self.log(f"Music 3 conditioning probe failed: {exc!r}")

    def _install_music3_custom_loop_probe(self):
        """Lightweight trace of only the MiniMax Music 3 AR stop decision.

        v1.16 traced every helper call inside encoders.py and created enormous logs.
        This probe attaches line tracing only to the single autoregressive __call__
        function. At 250-frame milestones it also measures live AR tensor payloads
        so growing cache/history state can be distinguished from static model VRAM.
        It does not alter sampling or generation values.
        """
        import sys
        try:
            import diffusers
            root = Path(diffusers.__file__).resolve().parent
            target = root / "modular_pipelines" / "minimax_music3" / "encoders.py"
            if not target.exists():
                self.log(f"Music 3 stop probe unavailable: {target} not found")
                return lambda: None

            lines = target.read_text(encoding="utf-8", errors="ignore").splitlines()
            start = next((i for i, l in enumerate(lines, 1) if "max_frames =" in l), 287)
            loop_line = next((i for i in range(start, min(len(lines), start + 90)) if "for frame_index in range" in lines[i-1]), None)
            eos_line = next((i for i in range(start, min(len(lines), start + 90)) if "_AUDIO_END_TOKEN_ID" in lines[i-1] and "if int(sampled.item())" in lines[i-1]), None)
            max_line = next((i for i in range(start, min(len(lines), start + 90)) if "len(frame_hiddens) >= max_frames" in lines[i-1]), None)
            self.log(
                f"Music 3 lightweight stop probe: {target} | max_frames L{start} | "
                f"loop L{loop_line} | EOS L{eos_line} | max-stop L{max_line}"
            )

            old_trace = sys.gettrace()
            target_norm = str(target).lower().replace('\\', '/')
            state = {"attached": False, "max_frames": None, "last_frame": -1, "eos_frame": None, "max_stop": False}

            def scalar(value):
                try:
                    if torch.is_tensor(value):
                        return int(value.item()) if value.numel() == 1 else None
                    return int(value)
                except Exception:
                    return None

            def _tensor_storage_bytes(t):
                try:
                    # Prefer the actual tensor payload size. This intentionally does not
                    # count allocator fragmentation/reserved slabs; those are logged separately.
                    return int(t.numel()) * int(t.element_size())
                except Exception:
                    return 0

            def _summarize_live_value(value, max_depth=4, max_items=4096):
                """Return CUDA/CPU tensor payload owned by a live AR local.

                Traversal is deliberately narrow: containers plus cache-like lightweight
                objects. nn.Module/model objects are skipped so model weights do not drown
                out the growing per-frame state we are trying to identify. Shared tensor
                storages are deduplicated within each local summary.
                """
                seen_obj = set()
                seen_tensor = set()
                stats = {"cuda": 0, "cpu": 0, "cuda_tensors": 0, "cpu_tensors": 0, "samples": []}
                budget = [int(max_items)]

                def walk(obj, depth, path):
                    if budget[0] <= 0 or obj is None or depth > max_depth:
                        return
                    budget[0] -= 1
                    oid = id(obj)
                    if oid in seen_obj:
                        return
                    seen_obj.add(oid)
                    if torch.is_tensor(obj):
                        try:
                            # data_ptr is enough for diagnostic dedupe here. Empty/meta tensors
                            # can report zero and are still harmless.
                            key = (str(obj.device), int(obj.data_ptr()) if obj.device.type != "meta" else oid, _tensor_storage_bytes(obj))
                        except Exception:
                            key = (oid,)
                        if key in seen_tensor:
                            return
                        seen_tensor.add(key)
                        b = _tensor_storage_bytes(obj)
                        dev = "cuda" if getattr(obj.device, "type", "") == "cuda" else "cpu"
                        stats[dev] += b
                        stats[f"{dev}_tensors"] += 1
                        if len(stats["samples"]) < 6:
                            try:
                                stats["samples"].append(f"{path}:{tuple(obj.shape)} {str(obj.dtype).replace('torch.','')} {obj.device}")
                            except Exception:
                                pass
                        return
                    if isinstance(obj, dict):
                        for k, v in list(obj.items())[:512]:
                            walk(v, depth + 1, f"{path}.{k}")
                        return
                    if isinstance(obj, (list, tuple)):
                        for i, v in enumerate(obj[:512]):
                            walk(v, depth + 1, f"{path}[{i}]")
                        return
                    # Transformers cache objects vary across versions. Inspect their small
                    # state dictionaries, but never descend into torch modules/models.
                    if isinstance(obj, torch.nn.Module):
                        return
                    mod = type(obj).__module__.lower()
                    name = type(obj).__name__.lower()
                    cache_like = ("cache" in name or "cache_utils" in mod or "modeling_outputs" in mod)
                    if cache_like and hasattr(obj, "__dict__"):
                        try:
                            for k, v in list(vars(obj).items())[:128]:
                                walk(v, depth + 1, f"{path}.{k}")
                        except Exception:
                            pass

                walk(value, 0, "v")
                return stats

            def _log_ar_memory_snapshot(fi, loc):
                try:
                    free, total = torch.cuda.mem_get_info()
                    alloc = int(torch.cuda.memory_allocated())
                    reserved = int(torch.cuda.memory_reserved())
                    self.log(
                        f"Music 3 AR memory @ frame {fi}: VRAM used={(total-free)/(1024**3):.2f} GiB | "
                        f"torch_alloc={alloc/(1024**3):.2f} GiB | torch_reserved={reserved/(1024**3):.2f} GiB"
                    )
                    rows = []
                    static_names = {"self", "components", "language_model", "generator", "hooked", "resident"}
                    for name, value in loc.items():
                        if name in static_names:
                            continue
                        st = _summarize_live_value(value)
                        if st["cuda"] or st["cpu"]:
                            rows.append((st["cuda"], st["cpu"], name, st))
                    rows.sort(reverse=True)
                    if not rows:
                        self.log("Music 3 AR live-state tensors: none found in traced locals")
                        return
                    for cuda_b, cpu_b, name, st in rows[:12]:
                        samples = "; ".join(st["samples"][:3])
                        self.log(
                            f"  AR live {name}: CUDA={cuda_b/(1024**2):.1f} MiB "
                            f"({st['cuda_tensors']} tensors) | CPU={cpu_b/(1024**2):.1f} MiB "
                            f"({st['cpu_tensors']} tensors)" + (f" | {samples}" if samples else "")
                        )
                except Exception as exc:
                    self.log(f"Music 3 AR memory snapshot failed at frame {fi}: {exc!r}")

            def local_tracer(frame, event, arg):
                try:
                    if event == "line":
                        ln = frame.f_lineno
                        loc = frame.f_locals
                        if ln == start + 1 and "max_frames" in loc and state["max_frames"] is None:
                            state["max_frames"] = int(loc["max_frames"])
                            self.log(f"Music 3 AR budget confirmed: max_frames={state['max_frames']}")
                        if loop_line is not None and ln == loop_line:
                            fi = loc.get("frame_index")
                            if isinstance(fi, int):
                                state["last_frame"] = max(state["last_frame"], fi)
                                if fi == 0 or (fi > 0 and fi % 250 == 0):
                                    self.log(f"Music 3 AR progress: frame={fi}/{loc.get('max_frames', state['max_frames'])}")
                                    _log_ar_memory_snapshot(fi, loc)
                        if eos_line is not None and ln == eos_line:
                            sampled = scalar(loc.get("sampled"))
                            try:
                                from diffusers.modular_pipelines.minimax_music3 import encoders as _m3enc
                                eos_id = int(getattr(_m3enc, "_AUDIO_END_TOKEN_ID"))
                            except Exception:
                                eos_id = None
                            if sampled is not None and eos_id is not None and sampled == eos_id:
                                fi = loc.get("frame_index")
                                fi = fi if isinstance(fi, int) else state["last_frame"]
                                state["eos_frame"] = fi
                                self.log(
                                    f"Music 3 AR STOP: native audio-end token at frame "
                                    f"{state['eos_frame']} of {loc.get('max_frames', state['max_frames'])}"
                                )
                        if max_line is not None and ln == max_line:
                            fh = loc.get("frame_hiddens")
                            mf = loc.get("max_frames", state["max_frames"])
                            if isinstance(fh, list) and isinstance(mf, int) and len(fh) >= mf:
                                state["max_stop"] = True
                                self.log(f"Music 3 AR STOP: reached max_frames={mf}")
                    elif event == "return":
                        self.log(
                            "Music 3 AR loop returned: "
                            f"last_frame={state['last_frame']} | eos_frame={state['eos_frame']} | "
                            f"reached_max={state['max_stop']}"
                        )
                except Exception:
                    pass
                return local_tracer

            def global_tracer(frame, event, arg):
                if event != "call":
                    return None
                try:
                    fn = frame.f_code.co_filename.lower().replace('\\', '/')
                    # The AR block is the __call__ whose first line sits just before max_frames.
                    if fn == target_norm and frame.f_code.co_name == "__call__" and frame.f_code.co_firstlineno <= start <= frame.f_code.co_firstlineno + 25:
                        state["attached"] = True
                        return local_tracer
                except Exception:
                    pass
                return None

            sys.settrace(global_tracer)

            def restore():
                try:
                    sys.settrace(old_trace)
                except Exception:
                    pass
                if not state["attached"]:
                    self.log("Music 3 lightweight stop probe did not attach to the AR loop.")
                elif state["eos_frame"] is None and not state["max_stop"]:
                    self.log(
                        "Music 3 AR probe ended without a normal EOS/max-frame stop; "
                        "the run may have been cancelled or interrupted."
                    )
            return restore
        except Exception as exc:
            self.log(f"Music 3 lightweight stop probe failed to install: {exc!r}")
            return lambda: None

    def generate(self, req: GenerationRequest):
        if not torch.cuda.is_available():
            raise RuntimeError("CUDA is unavailable. MiniMax Music 3 requires CUDA inference.")
        pipe = self._load(req.vram_mode, req.ar_weight_mode)
        req.output_path.parent.mkdir(parents=True, exist_ok=True)
        gen = torch.Generator("cuda").manual_seed(req.seed)
        self.log(f"Requested maximum duration: {req.duration:.1f}s | seed={req.seed}")
        nominal_frames = int(round(float(req.duration) * 25.0))
        limit_note = "documented range" if nominal_frames <= 9000 else "EXPERIMENTAL: above documented 9,000-frame limit"
        self.log(f"Nominal acoustic-frame budget at 25 fps: {nominal_frames:,} ({limit_note})")
        self.log("Duration behavior: native MiniMax Music 3 (no app-added EOS/minimum-duration enforcement).")
        self.log("Music description sent to MiniMax unchanged:")
        self.log(req.prompt if req.prompt else "<EMPTY>")
        effective_lyrics = (req.lyrics or "").strip() or "无歌词"
        if not (req.lyrics or "").strip():
            self.log("Lyrics input was empty; using MiniMax instrumental/no-lyrics marker: 无歌词")
        self.log("Lyrics sent to MiniMax:")
        self.log(effective_lyrics)
        self.log(
            "Generation kwargs: "
            f"audio_duration={float(req.duration)!r}, output='audios', generator_device='cuda'"
        )
        self.log(f"Output: {req.output_path}")
        self._log_generation_config(pipe)
        self._log_music3_conditioning(pipe, req.prompt, effective_lyrics)
        restore_probe = self._install_music3_custom_loop_probe()
        restore_top_k = self._install_music3_top_k_override(req.ar_top_k)
        restore_flow_steps = self._install_music3_flow_steps_override(req.flow_steps)
        restore_flow_guidance = self._install_music3_flow_guidance_override(req.flow_guidance)
        started = time.monotonic()

        def run_generation():
            with torch.inference_mode():
                return pipe(
                    prompt=req.prompt,
                    lyrics=effective_lyrics,
                    audio_duration=float(req.duration),
                    generator=gen,
                    output="audios",
                )[0]

        try:
            audio = self._run_with_heartbeat("Generating audio", run_generation, interval=15.0)
        finally:
            restore_flow_guidance()
            restore_flow_steps()
            restore_top_k()
            restore_probe()
        self.log(f"Generation finished in {time.monotonic() - started:.1f}s. Inspecting returned audio...")
        wav = self._audio_to_soundfile_array(audio)
        frames = int(wav.shape[0]) if wav.ndim >= 1 else 0
        channels = int(wav.shape[1]) if wav.ndim == 2 else 1
        reported_sr = int(getattr(pipe, "sampling_rate", 44100) or 44100)
        actual_duration = frames / reported_sr if reported_sr > 0 else 0.0
        self.log(
            f"Returned audio: shape={tuple(wav.shape)} | frames={frames:,} | channels={channels} | "
            f"pipe.sampling_rate={reported_sr} Hz"
        )
        self.log(f"Actual returned duration: {actual_duration:.2f}s at {reported_sr} Hz")
        if actual_duration + 0.5 < req.duration:
            self.log(
                f"WARNING: Model ended early: requested up to {req.duration:.1f}s, "
                f"returned {actual_duration:.1f}s. MiniMax Music 3 treats audio_duration as an upper bound "
                "and the language model may emit a stop token earlier."
            )
        self.log("Encoding 16-bit PCM WAV...")
        sf.write(str(req.output_path), wav, reported_sr, subtype="PCM_16")
        self.log(
            f"Saved 16-bit stereo WAV at {reported_sr} Hz "
            f"(actual duration {actual_duration:.2f}s): {req.output_path}"
        )
        return req.output_path
