"""Timeline-only H3 native masked latent continuation (Clown Context phase 1).

Copies the previous decoded-visible endpoint's native 39-frame latent tail into
sampling input and locks it with a zero denoise mask. Unlike a keyframe guide,
this is actual frozen sampler state. Output mux must remove precisely 39 frames.
"""
from pathlib import Path
import torch


def apply_timeline_context(latent, source_sidecar, width, height, overlap_frames=39):
    import comfy.nested_tensor
    if overlap_frames != 39:
        raise ValueError('Current validated Timeline overlap is 39 frames')
    if not isinstance(latent, dict) or 'samples' not in latent:
        raise TypeError('H3 latent container is missing samples')
    sample = latent['samples']
    if not getattr(sample, 'is_nested', False):
        raise RuntimeError('MiniMax H3 sampler requires packed video/audio nested latent')
    parts = list(sample.unbind())
    if len(parts) != 2 or parts[0].ndim != 5:
        raise RuntimeError('MiniMax H3 video/audio packed latent has unexpected layout')
    p = Path(source_sidecar)
    payload = torch.load(str(p), map_location='cpu', weights_only=False)
    if not isinstance(payload, dict) or not isinstance(payload.get('video'), torch.Tensor):
        raise ValueError('Previous H3 sidecar lacks native video latent')
    src = payload['video']
    dst = parts[0]
    if src.ndim != 5 or tuple(src.shape[:2]) != tuple(dst.shape[:2]) or tuple(src.shape[3:]) != tuple(dst.shape[3:]):
        raise ValueError('Previous H3 video latent layout/channel/spatial dimensions mismatch')
    if int(payload.get('width', width)) != width or int(payload.get('height', height)) != height:
        raise ValueError('Source H3 sidecar resolution mismatch')
    # 39 frames = (39 - 5)/17*5 + 2 = 12 video tokens.
    n = 12
    if src.shape[2] < n or dst.shape[2] < n:
        raise ValueError('Need at least 39-frame native video latent on both sides')
    v = dst.clone()
    v[:, :, :n] = src[:, :, -n:].to(device=v.device, dtype=v.dtype)
    parts[0] = v
    old = latent.get('noise_mask')
    if old is not None:
        if not getattr(old, 'is_nested', False):
            raise RuntimeError('Cannot merge flat noise mask with H3 packed latent')
        masks = list(old.unbind())
        if len(masks) != len(parts):
            raise RuntimeError('Incompatible H3 noise-mask element count')
        masks = [m.clone() for m in masks]
    else:
        masks = [torch.ones_like(t) for t in parts]
    if tuple(masks[0].shape) != tuple(v.shape):
        raise RuntimeError('Video noise mask shape mismatch')
    masks[0][:, :, :n] = 0.0
    out = dict(latent)
    out['samples'] = comfy.nested_tensor.NestedTensor(tuple(parts))
    out['noise_mask'] = comfy.nested_tensor.NestedTensor(tuple(masks))
    print(f'[TIMELINE CLOWN] Masked native video tail active | source={p.name} | overlap=39 frames | '
          f'locked tokens={n} | input video tokens={int(dst.shape[2])} | '
          f'video mask first {n} tokens=0; audio remains independently generated', flush=True)
    return out
