from __future__ import annotations

import subprocess
from pathlib import Path

_GIB_BYTES = 1024 ** 3
_GIB_MIB = 1024.0


def query_primary_gpu_vram():
    """Return (total_gib, free_gib, name) for GPU 0, or None if unavailable."""
    try:
        out = subprocess.check_output(
            [
                "nvidia-smi",
                "--query-gpu=memory.total,memory.free,name",
                "--format=csv,noheader,nounits",
                "--id=0",
            ],
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=2.0,
        ).strip().splitlines()[0]
        parts = [p.strip() for p in out.split(",", 2)]
        total = float(parts[0]) / _GIB_MIB
        free = float(parts[1]) / _GIB_MIB
        name = parts[2] if len(parts) > 2 else "NVIDIA GPU"
        if total <= 0 or free <= 0:
            return None
        return total, free, name
    except Exception:
        return None


def _file_gib(path, fallback: float) -> float:
    try:
        p = Path(path)
        if p.is_file():
            return max(0.01, p.stat().st_size / _GIB_BYTES)
    except Exception:
        pass
    return float(fallback)


def _usable_gpu(gpu):
    if gpu is None:
        return None
    total, free, _name = gpu
    # Keep the final slice of dedicated VRAM out of every native-stage budget and
    # react to memory already occupied by another process.
    return max(0.0, min(total - 1.00, free - 0.50))


def estimate_native_sampling_vram_gib(width: int, height: int, frames: int, mode: str = "fl2va") -> float:
    """Conservative empirical diffusion/sampling estimate for MiniMax-H3 W4A8."""
    width = max(32, int(width))
    height = max(32, int(height))
    frames = max(1, int(frames))
    reference_work = 1920.0 * 1088.0 * 124.0
    work_ratio = (width * height * frames) / reference_work

    model_floor = 11.70
    runtime_at_reference = 9.25
    required = model_floor + runtime_at_reference * work_ratio
    if str(mode).lower() == "ref2va":
        required += 0.60
    return required


def estimate_text_encoder_vram_gib(text_encoder_path=None) -> float:
    """Native Qwen requirement.

    The W4A8 encoder checkpoint is about 17 GiB in this app and Comfy's CLIP
    constructor normally requests a full CUDA load. Add room for model wrappers,
    temporary casts and prompt/vision-token activations.
    """
    weights = _file_gib(text_encoder_path, 17.0)
    return weights * 1.03 + 1.50


def estimate_reference_encode_vram_gib(
    width: int,
    height: int,
    reference_frames: int,
    video_vae_path=None,
    audio_vae_path=None,
    include_audio: bool = False,
) -> float:
    """Estimate peak native reference/keyframe encode pressure.

    MiniMax's video VAE model is only part of the peak; continuation/reference
    frames add transient encode tensors. This stage is isolated from diffusion,
    so it should not inherit diffusion's much larger estimate.
    """
    video_weights = _file_gib(video_vae_path, 5.0)
    width = max(32, int(width))
    height = max(32, int(height))
    reference_frames = max(1, int(reference_frames))
    work = (width * height * reference_frames) / (832.0 * 480.0 * 35.0)
    transient = 2.25 + 2.25 * min(4.0, max(0.10, work) ** 0.55)
    video_required = video_weights * 1.05 + transient
    if include_audio:
        audio_weights = _file_gib(audio_vae_path, 2.0)
        audio_required = audio_weights * 1.08 + 1.50
        return max(video_required, audio_required)
    return video_required


def estimate_video_decode_vram_gib(width: int, height: int, frames: int, video_vae_path=None) -> float:
    """Estimate tiled video-VAE decode peak.

    Spatial tiling bounds the worst activation size, while duration still affects
    temporal/output working tensors. The estimate intentionally remains more
    conservative than the reference encoder estimate.
    """
    weights = _file_gib(video_vae_path, 5.0)
    width = max(32, int(width))
    height = max(32, int(height))
    frames = max(1, int(frames))
    reference_work = 1920.0 * 1088.0 * 243.0
    work_ratio = (width * height * frames) / reference_work
    transient = 3.25 + 3.0 * min(2.5, max(0.05, work_ratio) ** 0.45)
    return weights * 1.05 + transient


def estimate_audio_decode_vram_gib(audio_vae_path=None) -> float:
    weights = _file_gib(audio_vae_path, 2.0)
    return weights * 1.08 + 1.75


def _stage_decision(required: float, usable, label: str, needed: bool = True):
    if not needed:
        return {
            "use_manager": False,
            "needed": False,
            "required_gib": 0.0,
            "reason": f"{label} not used by this job",
        }
    if usable is None:
        return {
            "use_manager": True,
            "needed": True,
            "required_gib": float(required),
            "reason": f"GPU VRAM detection unavailable; failing safe for {label}",
        }
    use_manager = float(required) > float(usable)
    return {
        "use_manager": use_manager,
        "needed": True,
        "required_gib": float(required),
        "reason": (
            f"estimated native peak {required:.2f} GiB "
            f"{'>' if use_manager else '<='} usable dedicated VRAM {usable:.2f} GiB"
        ),
    }


