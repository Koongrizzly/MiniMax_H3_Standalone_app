from __future__ import annotations
import argparse, json
from pathlib import Path
from safetensors import safe_open
from .paths import (
    DIFFUSION_DIR, TEXT_ENCODER_DIR, VIDEO_VAE_DIR, AUDIO_VAE_DIR,
    EXPECTED_FL2VA, EXPECTED_REF2VA, EXPECTED_TE,
    EXPECTED_VIDEO_VAE, EXPECTED_AUDIO_VAE,
)

def _safe_candidates(folder: Path):
    if not folder.exists():
        return []
    return sorted(
        [p for p in folder.rglob("*.safetensors") if p.is_file()],
        key=lambda p: (len(p.parts), p.name.lower(), str(p).lower()),
    )

def _keys(path: Path):
    with safe_open(str(path), framework="pt", device="cpu") as f:
        return set(f.keys()), (f.metadata() or {})

def inspect_quant(path: Path):
    keys, md = _keys(path)
    raw = md.get("_quantization_metadata") or md.get("quantization_metadata")
    qmd = None
    if raw:
        try: qmd = json.loads(raw)
        except Exception: qmd = raw
    text = json.dumps(qmd, ensure_ascii=False) if qmd is not None else ""
    format_ok = (
        "asym_w4a8_int8" in text
        or md.get("format") == "asym_w4a8_int8"
        or (
            any(k.endswith("weight_s_rel") for k in keys)
            and any(k.endswith("weight_codebook") for k in keys)
        )
    )
    field_hits = {
        "weight_s_rel": any(k.endswith("weight_s_rel") for k in keys),
        "weight_codebook": any(k.endswith("weight_codebook") for k in keys),
        "weight_s_channel": any(k.endswith("weight_s_channel") for k in keys),
    }
    return md, qmd, format_ok, field_hits, len(keys)

def inspect_video_vae(path: Path):
    keys, _ = _keys(path)
    required = {"decoder.transformer_blocks.0.scale1", "encoder.down.5.block.0.conv1.weight"}
    return required.issubset(keys), sorted(required - keys), len(keys)

def inspect_audio_vae(path: Path):
    keys, _ = _keys(path); missing = []
    for k in ("pre_block.attn.zero_k_bias", "dec_in_proj.weight"):
        if k not in keys: missing.append(k)
    if not ("decoder.conv_pre.weight" in keys or ("decoder.conv_pre.weight_g" in keys and "decoder.conv_pre.weight_v" in keys)):
        missing.append("decoder.conv_pre.weight OR decoder.conv_pre.weight_g+weight_v")
    return len(missing) == 0, missing, len(keys)

def _name_score(path: Path, kind: str, expected: str):
    n = path.name.lower()
    score = 0
    if path.name == expected:
        score += 10000
    terms = {
        "fl2va": (("fl2va", 500), ("ref2va", -1000), ("pruned", 80), ("convrot", 80), ("w4a8", 80), ("int4", 50)),
        "ref2va": (("ref2va", 500), ("fl2va", -1000), ("pruned", 80), ("convrot", 80), ("w4a8", 80), ("int4", 50)),
        "text_encoder": (("qwen", 400), ("text", 100), ("encoder", 100), ("minimax", 100), ("convrot", 80), ("w4a8", 80), ("int4", 50)),
        "video_vae": (("video", 300), ("vae", 200), ("audio", -1000), ("fp16", 70), ("hq", 30)),
        "audio_vae": (("audio", 350), ("vae", 200), ("video", -1000), ("fp32", 50), ("fp16", 30)),
    }
    for term, val in terms.get(kind, ()):
        if term in n: score += val
    # Prefer files directly in the intended component folder over deeply nested accidental copies.
    score -= max(0, len(path.parts) - 1)
    return score

def _inspect_for_kind(path: Path, kind: str):
    if kind in ("fl2va", "ref2va", "text_encoder"):
        md, qmd, ok, hits, n = inspect_quant(path)
        return ok and hits["weight_s_rel"] and hits["weight_codebook"], n
    if kind == "video_vae":
        ok, missing, n = inspect_video_vae(path)
        return ok, n
    if kind == "audio_vae":
        ok, missing, n = inspect_audio_vae(path)
        return ok, n
    return False, 0

