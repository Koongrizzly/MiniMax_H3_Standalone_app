from __future__ import annotations
import gc, os, sys, time, subprocess, tempfile
from collections import deque
from pathlib import Path
import numpy as np
import torch
import av
from PIL import Image
from scipy.io import wavfile
from .paths import ROOT, VENDOR_DIR, OUTPUT_DIR

# Use the supplied current ComfyUI computational implementation as an embedded Python library.
# No ComfyUI UI, HTTP server, workflow JSON, or custom-node backend is launched.
if str(VENDOR_DIR) not in sys.path:
    sys.path.insert(0, str(VENDOR_DIR))
os.chdir(ROOT)

# ComfyUI normally leaves CLI parsing disabled when embedded as a library.
# For isolated workers we intentionally pass a small set of Comfy memory/device flags
# through H3_COMFY_ARGS. Enable parsing BEFORE importing comfy.cli_args/model_management;
# otherwise Comfy silently parses [] and flags such as --cpu-vae are ignored.
_saved_argv = sys.argv[:]
_extra_comfy_args = os.environ.get("H3_COMFY_ARGS", "").split()
sys.argv = [sys.argv[0], *_extra_comfy_args]
import comfy.options
comfy.options.enable_args_parsing(True)
import comfy.cli_args
import comfy.sd
import comfy.utils
import comfy.model_management
import comfy.model_sampling
import node_helpers
import nodes
from comfy.nested_tensor import NestedTensor
from comfy_extras.nodes_minimax_h3 import _empty_av_latent
sys.argv = _saved_argv


def _load_sd(path):
    path = Path(path)
    if path.is_dir():
        # Official/Diffusers-style sharded MiniMax H3 VAE folder.
        # Merge one shard at a time into a normal state dict for Comfy's VAE loader.
        from safetensors import safe_open
        shards = sorted(path.glob("diffusion_pytorch_model-*-of-*.safetensors"))
        if not shards:
            raise FileNotFoundError(f"No diffusion_pytorch_model-*-of-*.safetensors shards found in {path}")
        print(f"Loading sharded VAE folder: {path} ({len(shards)} shards)", flush=True)
        sd = {}
        metadata = {}
        for i, shard in enumerate(shards, 1):
            print(f"  VAE shard {i}/{len(shards)}: {shard.name}", flush=True)
            with safe_open(str(shard), framework="pt", device="cpu") as f:
                if not metadata:
                    metadata = f.metadata() or {}
                for key in f.keys():
                    sd[key] = f.get_tensor(key)
        return sd, metadata
    return comfy.utils.load_torch_file(str(path), safe_load=True, return_metadata=True)

# MiniMax-H3 audio checkpoint normalization constants.  The released audio
# checkpoint stores the DAC/BigVGAN weights, while these per-channel values
# are pipeline/config constants rather than checkpoint tensors.
_MINIMAX_H3_AUDIO_LATENTS_MEAN = (
    -0.020211687488382354, 0.3876466479950502, -0.04398279799186767, -0.28591514936373,
    0.08179686214561671, -0.35782641352446604, 0.040623809960919084, -0.01552534501956604,
    -0.223362481667332, 0.1821006842509091, 0.2941778783780663, -0.07901167601970885,
    -0.056815072777201, -0.3699028221860095, -0.31616315591624855, 0.5905951377425391,
    -0.052139568068853864, 0.013673160263486295, -0.03691647864630577, 0.09732660653298163,
    -0.3394662328788498, -0.30685677538541667, -0.24504598907458763, -0.034698524462007344,
    0.02868032184767538, -0.21217779266454084, -0.1678263169941987, 0.3221287889040614,
    -0.1223055851554907, 0.4356604928128464, -0.0502599202236253, 0.3979258376211797,
)
_MINIMAX_H3_AUDIO_LATENTS_STD = (
    1.6895524230479284, 2.76263727217653, 1.7945344281264435, 1.6801681847309828,
    1.6390226546605453, 2.7788298348882177, 1.7659090095747236, 1.6199757612137327,
    2.6336525640336896, 1.8539356672817833, 2.5056497896915633, 1.811019237886178,
    1.9579657790720237, 1.6685498243529284, 1.4922469314453364, 3.298670198067373,
    1.9491804496832168, 1.8720003270431442, 1.8334080103291832, 1.6488070416529093,
    1.6176957696319716, 1.9131449234774398, 1.5695245398428617, 1.6943659940418612,
    1.8318420762504692, 1.5540637421583379, 1.9344930328968526, 1.599198216109855,
    1.718045989838149, 1.6307219190837705, 1.8661226051202384, 1.5613768203168363,
)