def decide_vram_stages(
    width: int,
    height: int,
    frames: int,
    mode: str = "fl2va",
    *,
    text_encoder_path=None,
    video_vae_path=None,
    audio_vae_path=None,
    reference_needed: bool = False,
    reference_frames: int = 1,
    reference_audio: bool = False,
):
    """Return independent automatic VRAM decisions for every expensive H3 stage."""
    gpu = query_primary_gpu_vram()
    usable = _usable_gpu(gpu)
    if gpu is None:
        total = free = None
        name = "unknown NVIDIA GPU"
    else:
        total, free, name = gpu

    stages = {
        "reference": _stage_decision(
            estimate_reference_encode_vram_gib(
                width, height, reference_frames,
                video_vae_path=video_vae_path,
                audio_vae_path=audio_vae_path,
                include_audio=reference_audio,
            ),
            usable,
            "reference/video encode",
            needed=bool(reference_needed),
        ),
        "text": _stage_decision(
            estimate_text_encoder_vram_gib(text_encoder_path),
            usable,
            "Qwen/text encoder",
            needed=True,
        ),
        "diffusion": _stage_decision(
            estimate_native_sampling_vram_gib(width, height, frames, mode),
            usable,
            "diffusion",
            needed=True,
        ),
        "video_decode": _stage_decision(
            estimate_video_decode_vram_gib(width, height, frames, video_vae_path),
            usable,
            "video VAE decode",
            needed=True,
        ),
        "audio_decode": _stage_decision(
            estimate_audio_decode_vram_gib(audio_vae_path),
            usable,
            "audio VAE decode",
            needed=True,
        ),
    }
    return {
        "gpu_name": name,
        "total_gib": total,
        "free_gib": free,
        "usable_gib": usable,
        "stages": stages,
        "managed_stages": [name for name, d in stages.items() if d.get("use_manager")],
    }


def print_vram_stage_decisions(plan, width: int, height: int, frames: int):
    total = plan.get("total_gib")
    free = plan.get("free_gib")
    usable = plan.get("usable_gib")
    gpu = plan.get("gpu_name") or "GPU"
    if total is not None and free is not None:
        print(
            f"[VRAM-AUTO] per-stage | {width}x{height} {frames}f | {gpu} | "
            f"total={total:.2f} GiB free={free:.2f} GiB usable={usable:.2f} GiB",
            flush=True,
        )
    else:
        print(f"[VRAM-AUTO] per-stage | {width}x{height} {frames}f | {gpu} | VRAM query unavailable", flush=True)

    labels = (
        ("reference", "reference/video encode"),
        ("text", "Qwen/text encoder"),
        ("diffusion", "diffusion"),
        ("video_decode", "video VAE decode"),
        ("audio_decode", "audio VAE decode"),
    )
    for key, label in labels:
        d = plan["stages"][key]
        if not d.get("needed", True):
            state = "N/A"
        else:
            state = "MANAGED" if d["use_manager"] else "NATIVE"
        req = d.get("required_gib", 0.0)
        print(f"[VRAM-AUTO] {label}: {state} | estimate={req:.2f} GiB | {d['reason']}", flush=True)


# Backward-compatible whole-job API for older launchers/tools. The whole-job
# answer now reflects only the diffusion stage; new code should use
# decide_vram_stages() and act on each stage independently.
def decide_vram_manager(width: int, height: int, frames: int, mode: str = "fl2va"):
    required = estimate_native_sampling_vram_gib(width, height, frames, mode)
    gpu = query_primary_gpu_vram()
    usable = _usable_gpu(gpu)
    if gpu is None:
        return {
            "use_manager": True,
            "reason": "GPU VRAM detection unavailable; failing safe to VRAM Lab",
            "required_gib": required,
            "total_gib": None,
            "free_gib": None,
            "usable_gib": None,
            "gpu_name": "unknown NVIDIA GPU",
        }
    total, free, name = gpu
    use_manager = required > usable
    return {
        "use_manager": use_manager,
        "reason": f"estimated native peak {required:.2f} GiB {'>' if use_manager else '<='} usable dedicated VRAM {usable:.2f} GiB",
        "required_gib": required,
        "total_gib": total,
        "free_gib": free,
        "usable_gib": usable,
        "gpu_name": name,
    }


def print_vram_auto_decision(decision, width: int, height: int, frames: int):
    state = "ON" if decision["use_manager"] else "BYPASS"
    total = decision.get("total_gib")
    free = decision.get("free_gib")
    gpu = decision.get("gpu_name") or "GPU"
    detected = (
        f"{gpu} | total={total:.2f} GiB free={free:.2f} GiB"
        if total is not None and free is not None
        else f"{gpu} | VRAM query unavailable"
    )
    print(
        f"[VRAM-AUTO] {state} | {width}x{height} {frames}f | {detected} | {decision['reason']}",
        flush=True,
    )
