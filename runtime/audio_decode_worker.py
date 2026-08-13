from __future__ import annotations
import argparse, gc
import numpy as np, torch
from scipy.io import wavfile
from runtime.headless_h3 import load_vae, decode_audio, _flush_models
from runtime.memory_diag import log_mem, reset_cuda_peaks

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--latents',required=True); ap.add_argument('--vae',required=True); ap.add_argument('--wav',required=True); ap.add_argument('--extended-logging', action='store_true')
    ns=ap.parse_args(); d=torch.load(ns.latents,map_location='cpu',weights_only=False); audio_latent=d['audio']
    if ns.extended_logging:
        reset_cuda_peaks(); log_mem('audio worker start', audio_latent)
    torch.set_grad_enabled(False)
    print('Loading audio VAE...', flush=True)
    av=load_vae(ns.vae)
    if ns.extended_logging: log_mem('after audio VAE load')
    print('Decoding audio...', flush=True)
    with torch.inference_mode():
        if ns.extended_logging: log_mem('immediately before audio decode', audio_latent)
        audio,sr=decode_audio(audio_latent,av)
        if ns.extended_logging: log_mem('immediately after audio decode', audio)
    finite = torch.isfinite(audio)
    finite_count = int(finite.sum().item()); total = int(audio.numel())
    if ns.extended_logging: print(f'Audio decode tensor: shape={tuple(audio.shape)} dtype={audio.dtype} finite={finite_count}/{total}', flush=True)
    if finite_count != total:
        raise RuntimeError(f'Audio VAE produced NaN/Inf samples: {total-finite_count} invalid values; refusing to write a corrupt WAV.')
    amin=float(audio.min().item()); amax=float(audio.max().item()); amean=float(audio.mean().item()); astd=float(audio.std().item())
    if ns.extended_logging: print(f'Audio stats: min={amin:.6f} max={amax:.6f} mean={amean:.6f} std={astd:.6f} sr={sr}', flush=True)
    wave=audio.numpy()
    if wave.ndim==3: wave=wave[0]
    if wave.shape[0] in (1,2): wave=wave.T
    wave=np.clip(wave,-1,1).astype(np.float32)
    wavfile.write(ns.wav,sr,wave)
    del av,audio,audio_latent,d; _flush_models(); gc.collect()
    if ns.extended_logging: log_mem('audio worker finished')
    print('Audio decode stage complete with finite waveform.',flush=True)
    return 0
if __name__=='__main__': raise SystemExit(main())