def _fold_legacy_weight_norm(v, g):
    # torch.nn.utils.weight_norm defaults to dim=0.  The released MiniMax-H3
    # audio checkpoint uses the legacy weight_g/weight_v representation.
    # Comfy's compact audio VAE intentionally uses plain Conv weights, so fold
    # the parametrization once on CPU before load_state_dict.
    if hasattr(torch, '_weight_norm'):
        try:
            return torch._weight_norm(v, g, 0)
        except Exception:
            pass
    dims = tuple(i for i in range(v.ndim) if i != 0)
    denom = torch.linalg.vector_norm(v.float(), dim=dims, keepdim=True).to(v.dtype)
    eps = torch.finfo(v.dtype).tiny if v.dtype.is_floating_point else 1e-12
    return v * (g.to(v.dtype) / denom.clamp_min(eps))

def _prepare_minimax_h3_audio_sd(sd):
    if 'pre_block.attn.zero_k_bias' not in sd:
        return sd, None
    legacy_g = [k for k in sd.keys() if k.endswith('.weight_g')]
    converted = 0
    missing_pairs = []
    for gk in legacy_g:
        base = gk[:-len('.weight_g')]
        vk = base + '.weight_v'
        wk = base + '.weight'
        if vk not in sd:
            missing_pairs.append(base)
            continue
        if wk not in sd:
            sd[wk] = _fold_legacy_weight_norm(sd[vk], sd[gk])
            converted += 1
        del sd[gk]
        del sd[vk]
    # These values are configuration constants in the official implementation,
    # not tensors in the released safetensors checkpoint.
    sd.setdefault('latents_mean', torch.tensor(_MINIMAX_H3_AUDIO_LATENTS_MEAN, dtype=torch.float32))
    sd.setdefault('latents_std', torch.tensor(_MINIMAX_H3_AUDIO_LATENTS_STD, dtype=torch.float32))
    info = {'converted_weight_norm_layers': converted, 'missing_weight_norm_pairs': missing_pairs}
    return sd, info

def load_vae(path):
    sd, metadata = _load_sd(path)
    sd, audio_fix = _prepare_minimax_h3_audio_sd(sd)
    if audio_fix is not None:
        print('MiniMax-H3 audio checkpoint adapter: '
              f"folded {audio_fix['converted_weight_norm_layers']} weight-norm layers; "
              f"missing pairs={len(audio_fix['missing_weight_norm_pairs'])}", flush=True)
        if audio_fix['missing_weight_norm_pairs']:
            raise RuntimeError('Audio VAE checkpoint has incomplete weight_g/weight_v pairs: ' + ', '.join(audio_fix['missing_weight_norm_pairs'][:8]))
    vae = comfy.sd.VAE(sd=sd, metadata=metadata)
    vae.throw_exception_if_invalid()
    if audio_fix is not None:
        model_keys = set(vae.first_stage_model.state_dict().keys())
        critical = {'latents_mean','latents_std','dec_in_proj.weight','decoder.conv_pre.weight','decoder.conv_post.weight'}
        missing = sorted(critical - model_keys)
        if missing:
            raise RuntimeError('Audio VAE model missing critical tensors after conversion: ' + ', '.join(missing))
    return vae

def load_image(path):
    im=Image.open(path).convert("RGB")
    arr=np.asarray(im).astype(np.float32)/255.0
    return torch.from_numpy(arr)[None,...]

