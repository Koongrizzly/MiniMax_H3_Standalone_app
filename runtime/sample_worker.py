from __future__ import annotations
import argparse, os, gc
from pathlib import Path
import torch
from runtime.memory_diag import log_mem, log_mem_throttled, install_sampling_block_trace, install_comfy_load_trace
from runtime import vram_manager as _vram_manager_module
from runtime.vram_manager import VRAMManager, VRAMManagerConfig
from runtime.headless_h3 import comfy, nodes, build_conditioning, prepare_keyframe_conditioning, prepare_audio_continue_conditioning, patch_sigma, apply_loras, split_av_latents, _flush_models, load_vae



def _fmt_bytes(n):
    return f"{int(n)/(1024**3):.2f} GiB"

def _log_checkpoint(label, path):
    try:
        p=Path(path)
        print(f"[MODEL] {label}: {p.name} | size={_fmt_bytes(p.stat().st_size)} | path={p}", flush=True)
    except Exception as e:
        print(f"[MODEL] {label}: {path} | size=unknown ({e})", flush=True)

def _install_qwen_layer_trace(manager=None):
    import comfy.text_encoders.llama as h3_llama
    original=h3_llama.TransformerBlock.forward
    counter={'n':0}
    def traced(self, x, *args, **kwargs):
        n=counter['n']; counter['n'] += 1
        if manager is not None:
            manager.maybe_check_blocks(reason=f'Qwen boundary {n:03d}')
        log_mem_throttled('qwen_layers', f'Qwen transformer layer ACTIVE | call={n:03d}', x, interval=1.0)
        out=original(self, x, *args, **kwargs)
        return out
    h3_llama.TransformerBlock.forward=traced
    return h3_llama, original

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--diffusion', required=True); ap.add_argument('--text-encoder', required=True); ap.add_argument('--video-vae'); ap.add_argument('--audio-vae')
    ap.add_argument('--prompt', required=True); ap.add_argument('--width', type=int, required=True); ap.add_argument('--height', type=int, required=True)
    ap.add_argument('--frames', type=int, required=True); ap.add_argument('--steps', type=int, required=True); ap.add_argument('--cfg', type=float, required=True)
    ap.add_argument('--seed', type=int, required=True); ap.add_argument('--shift', type=float, required=True); ap.add_argument('--audio-shift', type=float, required=True)
    ap.add_argument('--sampler', default='euler'); ap.add_argument('--scheduler', default='simple'); ap.add_argument('--out', required=True)
    ap.add_argument('--first-frame'); ap.add_argument('--last-frame'); ap.add_argument('--continue-video'); ap.add_argument('--continue-context-frames', type=int, default=39); ap.add_argument('--continue-audio-memory', action='store_true')
    ap.add_argument('--lora', action='append', default=[]); ap.add_argument('--lora-strength', action='append', type=float, default=[])
    ap.add_argument('--extended-logging', action='store_true')
    ap.add_argument('--spectrum', action='store_true', help='Enable MiniMax H3 Spectrum feature forecasting')
    ap.add_argument('--experimental-long-duration', action='store_true', help='Allow native-grid research durations up to 2385 frames')
    ap.add_argument('--vram-manager', action='store_true')
    ap.add_argument('--vram-managed-stage', action='append', choices=['reference','text','diffusion'], default=[], help='Limit VRAM Manager to selected worker stage(s); omitted means all stages')
    ap.add_argument('--vram-runtime-free-gb', type=float, default=0.5)
    ap.add_argument('--vram-text-headroom-gb', type=float, default=2.0)
    ap.add_argument('--vram-diffusion-headroom-gb', type=float, default=4.0)
    ap.add_argument('--vram-vae-headroom-gb', type=float, default=6.0)
    ap.add_argument('--vram-offload-chunk-mb', type=int, default=512)
    ap.add_argument('--vram-max-resident-weights-gb', type=float, default=0.0)
    ap.add_argument('--vram-block-check-interval', type=int, default=1)
    ap.add_argument('--vram-residency-fill', action=argparse.BooleanOptionalAction, default=True)
    ap.add_argument('--vram-residency-target-free-gb', type=float, default=0.5)
    ap.add_argument('--vram-residency-warmup-blocks', type=int, default=2)
    ap.add_argument('--vram-residency-refill-interval', type=int, default=1)
    ap.add_argument('--vram-allocator-fraction', type=float, default=0.94)
    ap.add_argument('--vram-cache-trim-slack-gb', type=float, default=2.0)
    ap.add_argument('--vram-keep-text-encoder', action='store_true')
    ns=ap.parse_args()
    if ns.vram_manager:
        expected = "V11_4_REF_VAE_ADMISSION_20260817A"
        actual = getattr(_vram_manager_module, "VRAM_MANAGER_SIGNATURE", None)
        if actual != expected:
            raise RuntimeError(f"VRAM Manager worker mismatch: expected {expected}, got {actual!r} from {getattr(_vram_manager_module, '__file__', 'unknown')}")
        print(f"[VRAM-MGR] V11.4 worker runtime verified: {getattr(_vram_manager_module, '__file__', 'unknown')}", flush=True)
    torch.set_grad_enabled(False)
    if len(ns.lora) != len(ns.lora_strength): raise ValueError('Each --lora needs one matching --lora-strength')
    if len(ns.lora) > 3: raise ValueError('Maximum 3 LoRAs are supported')
    max_frames = 2385 if ns.experimental_long_duration else 719
    if ns.frames > max_frames: raise ValueError(f'Maximum allowed requested frame count is {max_frames} ({max_frames / 24.0:.3f} seconds at 24 FPS)')
    manager=None
    if ns.vram_manager:
        manager=VRAMManager(comfy, VRAMManagerConfig(
            runtime_free_gb=max(0.1, ns.vram_runtime_free_gb),
            text_load_headroom_gb=max(ns.vram_runtime_free_gb, ns.vram_text_headroom_gb),
            diffusion_load_headroom_gb=max(ns.vram_runtime_free_gb, ns.vram_diffusion_headroom_gb),
            vae_load_headroom_gb=max(ns.vram_runtime_free_gb, ns.vram_vae_headroom_gb),
            offload_chunk_mb=max(64, ns.vram_offload_chunk_mb),
            max_resident_weights_gb=max(0.0, ns.vram_max_resident_weights_gb),
            block_check_interval=max(1, ns.vram_block_check_interval),
            residency_fill_enabled=bool(ns.vram_residency_fill),
            residency_target_free_gb=max(ns.vram_runtime_free_gb, ns.vram_residency_target_free_gb),
            residency_warmup_blocks=max(0, ns.vram_residency_warmup_blocks),
            residency_refill_interval=max(1, ns.vram_residency_refill_interval),
            allocator_memory_fraction=min(0.99, max(0.50, ns.vram_allocator_fraction)),
            cache_trim_slack_gb=max(0.25, ns.vram_cache_trim_slack_gb),
            managed_stages=tuple(ns.vram_managed_stage) or None,
        ), verbose=ns.extended_logging)
        manager.install(); manager.set_stage('text')
    load_trace_cleanup=(install_comfy_load_trace(comfy) if ns.extended_logging else (lambda: None))
    log_mem('V8 worker baseline before model stages', sync=True) if ns.extended_logging else None
    vv=None
    prepared_keyframes=None
    prepared_audio_keyframes=None
    if ns.first_frame or ns.last_frame or ns.continue_video:
        if not ns.video_vae: raise ValueError('--video-vae is required for FL2VA visual conditioning')
        if ns.continue_video and ns.first_frame: raise ValueError('--continue-video cannot be combined with --first-frame')
        if ns.continue_video and not Path(ns.continue_video).is_file(): raise ValueError(f'Continue video not found: {ns.continue_video}')
        if ns.continue_context_frames < 1: raise ValueError('--continue-context-frames must be at least 1')
        if manager is not None:
            manager.set_stage('reference')
            manager.trim_cuda_cache(reason='pre-keyframe-vae', force=True)
        print('Loading native video VAE for keyframe conditioning...', flush=True)
        if ns.extended_logging: log_mem('before keyframe VAE load', sync=True)
        vv=load_vae(Path(ns.video_vae))
        print('Encoding FL2VA keyframe conditioning before text encoder load...', flush=True)
        prepared_keyframes=prepare_keyframe_conditioning(vv,ns.width,ns.height,ns.frames,ns.first_frame,ns.last_frame,ns.continue_video,ns.continue_context_frames)
        if ns.extended_logging: log_mem('after keyframe VAE encode / before VAE flush', sync=True)
        del vv
        vv=None
        _flush_models()
        if ns.extended_logging: log_mem('after keyframe VAE flush / before text encoder load', sync=True)
        print('Keyframe VAE unloaded before text encoder load.', flush=True)
        if ns.continue_video and ns.continue_audio_memory:
            if not ns.audio_vae:
                raise ValueError('--audio-vae is required when --continue-audio-memory is enabled')
            if manager is not None:
                manager.set_stage('reference')
                manager.trim_cuda_cache(reason='pre-continue-audio-vae', force=True)
            print('FL2VA source-audio memory: ENABLED | 1.000s tail | 40 Hz timeline end-alignment', flush=True)
            print('Loading native audio VAE for continuation audio history...', flush=True)
            av_for_history=load_vae(Path(ns.audio_vae))
            prepared_audio_keyframes=prepare_audio_continue_conditioning(av_for_history,ns.continue_video,24,24.0)
            del av_for_history
            _flush_models()
            if ns.extended_logging: log_mem('after continuation audio VAE flush / before text encoder load', sync=True)
            print('Continuation audio VAE unloaded before text encoder load.', flush=True)
        elif ns.continue_video:
            print('FL2VA source-audio memory: disabled; continuing with normal newly generated audio.', flush=True)
        if manager is not None:
            manager.set_stage('text')
            manager.trim_cuda_cache(reason='post-keyframe-vae', force=True)
    print(f'Generation settings: {ns.width}x{ns.height} | requested frames={ns.frames} | steps={ns.steps} | CFG={ns.cfg:g} | shift={ns.shift:g} | audio shift={ns.audio_shift:g} | sampler={ns.sampler} | scheduler={ns.scheduler} | seed={ns.seed}', flush=True)
    qwen_patch=None
    if ns.extended_logging:
        _log_checkpoint('text encoder checkpoint', ns.text_encoder); log_mem('before text encoder load'); torch.cuda.reset_peak_memory_stats() if torch.cuda.is_available() else None
    if ns.extended_logging or (manager is not None and manager.is_stage_managed('text')):
        qwen_patch=_install_qwen_layer_trace(manager)
    print('Loading W4A8 text encoder...', flush=True)
    clip=comfy.sd.load_clip([ns.text_encoder], clip_type=comfy.sd.CLIPType.MINIMAX)
    if ns.extended_logging: log_mem('after text encoder object load')
    # build_conditioning() tokenizes internally, so make a matching token set here
    # solely to force the lazy Qwen residency load under V11.4 admission control.
    if manager is not None and manager.is_stage_managed('text'):
        admission_images=prepared_keyframes[0] if prepared_keyframes is not None else []
        admission_tokens=clip.tokenize(ns.prompt, images=admission_images)
        manager.begin_text_conditioning_admission()
        clip.load_model(admission_tokens)
        manager.prepare_text_conditioning(reason='FL2VA pre-Qwen conditioning')
        del admission_tokens
    print('Encoding prompt and building conditioning...', flush=True)
    if ns.extended_logging: log_mem('before text encoder conditioning')
    positive,negative,latent,actual_frames=build_conditioning(clip,vv,ns.prompt,ns.width,ns.height,ns.frames,ns.first_frame,ns.last_frame,prepared_keyframes=prepared_keyframes,prepared_audio_keyframes=prepared_audio_keyframes)
    if manager is not None and manager.is_stage_managed('text'):
        manager.end_text_conditioning_admission()
    if ns.extended_logging:
        log_mem('after text encoder conditioning', latent.get('samples') if isinstance(latent,dict) and torch.is_tensor(latent.get('samples')) else None)
        if torch.cuda.is_available(): print(f'[PEAK] text encoder CUDA peak allocated={_fmt_bytes(torch.cuda.max_memory_allocated())} reserved={_fmt_bytes(torch.cuda.max_memory_reserved())}', flush=True)
    if qwen_patch is not None: qwen_patch[0].TransformerBlock.forward=qwen_patch[1]
    del vv
    if ns.vram_keep_text_encoder:
        gc.collect()
        print('[VRAM-MGR] text encoder kept resident/managed after conditioning.', flush=True)
    else:
        _flush_models()
    if ns.extended_logging: log_mem('after text encoder flush', sync=True)
    if manager is not None: manager.set_stage('diffusion')
    if ns.extended_logging: _log_checkpoint('FL2VA diffusion checkpoint', ns.diffusion); log_mem('before diffusion model load'); torch.cuda.reset_peak_memory_stats() if torch.cuda.is_available() else None
    print('Loading W4A8 FL2VA diffusion model...', flush=True)
    model=comfy.sd.load_diffusion_model(ns.diffusion)
    if ns.extended_logging: log_mem('after diffusion checkpoint object load / before LoRA', sync=True)
    model=apply_loras(model, zip(ns.lora, ns.lora_strength))
    if ns.extended_logging: log_mem('after LoRA patching', sync=True)
    model=patch_sigma(model,ns.shift,ns.audio_shift)
    spectrum_controller = None
    if ns.spectrum:
        from runtime.h3_spectrum import MiniMaxH3Spectrum, MIN_FIT_POINTS
        if int(ns.steps) < MIN_FIT_POINTS + 2:
            print(f'[SPECTRUM] requested, but {ns.steps} denoise steps cannot benefit: the forecaster needs {MIN_FIT_POINTS} real H3 anchors plus a later forecastable step. Spectrum left inactive for this job.', flush=True)
        else:
            spectrum_controller = MiniMaxH3Spectrum(total_steps=int(ns.steps), verbose=bool(ns.extended_logging))
            to = model.model_options['transformer_options'] = model.model_options.get('transformer_options', {}).copy()
            to['minimax_h3_spectrum'] = spectrum_controller
            print(f'[SPECTRUM] enabled | degree=1 | warm-up={MIN_FIT_POINTS} actual steps | max consecutive forecasts=1 | implementation=standalone H3 target-row spectral forecasting', flush=True)
    if ns.extended_logging: log_mem('after sigma patch / before sampler setup', sync=True)
    if ns.extended_logging: log_mem('after diffusion model load')
    diag_cleanup=(install_sampling_block_trace(model, ns.steps) if ns.extended_logging else (lambda: None))
    mgr_cleanup=(manager.install_sampling_hooks(model) if manager is not None else (lambda: None))
    print(f'Sampling {actual_frames} frames with {ns.steps} steps...', flush=True)
    if ns.extended_logging: log_mem('immediately before common_ksampler', sync=True)
    if manager is not None:
        manager.trim_cuda_cache(reason='pre-sampler', force=True)
    try:
        with torch.no_grad(): sampled=nodes.common_ksampler(model,ns.seed,ns.steps,ns.cfg,ns.sampler,ns.scheduler,positive,negative,latent,denoise=1.0)[0]
    finally:
        try: diag_cleanup()
        except Exception: pass
        try: mgr_cleanup()
        except Exception: pass
    if spectrum_controller is not None:
        print(f'[SPECTRUM] sampling complete | {spectrum_controller.summary()}', flush=True)
        spectrum_controller.reset()
    video_latent,audio_latent=split_av_latents(sampled)
    def diag(name,t):
        tf=t.detach().float(); finite=torch.isfinite(tf); ratio=float(finite.float().mean().item()); vals=tf[finite]
        if vals.numel(): print(f'[LATENT] {name}: shape={tuple(t.shape)} dtype={t.dtype} device={t.device} finite={ratio:.6f} min={vals.min().item():.6g} max={vals.max().item():.6g} mean={vals.mean().item():.6g} std={vals.std().item():.6g}',flush=True)
        if ratio<1.0: raise RuntimeError(f'{name} latent contains NaN/Inf')
    diag('video',video_latent); diag('audio',audio_latent)
    payload={'video':video_latent.detach().cpu(),'audio':audio_latent.detach().cpu(),'frames':int(actual_frames),'seed':int(ns.seed)}
    del sampled,video_latent,audio_latent,model,clip,positive,negative,latent; _flush_models(); gc.collect(); torch.save(payload,ns.out)
    if ns.extended_logging: log_mem('after sampling model flush / latents on CPU', sync=True)
    if manager is not None: manager.restore()
    try: load_trace_cleanup()
    except Exception: pass
    print('Sampling stage complete; latents moved to CPU and saved.',flush=True); return 0
if __name__=='__main__': raise SystemExit(main())
