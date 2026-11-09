"""MiniMax H3 voice-only RefMod encoder for GrizzlyMax.

Original RefMod format: Luisa (luisacaotica), ComfyUI-MiniMaxH3Mod (MIT).
With thanks to Gary for sharing RefMod work and ChatGPT-assisted tests.
Uses the existing GrizzlyMax audio VAE, FFmpeg and Python environment.
No diffusion checkpoint or LoRA training required.
"""
from __future__ import annotations

import argparse
from pathlib import Path


def main():
    ap = argparse.ArgumentParser(description="Create an H3 voice RefMod")
    ap.add_argument("--audio", required=True)
    ap.add_argument("--audio-vae", required=True)
    ap.add_argument("--output", required=True)
    ap.add_argument("--max-seconds", type=float, default=30.0)
    ns = ap.parse_args()
    src = Path(ns.audio).resolve()
    vae_path = Path(ns.audio_vae).resolve()
    dest = Path(ns.output).resolve()
    if not src.is_file() or not vae_path.is_file():
        ap.error("Audio input or audio VAE does not exist")
    if dest.suffix.lower() != ".safetensors":
        ap.error("Output must end in .safetensors")
    if not (0.5 <= ns.max_seconds <= 30.0):
        ap.error("--max-seconds must be between 0.5 and 30")
    # Initializes embedded Comfy paths and normalized H3 audio VAE loading.
    from runtime.headless_h3 import load_vae, _flush_models
    from runtime.sample_worker_ref import load_audio, encode_audio
    from runtime.refmod_support.core import H3RefMod
    print(f"[VOICE REFMOD] Decoding {src.name}", flush=True)
    audio = load_audio(str(src))
    sr = int(audio['sample_rate'])
    max_samples = int(sr * ns.max_seconds)
    audio['waveform'] = audio['waveform'][..., :max_samples].contiguous()
    if audio['waveform'].shape[-1] < sr // 2:
        raise ValueError("Voice file has less than 0.5 seconds of decodable audio")
    print(f"[VOICE REFMOD] Loading audio VAE {vae_path.name}", flush=True)
    vae = load_vae(vae_path)
    latent, length = encode_audio(vae, audio)
    del vae
    _flush_models()
    print(f"[VOICE REFMOD] Audio latent shape {tuple(latent.shape)}", flush=True)
    mod = H3RefMod(
        name=dest.stem, kind="audio", latent=latent.contiguous(),
        latent_t=int(length), mode="encode", source="audio",
        source_shape=str(tuple(audio['waveform'].shape)),
        concept_type="voice", sample_rate=sr,
        description="Voice reference (not guaranteed voice cloning)",
    )
    dest.parent.mkdir(parents=True, exist_ok=True)
    saved = mod.save(str(dest.with_suffix('')))
    print(f"[VOICE REFMOD] Saved {saved}", flush=True)


if __name__ == "__main__":
    main()