def zero_conditioning(conditioning):
    out=[]
    for t in conditioning:
        d=t[1].copy()
        if d.get("pooled_output") is not None: d["pooled_output"]=torch.zeros_like(d["pooled_output"])
        if d.get("conditioning_lyrics") is not None: d["conditioning_lyrics"]=torch.zeros_like(d["conditioning_lyrics"])
        out.append([torch.zeros_like(t[0]), d])
    return out

def apply_loras(model, lora_specs):
    """Apply one or more diffusion-model LoRAs to a Comfy ModelPatcher.

    lora_specs is an iterable of (path, strength). MiniMax H3 LoRAs are applied
    to the diffusion transformer only; the Qwen text encoder is intentionally
    left untouched. A selected LoRA that matches zero model keys is treated as
    an error so the GUI never silently pretends an incompatible LoRA is active.
    """
    specs = [(Path(path), float(strength)) for path, strength in (lora_specs or []) if str(path).strip() and float(strength) != 0.0]
    if not specs:
        return model

    import comfy.lora
    import comfy.lora_convert

    for index, (path, strength) in enumerate(specs, 1):
        if not path.is_file():
            raise FileNotFoundError(f"LoRA {index} not found: {path}")
        print(f"Loading LoRA {index}/{len(specs)}: {path.name} | strength={strength:g}", flush=True)
        lora_sd, _metadata = comfy.utils.load_torch_file(str(path), safe_load=True, return_metadata=True)
        key_map = comfy.lora.model_lora_keys_unet(model.model, {})
        converted = comfy.lora_convert.convert_lora(lora_sd)
        patches = comfy.lora.load_lora(converted, key_map, log_missing=True)
        patched_model = model.clone()
        matched = patched_model.add_patches(patches, strength)
        if not matched:
            raise RuntimeError(
                f"LoRA is not compatible with the loaded MiniMax H3 diffusion model: {path.name} "
                f"(0 model keys matched; LoRA tensors={len(lora_sd)})"
            )
        model = patched_model
        print(f"LoRA {index} active: {path.name} | matched model patches={len(matched)} | strength={strength:g}", flush=True)
        del lora_sd, converted, patches
    return model


def patch_sigma(model, shift_video=12.0, shift_audio=3.0):
    m=model.clone()
    class ModelSamplingAdvanced(comfy.model_sampling.ModelSamplingAV, comfy.model_sampling.CONST):
        pass
    original=m.get_model_object("model_sampling")
    ms=ModelSamplingAdvanced(model.model.model_config)
    ms.set_parameters(shift=float(shift_video), audio_shift=float(shift_audio))
    if hasattr(original,"noise_scale"): ms.set_noise_scale(original.noise_scale)
    m.add_object_patch("model_sampling", ms)
    to=m.model_options["transformer_options"]=m.model_options.get("transformer_options",{}).copy()
    to["minimax_h3_sigma_shift_video"]=float(shift_video)
    to["minimax_h3_sigma_shift_audio"]=float(shift_audio)
    return m

def _load_video_tail_24(path, count):
    """Return the last *count* source frames sampled on a 24 fps timeline as BHWC float RGB.

    Only a small rolling tail is retained in RAM. This is continuation context, not a
    last-frame extraction: all frames except the final boundary frame are VAE encoded
    together as temporal history.
    """
    count=max(1,int(count))
    sampled=deque(maxlen=count)
    with av.open(str(path)) as container:
        if not container.streams.video:
            raise ValueError(f"No video stream found: {path}")
        stream=container.streams.video[0]
        next_t=None
        fallback_index=0
        for frame in container.decode(stream):
            if frame.pts is not None and frame.time_base is not None:
                t=float(frame.pts * frame.time_base)
            else:
                rate=float(stream.average_rate) if stream.average_rate else 24.0
                t=fallback_index/max(rate,1e-6)
            fallback_index += 1
            if next_t is None:
                next_t=t
            # Sample at 24 fps. If the source itself is 24 fps this keeps every frame.
            if t + 1e-7 < next_t:
                continue
            arr=np.asarray(frame.to_image().convert("RGB"),dtype=np.uint8)
            sampled.append(torch.from_numpy(arr.copy()).float().div_(255.0))
            while next_t <= t + 1e-7:
                next_t += 1.0/24.0
    if not sampled:
        raise ValueError(f"No decodable video frames: {path}")
    return torch.stack(list(sampled),dim=0)

