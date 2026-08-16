from __future__ import annotations
import argparse, gc, math, subprocess, tempfile
from pathlib import Path
import numpy as np
import torch
from runtime.memory_diag import log_mem, log_mem_throttled, install_sampling_block_trace, install_comfy_load_trace
from runtime import vram_manager as _vram_manager_module
from runtime.vram_manager import VRAMManager, VRAMManagerConfig
import torchaudio
from PIL import Image
import av
from runtime.ffmpeg_tools import ensure_ffmpeg_tools, tool_path
# Import the shared embedded-Comfy bootstrap first. It adds vendor/ to sys.path,
# enables H3_COMFY_ARGS parsing, and only then imports comfy/node_helpers.
# Ref2VA previously imported comfy directly before this bootstrap, causing
# `ModuleNotFoundError: No module named 'comfy'` in the isolated worker.
from runtime.headless_h3 import comfy, nodes, patch_sigma, apply_loras, split_av_latents, _flush_models, load_vae, zero_conditioning
import node_helpers
from comfy_extras.nodes_minimax_h3 import _empty_av_latent, adapt_canvas, CANVAS_MULTIPLE, REF_IMAGE_SHORT_EDGE, FPS

def load_image(path):
    im=Image.open(path).convert('RGB'); return torch.from_numpy(np.asarray(im).astype(np.float32)/255.0)[None,...]
def resize(image,w,h,crop='disabled'):
    s=image[...,:3].movedim(-1,1); s=comfy.utils.common_upscale(s,w,h,'lanczos',crop); return s.movedim(1,-1)
def load_video_24(path, max_frames):
    frames=[]
    with av.open(str(path)) as c:
        st=c.streams.video[0]
        for f in c.decode(st):
            img=f.to_image().convert('RGB'); frames.append(torch.from_numpy(np.asarray(img).astype(np.float32)/255.0))
            if len(frames)>=max_frames: break
    if not frames: raise ValueError(f'No decodable video frames: {path}')
    return torch.stack(frames)