def _resolve_override_or_scan(raw, folder: Path, expected: str, label: str, kind: str):
    # Explicit override always wins. A folder override is scanned recursively.
    if raw:
        p = Path(raw).expanduser().resolve()
        if p.is_file():
            try:
                ok, _ = _inspect_for_kind(p, kind)
            except Exception as e:
                return None, f"{label}: failed reading override {p}: {e}"
            if ok:
                return p, None
            return None, f"{label}: selected file is not a compatible {label} checkpoint: {p}"
        if p.is_dir():
            folder = p
        else:
            return None, f"{label}: path does not exist: {p}"

    exact = folder / expected
    if exact.is_file():
        try:
            ok, _ = _inspect_for_kind(exact, kind)
            if ok:
                return exact, None
        except Exception:
            pass

    candidates = _safe_candidates(folder)
    valid = []
    read_errors = []
    for cand in candidates:
        try:
            ok, n = _inspect_for_kind(cand, kind)
            if ok:
                valid.append((_name_score(cand, kind, expected), n, cand))
        except Exception as e:
            read_errors.append(f"{cand.name}: {e}")

    if valid:
        valid.sort(key=lambda x: (-x[0], -x[1], str(x[2]).lower()))
        return valid[0][2], None

    # Give a useful special message when an official Diffusers-style visual VAE
    # folder is present. Those shards are not a Comfy single-file video-VAE checkpoint.
    if kind == "video_vae":
        for d in [folder, *[p for p in folder.rglob("*") if p.is_dir()]]:
            cfg = d / "config.json"
            idx = d / "diffusion_pytorch_model.safetensors.index.json"
            shards = list(d.glob("diffusion_pytorch_model-*-of-*.safetensors"))
            if cfg.is_file() and (idx.is_file() or len(shards) > 1):
                return None, (
                    f"{label}: found official Diffusers-style VAE at {d}, but the current "
                    "Comfy video-VAE loader needs a compatible single-file video VAE checkpoint. "
                    "The official shards were NOT treated as a broken single-file VAE."
                )

    if not candidates:
        return None, f"No {label} checkpoint found under: {folder}"
    names = ", ".join(p.name for p in candidates[:8])
    extra = "" if len(candidates) <= 8 else f" (+{len(candidates)-8} more)"
    return None, f"No compatible {label} found under {folder}. Scanned: {names}{extra}"

def validate(require_vae=True, mode="both", fl2va_path=None, ref2va_path=None, text_encoder_path=None, video_vae_path=None, audio_vae_path=None):
    errors = []; info = []
    diff = ref = None
    if mode in ("both", "fl2va"):
        diff, err = _resolve_override_or_scan(fl2va_path, DIFFUSION_DIR, EXPECTED_FL2VA, "FL2VA model", "fl2va")
        if err: errors.append(err)
    if mode in ("both", "ref2va"):
        ref, err = _resolve_override_or_scan(ref2va_path, DIFFUSION_DIR, EXPECTED_REF2VA, "Ref2VA model", "ref2va")
        if err: errors.append(err)

    te, err = _resolve_override_or_scan(text_encoder_path, TEXT_ENCODER_DIR, EXPECTED_TE, "text encoder", "text_encoder")
    if err: errors.append(err)

    vv = av = None
    if require_vae:
        vv, err = _resolve_override_or_scan(video_vae_path, VIDEO_VAE_DIR, EXPECTED_VIDEO_VAE, "video VAE", "video_vae")
        if err: errors.append(err)
        av, err = _resolve_override_or_scan(audio_vae_path, AUDIO_VAE_DIR, EXPECTED_AUDIO_VAE, "audio VAE", "audio_vae")
        if err: errors.append(err)

    for label, p in (("fl2va", diff), ("ref2va", ref), ("text_encoder", te)):
        if not p: continue
        try:
            md, qmd, ok, hits, n = inspect_quant(p); info.append((label, p, ok, hits, n, qmd))
            if not ok: errors.append(f"{label}: {p.name} does not look like a supported W4A8/ConvRot checkpoint")
            if not hits["weight_s_rel"]: errors.append(f"{label}: no weight_s_rel tensors found")
            if not hits["weight_codebook"]: errors.append(f"{label}: no weight_codebook tensors found")
        except Exception as e: errors.append(f"{label}: failed reading {p}: {e}")

    if vv:
        try:
            ok, missing, n = inspect_video_vae(vv); info.append(("video_vae", vv, ok, {"native_keys": ok}, n, None))
            if not ok: errors.append(f"video_vae: wrong checkpoint structure; missing keys: {', '.join(missing)}")
        except Exception as e: errors.append(f"video_vae: failed reading {vv}: {e}")

    if av:
        try:
            ok, missing, n = inspect_audio_vae(av); info.append(("audio_vae", av, ok, {"native_keys": ok}, n, None))
            if not ok: errors.append(f"audio_vae: wrong checkpoint structure; missing keys: {', '.join(missing)}")
        except Exception as e: errors.append(f"audio_vae: failed reading {av}: {e}")

    return errors, info, diff, ref, te, vv, av

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--weights-only", action="store_true")
    ap.add_argument("--mode", choices=["both", "fl2va", "ref2va"], default="both")
    ap.add_argument("--fl2va-checkpoint"); ap.add_argument("--ref2va-checkpoint")
    ap.add_argument("--text-encoder"); ap.add_argument("--video-vae"); ap.add_argument("--audio-vae")
    ns = ap.parse_args()

    errors, info, diff, ref, te, vv, av = validate(
        require_vae=not ns.weights_only, mode=ns.mode,
        fl2va_path=ns.fl2va_checkpoint, ref2va_path=ns.ref2va_checkpoint,
        text_encoder_path=ns.text_encoder, video_vae_path=ns.video_vae,
        audio_vae_path=ns.audio_vae,
    )

    print("MiniMax-H3 standalone model discovery / validation")
    for label, p, ok, hits, n, qmd in info:
        print(f"[{label}] {p}")
        print(f"  tensors={n} structure_ok={ok} fields={hits}")
        if isinstance(qmd, dict):
            print("  quant format:", qmd.get("format", qmd.get("quantization_format", "<nested metadata>")))
    if errors:
        print("\nVALIDATION FAILED")
        for e in errors: print(" -", e)
        return 2
    print("\nVALIDATION OK")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