def _resize_video_frames(frames,width,height,crop="center"):
    samples=frames[...,:3].movedim(-1,1)
    samples=comfy.utils.common_upscale(samples,width,height,"lanczos",crop)
    return samples.movedim(1,-1).contiguous()


def _encode_continue_history(video_vae, history, tile_size=128, tile_overlap=32):
    """Low-spill MiniMax H3 history encoder used by FL2VA Continue Video.

    The stock VAE.encode() first attempts a regular encode and only falls back to
    tiling *after* an OOM.  On Windows/WDDM that failed attempt can already spill
    tens of GiB into shared GPU memory.  Continue Video therefore goes directly to
    the MiniMax VAE's tiled path, under inference_mode, with a smaller spatial tile.
    Temporal chunking remains the model's native 17-frame scheme.
    """
    fs = video_vae.first_stage_model
    old_size = getattr(fs, "tile_size", None)
    old_overlap = getattr(fs, "tile_overlap_min", None)
    if old_size is not None:
        fs.tile_size = int(tile_size)
    if old_overlap is not None:
        fs.tile_overlap_min = int(tile_overlap)
    try:
        print(f"FL2VA continuation VAE encode: direct tiled path | temporal chunks=17f | tile={tile_size}px overlap={tile_overlap}px | autograd=off", flush=True)
        with torch.inference_mode():
            return video_vae.encode_tiled(history, tile_x=tile_size, tile_y=tile_size, overlap=tile_overlap)
    finally:
        if old_size is not None:
            fs.tile_size = old_size
        if old_overlap is not None:
            fs.tile_overlap_min = old_overlap



def _load_audio_tail_32k(path, seconds):
    """Return the final source-audio window as [1, samples, 2] float32 at 32 kHz.

    Continue Video uses the same temporal overlap duration as the visual history.
    Missing/no-audio sources return None instead of failing the video generation.
    """
    from runtime.ffmpeg_tools import ensure_ffmpeg_tools, tool_path
    seconds=max(0.0,float(seconds))
    if seconds <= 0:
        return None
    ok,msg=ensure_ffmpeg_tools(lambda x: print(x,flush=True))
    if not ok:
        print(f"FL2VA audio continuation skipped: FFmpeg unavailable: {msg}",flush=True)
        return None
    ff=str(tool_path('ffmpeg.exe'))
    # -sseof keeps this independent of the total clip length and preserves the
    # exact tail used as continuation context. Raw f32 avoids temporary files.
    cmd=[ff,'-v','error','-sseof',f'-{seconds:.9f}','-i',str(path),'-vn','-ac','2','-ar','32000','-f','f32le','pipe:1']
    r=subprocess.run(cmd,stdout=subprocess.PIPE,stderr=subprocess.PIPE,check=False)
    if r.returncode != 0 or not r.stdout:
        print('FL2VA audio continuation: source has no usable audio track; continuing with generated audio only.',flush=True)
        return None
    data=np.frombuffer(r.stdout,dtype=np.float32)
    if data.size < 2:
        return None
    data=data[:data.size-(data.size%2)].reshape(-1,2).copy()
    return torch.from_numpy(data).unsqueeze(0)