def extract_audio(path,tmpdir):
    wav=Path(tmpdir)/('ref_'+Path(path).stem+'.wav')
    ok, msg = ensure_ffmpeg_tools(lambda x: print(x, flush=True))
    if not ok: raise RuntimeError('FFmpeg tools unavailable: ' + msg)
    ff=str(tool_path('ffmpeg.exe'))
    r=subprocess.run([ff,'-y','-i',str(path),'-vn','-ac','2','-ar','32000',str(wav)],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    if r.returncode or not wav.is_file(): return None
    waveform,sr=torchaudio.load(str(wav)); return {'waveform':waveform.unsqueeze(0),'sample_rate':sr}
def load_audio(path):
    waveform,sr=torchaudio.load(str(path)); return {'waveform':waveform.unsqueeze(0),'sample_rate':sr}
def encode_audio(vae,audio):
    w=audio['waveform']; sr=audio['sample_rate']; vsr=getattr(vae,'audio_sample_rate',32000)
    if sr!=vsr: w=torchaudio.functional.resample(w,sr,vsr)
    # Ref2VA audio encoding is inference-only. Running the MiniMax audio VAE with
    # autograd enabled can retain a very large encoder/attention graph for long
    # reference clips. On a 24 GB Windows GPU that graph can consume the whole
    # card and spill into shared GPU memory before Qwen even starts.
    # Keep no graph at all and move the tiny 40 Hz reference latent to CPU
    # immediately, before leaving this helper.
    with torch.inference_mode():
        z=vae.encode(w[:1].movedim(1,-1))
        z=z.detach().cpu()
    return z,z.shape[-1]



def _fmt_bytes(n):
    return f"{int(n)/(1024**3):.2f} GiB"

def _log_checkpoint(label, path):
    try:
        p=Path(path)
        print(f"[MODEL] {label}: {p.name} | size={_fmt_bytes(p.stat().st_size)} | path={p}", flush=True)
    except Exception as e:
        print(f"[MODEL] {label}: {path} | size=unknown ({e})", flush=True)

def _cpu_latent(x):
    if x is None:
        return None
    try:
        return x.detach().cpu()
    except Exception:
        return x.cpu() if hasattr(x, "cpu") else x

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
    ap=argparse.ArgumentParser();
    for n in ('diffusion','text-encoder','video-vae','audio-vae','prompt','out'): ap.add_argument('--'+n,required=True)
    for n in ('width','height','frames','steps','seed'): ap.add_argument('--'+n,type=int,required=True)
    for n in ('cfg','shift','audio-shift'): ap.add_argument('--'+n,type=float,required=True)
    ap.add_argument('--sampler',default='euler'); ap.add_argument('--scheduler',default='simple'); ap.add_argument('--ref-image-size',choices=['match','max'],default='match')
    ap.add_argument('--ref-image',action='append',default=[]); ap.add_argument('--ref-video',action='append',default=[]); ap.add_argument('--ref-audio',action='append',default=[])
    ap.add_argument('--lora',action='append',default=[]); ap.add_argument('--lora-strength',action='append',type=float,default=[])
    ap.add_argument('--extended-logging', action='store_true')
    ap.add_argument('--spectrum', action='store_true', help='Enable experimental MiniMax H3 Spectrum feature forecasting')
    ap.add_argument('--experimental-long-duration', action='store_true', help='Allow native-grid research durations up to 2385 frames')
    ap.add_argument('--vram-manager', action='store_true')
    ap.add_argument('--vram-managed-stage', action='append', choices=['reference','text','diffusion'], default=[], help='Limit VRAM Manager to selected worker stage(s); omitted means all stages')
    ap.add_argument('--vram-runtime-free-gb', type=float, default=0.5)
    ap.add_argument('--vram-text-headroom-gb', type=float, default=2.0)
    ap.add_argument('--vram-diffusion-headroom-gb', type=float, default=4.0)
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
        expected = "V11_3_AUTO_REF2VA_QWEN_20260817A"
        actual = getattr(_vram_manager_module, "VRAM_MANAGER_SIGNATURE", None)
        if actual != expected:
            raise RuntimeError(f"VRAM Manager worker mismatch: expected {expected}, got {actual!r} from {getattr(_vram_manager_module, '__file__', 'unknown')}")
        print(f"[VRAM-MGR] V11.3 worker runtime verified: {getattr(_vram_manager_module, '__file__', 'unknown')}", flush=True)
    if len(ns.lora)!=len(ns.lora_strength): raise ValueError('Each --lora needs one matching --lora-strength')
    if len(ns.lora)>3: raise ValueError('Maximum 3 LoRAs are supported')
    manager=None
    if ns.vram_manager:
        manager=VRAMManager(comfy, VRAMManagerConfig(
            runtime_free_gb=max(0.1, ns.vram_runtime_free_gb),
            text_load_headroom_gb=max(ns.vram_runtime_free_gb, ns.vram_text_headroom_gb),
            diffusion_load_headroom_gb=max(ns.vram_runtime_free_gb, ns.vram_diffusion_headroom_gb),
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
    max_frames = 2385 if ns.experimental_long_duration else 719
    if ns.frames > max_frames: raise ValueError(f'Maximum allowed requested frame count is {max_frames} ({max_frames / 24.0:.3f} seconds at 24 FPS)')
    load_trace_cleanup=(install_comfy_load_trace(comfy) if ns.extended_logging else (lambda: None))
    if ns.extended_logging: log_mem('V8 worker baseline before Ref2VA stages', sync=True)
    print(f'Generation settings: {ns.width}x{ns.height} | requested frames={ns.frames} | steps={ns.steps} | CFG={ns.cfg:g} | shift={ns.shift:g} | audio shift={ns.audio_shift:g} | sampler={ns.sampler} | scheduler={ns.scheduler} | seed={ns.seed}', flush=True)
    latent,frame_count=_empty_av_latent(ns.width,ns.height,ns.frames)
    ref_items=[]; ref_blocks=[]
    if manager is not None: manager.set_stage('reference')
    print('Loading video VAE and encoding Ref2VA visual references...',flush=True); vv=load_vae(Path(ns.video_vae))
    for path in ns.ref_image[:9]:
        img=load_image(path); h,w=img.shape[1],img.shape[2]
        scale=min(1.0, math.sqrt((ns.width*ns.height)/(w*h))) if ns.ref_image_size=='match' else min(1.0,REF_IMAGE_SHORT_EDGE/min(w,h))
        tw=max(CANVAS_MULTIPLE,round(w*scale/CANVAS_MULTIPLE)*CANVAS_MULTIPLE); th=max(CANVAS_MULTIPLE,round(h*scale/CANVAS_MULTIPLE)*CANVAS_MULTIPLE)
        r=resize(img,tw,th); z=_cpu_latent(vv.encode(r)); ref_items.append({'type':'image','data':r.cpu()}); ref_blocks.append({'kind':'image','latent_h':th//16,'latent_w':tw//16,'latent':z})
    video_data=[]
    for path in ns.ref_video[:3]:
        vf=load_video_24(path,frame_count); vh,vw=vf.shape[1],vf.shape[2]; cw,ch=adapt_canvas(vw,vh)
        if vw*vh<cw*ch: cw=max(CANVAS_MULTIPLE,round(vw/CANVAS_MULTIPLE)*CANVAS_MULTIPLE); ch=max(CANVAS_MULTIPLE,round(vh/CANVAS_MULTIPLE)*CANVAS_MULTIPLE)
        frames=resize(vf,cw,ch)
        n=min(frames.shape[0],frame_count)
        if n<5: raise ValueError('MiniMax H3 reference videos need at least 5 frames')
        while n%17!=5 and n>5:n-=1
        frames=frames[:n]; z=_cpu_latent(vv.encode(frames))
        sample_idx=list(range(0,frames.shape[0],FPS//2)); qwen=frames[sample_idx].cpu()
        video_data.append((path,z,ch,cw,qwen,[i/2.0 for i in range(len(sample_idx))]))
    del vv; _flush_models()
    if ns.extended_logging: log_mem('after Ref2VA visual reference encode / VAE flush', sync=True)
    if manager is not None: manager.set_stage('reference')
    print('Loading audio VAE and encoding Ref2VA audio references...',flush=True); avae=load_vae(Path(ns.audio_vae))
    with tempfile.TemporaryDirectory(prefix='h3_ref_audio_') as td:
        for path,z,ch,cw,qwen,timestamps in video_data:
            soundtrack=extract_audio(path,td); az,at=(None,0)
            if soundtrack is not None: az,at=encode_audio(avae,soundtrack); az=_cpu_latent(az); ref_items.append({'type':'audio'})
            ref_items.append({'type':'video','data':qwen,'timestamps':timestamps})
            ref_blocks.append({'kind':'video_audio' if at else 'video','latent_t':z.shape[2],'latent_h':ch//16,'latent_w':cw//16,'ref_audio_t':at,'latent':z,'audio_latent':az})
        for path in ns.ref_audio[:3]:
            az,at=encode_audio(avae,load_audio(path)); az=_cpu_latent(az); ref_items.append({'type':'audio'}); ref_blocks.append({'kind':'audio','ref_audio_t':at,'audio_latent':az})
    del avae,video_data; _flush_models()
    if ns.extended_logging: log_mem('after Ref2VA audio reference encode / VAE flush', sync=True)
    if manager is not None: manager.set_stage('text')
    qwen_patch=None
    if ns.extended_logging:
        _log_checkpoint('text encoder checkpoint', ns.text_encoder); log_mem('before text encoder load'); torch.cuda.reset_peak_memory_stats() if torch.cuda.is_available() else None
    if ns.extended_logging or (manager is not None and manager.is_stage_managed('text')):
        qwen_patch=_install_qwen_layer_trace(manager)
    print('Loading W4A8 text encoder for Ref2VA...',flush=True); clip=comfy.sd.load_clip([ns.text_encoder],clip_type=comfy.sd.CLIPType.MINIMAX)
    if ns.extended_logging: log_mem('after text encoder object load')
    tokens=clip.tokenize(ns.prompt,minimax_ref_items=ref_items)
    if manager is not None and manager.is_stage_managed('text'):
        manager.begin_text_conditioning_admission()
        # comfy.sd.load_clip() is lazy: this is the first real Qwen CUDA residency
        # load.  Do it under the V11.3 activation reserve, then verify actual free
        # VRAM before the first transformer forward.
        clip.load_model(tokens)
        manager.prepare_text_conditioning(reason='Ref2VA pre-Qwen conditioning')
    print('Encoding prompt and Ref2VA conditioning...', flush=True)
    if ns.extended_logging: log_mem('before text encoder conditioning')
    positive=clip.encode_from_tokens_scheduled(tokens)
    if manager is not None and manager.is_stage_managed('text'):
        manager.end_text_conditioning_admission()
    if ns.extended_logging:
        log_mem('after text encoder conditioning')
        if torch.cuda.is_available(): print(f'[PEAK] text encoder CUDA peak allocated={_fmt_bytes(torch.cuda.max_memory_allocated())} reserved={_fmt_bytes(torch.cuda.max_memory_reserved())}', flush=True)
    if qwen_patch is not None: qwen_patch[0].TransformerBlock.forward=qwen_patch[1]
    positive=node_helpers.conditioning_set_values(positive,{'minimax_refs':ref_blocks}); negative=zero_conditioning(positive)
    del ref_items,ref_blocks
    if ns.vram_keep_text_encoder:
        gc.collect(); print('[VRAM-MGR] text encoder kept resident/managed after conditioning.', flush=True)
    else:
        _flush_models()
    if ns.extended_logging: log_mem('after text encoder flush', sync=True)
    if manager is not None: manager.set_stage('diffusion')
    if ns.extended_logging: _log_checkpoint('Ref2VA diffusion checkpoint', ns.diffusion); log_mem('before diffusion model load'); torch.cuda.reset_peak_memory_stats() if torch.cuda.is_available() else None
    print('Loading W4A8 Ref2VA diffusion model...',flush=True)
    model=comfy.sd.load_diffusion_model(ns.diffusion)
    if ns.extended_logging: log_mem('after diffusion checkpoint object load / before LoRA', sync=True)
    model=apply_loras(model,zip(ns.lora,ns.lora_strength))
    if ns.extended_logging: log_mem('after LoRA patching', sync=True)
    model=patch_sigma(model,ns.shift,ns.audio_shift)
    spectrum = None
    if ns.spectrum:
        from runtime.h3_spectrum import MiniMaxH3Spectrum, MIN_FIT_POINTS
        if int(ns.steps) < MIN_FIT_POINTS + 2:
            print(f'[SPECTRUM] requested but inactive: {ns.steps} steps is too short; at least {MIN_FIT_POINTS + 2} steps are required for two anchors, a forecast, and a native tail refresh.', flush=True)
        else:
            spectrum = MiniMaxH3Spectrum(total_steps=ns.steps, start_step=0, verbose=ns.extended_logging)
            opts = model.model_options.setdefault('transformer_options', {})
            opts['minimax_h3_spectrum'] = spectrum
            print(f'[SPECTRUM] enabled | standalone H3 target-row feature forecasting | steps={ns.steps} | warm-up actual passes={MIN_FIT_POINTS}', flush=True)
    if ns.extended_logging: log_mem('after sigma patch / before sampler setup', sync=True)
    if ns.extended_logging: log_mem('after diffusion model load')
    diag_cleanup=(install_sampling_block_trace(model, ns.steps) if ns.extended_logging else (lambda: None))
    mgr_cleanup=(manager.install_sampling_hooks(model) if manager is not None else (lambda: None))
    print(f'Sampling {frame_count} frames with {ns.steps} steps...',flush=True)
    if ns.extended_logging: log_mem('immediately before common_ksampler', sync=True)
    try:
        with torch.no_grad(): sampled=nodes.common_ksampler(model,ns.seed,ns.steps,ns.cfg,ns.sampler,ns.scheduler,positive,negative,latent,denoise=1.0)[0]
    finally:
        try: diag_cleanup()
        except Exception: pass
        try: mgr_cleanup()
        except Exception: pass
    if spectrum is not None:
        print(f'[SPECTRUM] sampling complete | {spectrum.summary()}', flush=True)
        spectrum.reset()
    video_latent,audio_latent=split_av_latents(sampled); payload={'video':video_latent.detach().cpu(),'audio':audio_latent.detach().cpu(),'frames':int(frame_count),'seed':int(ns.seed)}
    del sampled,video_latent,audio_latent,model,clip,positive,negative,latent; _flush_models(); gc.collect(); torch.save(payload,ns.out)
    if ns.extended_logging: log_mem('after sampling model flush / latents on CPU', sync=True)
    if manager is not None: manager.restore()
    try: load_trace_cleanup()
    except Exception: pass
    print('Ref2VA sampling stage complete.',flush=True); return 0
if __name__=='__main__': raise SystemExit(main())