def prepare_audio_continue_conditioning(audio_vae, continue_video, continue_context_frames, fps=24.0):
    """Encode source-tail audio strictly as pre-target H3 history conditioning.

    The final ~one video-frame worth of source audio is intentionally *not* placed
    on the generated target's first instant.  That boundary conditioning caused
    the beginning of a continuation segment to replay a short piece of the source
    soundtrack even though the video had already advanced.  We keep the earlier
    tail as context and discard that boundary slice so target audio starts newly
    generated at frame 0 while still inheriting acoustic context from the source.
    """
    if not continue_video or audio_vae is None:
        return []
    requested=max(1,int(continue_context_frames))
    if requested > 1:
        requested=((requested-1)//17)*17+1
    seconds=requested/float(fps)
    waveform=_load_audio_tail_32k(continue_video,seconds)
    if waveform is None:
        return []
    # Comfy MiniMax audio VAE expects [B, samples, channels].
    with torch.inference_mode():
        latent=audio_vae.encode(waveform)
    latent=latent.detach().cpu()
    total=int(latent.shape[-1])
    if total <= 0:
        return []

    # Reserve roughly one output-video-frame of 40 Hz audio latents at the end of
    # the source tail.  Do NOT inject this reserved slice at target time zero: it
    # is the part that was audibly repeated at clip joins.
    dropped_boundary=min(total,max(1,round(40.0/float(fps))))
    history=total-dropped_boundary
    if history <= 0:
        print(f"FL2VA audio continuation skipped: source={Path(continue_video).name} | audio tail too short after boundary exclusion",flush=True)
        return []

    print(f"FL2VA audio continuation context: source={Path(continue_video).name} | requested tail={seconds:.3f}s | audio latents={total} | history={history} | excluded boundary={dropped_boundary} | target audio starts fresh",flush=True)
    return [{'anchor':'history','latent_frame_count':history,'latent':latent[...,:history]}]

def prepare_keyframe_conditioning(video_vae, width, height, frames, first_frame=None, last_frame=None, continue_video=None, continue_context_frames=35):
    """Encode FL2VA image anchors and optional native temporal continuation history.

    Continue-video conditioning follows H3's model-facing scheme: a temporal block from
    the end of the source clip is VAE-encoded as a ``history`` condition and the source
    clip's final frame is separately used as the first-frame boundary anchor. The text
    encoder therefore still sees a clean boundary image while the DiT receives motion
    history rather than depending on that one image alone.
    """
    latent, frame_count = _empty_av_latent(width, height, frames)
    images=[]; keyframes=[]
    if (first_frame or last_frame or continue_video) and video_vae is None:
        raise RuntimeError("A video VAE is required when FL2VA visual conditioning is used")
    if continue_video and first_frame:
        raise ValueError("Continue Video already supplies the first-frame boundary; remove the separate First frame")
    if continue_video:
        requested=max(1,int(continue_context_frames))
        # Native H3 overlap grid is 17k+1. Floor arbitrary values to a safe valid size.
        if requested > 1:
            requested=((requested-1)//17)*17+1
        tail=_load_video_tail_24(continue_video,requested)
        if tail.shape[0] > 1:
            # If the source is shorter than requested, floor its available overlap to 17k+1.
            available=int(tail.shape[0])
            valid=((available-1)//17)*17+1 if available > 1 else 1
            tail=tail[-valid:]
        tail=_resize_video_frames(tail,width,height,"center")
        if tail.shape[0] > 1:
            history=tail[:-1]
            history_latent=_encode_continue_history(video_vae, history)
            keyframes.append({"anchor":"history","latent_frame_count":int(history_latent.shape[2]),"latent":history_latent})
        boundary=tail[-1:]
        images.append(boundary)
        with torch.inference_mode():
            boundary_latent=video_vae.encode(boundary)
        keyframes.append({"anchor":"first","resolved_frame_index":0,"latent_frame_count":int(boundary_latent.shape[2]),"latent":boundary_latent})
        print(f"FL2VA continuation context: source={Path(continue_video).name} | source frames used={int(tail.shape[0])} | history frames={max(0,int(tail.shape[0])-1)} | boundary=final source frame", flush=True)
    elif first_frame:
        img=load_image(first_frame)
        samples=img[..., :3].movedim(-1,1)
        samples=comfy.utils.common_upscale(samples,width,height,"lanczos","disabled")
        img=samples.movedim(1,-1)
        images.append(img)
        z=video_vae.encode(img)
        keyframes.append({"anchor":"first","resolved_frame_index":0,"latent_frame_count":int(z.shape[2]),"latent":z})
    if last_frame:
        img=load_image(last_frame)
        samples=img[..., :3].movedim(-1,1)
        samples=comfy.utils.common_upscale(samples,width,height,"lanczos","center")
        img=samples.movedim(1,-1)
        images.append(img)
        z=video_vae.encode(img)
        keyframes.append({"anchor":"last","resolved_frame_index":frame_count-1,"latent_frame_count":int(z.shape[2]),"latent":z})
    return images, keyframes, latent, frame_count

def build_conditioning(clip, video_vae, prompt, width, height, frames, first_frame=None, last_frame=None, prepared_keyframes=None, prepared_audio_keyframes=None):
    if prepared_keyframes is not None:
        images, keyframes, latent, frame_count = prepared_keyframes
    else:
        latent, frame_count = _empty_av_latent(width, height, frames)
        images=[]; keyframes=[]
        if (first_frame or last_frame) and video_vae is None:
            raise RuntimeError("A video VAE is required when first/last frame conditioning is used")
        if first_frame:
            img=load_image(first_frame)
            samples=img[..., :3].movedim(-1,1)
            samples=comfy.utils.common_upscale(samples,width,height,"lanczos","disabled")
            img=samples.movedim(1,-1)
            images.append(img); keyframes.append({"resolved_frame_index":0,"image":img})
        if last_frame:
            img=load_image(last_frame)
            samples=img[..., :3].movedim(-1,1)
            samples=comfy.utils.common_upscale(samples,width,height,"lanczos","center")
            img=samples.movedim(1,-1)
            images.append(img); keyframes.append({"resolved_frame_index":frame_count-1,"image":img})
        if keyframes:
            for kf in keyframes: kf["latent"]=video_vae.encode(kf.pop("image"))
    tokens=clip.tokenize(prompt, images=images)
    positive=clip.encode_from_tokens_scheduled(tokens)
    cond_values={}
    if keyframes:
        cond_values.update({"minimax_keyframes":keyframes,"minimax_frame_count":frame_count})
    if prepared_audio_keyframes:
        cond_values["minimax_audio_keyframes"]=prepared_audio_keyframes
    if cond_values:
        positive=node_helpers.conditioning_set_values(positive,cond_values)
    negative=zero_conditioning(positive)
    return positive, negative, latent, frame_count

def _flush_models():
    try:
        comfy.model_management.unload_all_models()
    except Exception:
        pass
    gc.collect()
    try:
        comfy.model_management.soft_empty_cache(force=True)
    except Exception:
        if torch.cuda.is_available(): torch.cuda.empty_cache()

def split_av_latents(samples):
    latent=samples["samples"]
    if not getattr(latent,"is_nested",False):
        raise RuntimeError("MiniMax H3 sampler did not return nested video+audio latents")
    return latent.unbind()

def decode_video(video_latent, video_vae, force_tiled=False, tile_size=256, tile_overlap=128):
    # MiniMax H3 owns its spatial tiling internally. For the large native FP16 VAE,
    # skip Comfy's initial full-frame decode attempt and go straight to the tiled path.
    if hasattr(video_vae, "first_stage_model"):
        fs = video_vae.first_stage_model
        if hasattr(fs, "tile_size"):
            fs.tile_size = int(tile_size)
        if hasattr(fs, "tile_overlap_min"):
            fs.tile_overlap_min = int(tile_overlap)
    if force_tiled:
        images=video_vae.decode_tiled(video_latent)
    else:
        images=video_vae.decode(video_latent)
    if images.ndim==5: images=images.reshape(-1,*images.shape[-3:])
    return images.detach().cpu()

def decode_audio(audio_latent, audio_vae):
    audio=audio_vae.decode(audio_latent).movedim(-1,1).to(audio_latent.device)
    sr=int(getattr(audio_vae.first_stage_model,"output_sample_rate",32000))
    return audio.detach().cpu(), sr

def save_mp4(images, audio, sample_rate, out_path, fps=24):
    import imageio_ffmpeg
    out_path=Path(out_path); out_path.parent.mkdir(parents=True,exist_ok=True)
    ffmpeg=imageio_ffmpeg.get_ffmpeg_exe()
    with tempfile.TemporaryDirectory(prefix="h3frames_") as td:
        td=Path(td)
        imgs=(images.clamp(0,1).numpy()*255.0+0.5).astype(np.uint8)
        for i,frame in enumerate(imgs): Image.fromarray(frame).save(td/f"frame_{i:06d}.png")
        wave=audio.numpy()
        if wave.ndim==3: wave=wave[0]
        if wave.shape[0] in (1,2): wave=wave.T
        wave=np.clip(wave,-1,1).astype(np.float32)
        wav=td/"audio.wav"; wavfile.write(wav,sample_rate,wave)
        cmd=[ffmpeg,"-y","-framerate",str(fps),"-i",str(td/"frame_%06d.png"),"-i",str(wav),
             "-c:v","libx264","-pix_fmt","yuv420p","-crf","18","-c:a","aac","-b:a","256k","-shortest",str(out_path)]
        subprocess.check_call(cmd)
    return out_path

def generate(*, diffusion_model, text_encoder, video_vae, audio_vae, prompt, width=576, height=320,
             frames=124, steps=10, cfg=1.0, seed=-1, shift_video=12.0, shift_audio=3.0,
             sampler_name="euler", scheduler="simple", first_frame=None, last_frame=None, output_path=None):
    if seed is None or int(seed) < 0:
        seed = int.from_bytes(os.urandom(4), "little") % 99_000_000
    else:
        seed = int(seed) % 99_000_000
    width=int(width)//32*32; height=int(height)//32*32
    print(f"MiniMax-H3 standalone W4A8 | {width}x{height} | requested frames={frames} | steps={steps} | seed={seed}", flush=True)
    vv_for_refs=None
    prepared_keyframes=None
    if first_frame or last_frame:
        print("Loading native video VAE for frame conditioning...",flush=True)
        vv_for_refs=load_vae(video_vae)
        prepared_keyframes=prepare_keyframe_conditioning(vv_for_refs,width,height,int(frames),first_frame,last_frame)
        del vv_for_refs
        vv_for_refs=None
        _flush_models()
        print("Keyframe VAE unloaded before text encoder load.",flush=True)
    print("Loading W4A8 text encoder...",flush=True)
    clip=comfy.sd.load_clip([str(text_encoder)], clip_type=comfy.sd.CLIPType.MINIMAX)
    positive,negative,latent,actual_frames=build_conditioning(clip,vv_for_refs,prompt,width,height,int(frames),first_frame,last_frame,prepared_keyframes=prepared_keyframes)
    _flush_models()

    print("Loading W4A8 FL2VA diffusion model...",flush=True)
    model=comfy.sd.load_diffusion_model(str(diffusion_model))
    model=patch_sigma(model,shift_video,shift_audio)
    print(f"Sampling {actual_frames} frames...",flush=True)
    sampled=nodes.common_ksampler(model,int(seed),int(steps),float(cfg),sampler_name,scheduler,positive,negative,latent,denoise=1.0)[0]
    video_latent,audio_latent=split_av_latents(sampled)
    del model, clip, positive, negative, latent, sampled
    _flush_models()

    print("Loading native FP16 video VAE and decoding video...",flush=True)
    vv=load_vae(video_vae)
    images=decode_video(video_latent,vv)
    del vv, video_latent
    _flush_models()

    print("Loading audio VAE and decoding audio...",flush=True)
    av=load_vae(audio_vae)
    audio,sr=decode_audio(audio_latent,av)
    del av, audio_latent
    _flush_models()
    if output_path is None:
        OUTPUT_DIR.mkdir(parents=True,exist_ok=True)
        output_path=OUTPUT_DIR/f"minimax_h3_int4_{time.strftime('%Y%m%d_%H%M%S')}.mp4"
    out=save_mp4(images,audio,sr,output_path,24)
    print("Saved:",out,flush=True)
    return {"output":str(out),"seed":int(seed),"frames":int(actual_frames),"width":width,"height":height}
