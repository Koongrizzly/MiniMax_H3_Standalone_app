from __future__ import annotations

import subprocess

_GIB = 1024.0


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
        total = float(parts[0]) / _GIB
        free = float(parts[1]) / _GIB
        name = parts[2] if len(parts) > 2 else "NVIDIA GPU"
        if total <= 0 or free <= 0:
            return None
        return total, free, name
    except Exception:
        return None


def estimate_native_sampling_vram_gib(width: int, height: int, frames: int, mode: str = "fl2va") -> float:
    """Conservative empirical estimate for this MiniMax-H3 W4A8 standalone.

    Calibrated from the measured RTX 3090 boundary in this project:
      * 1920x1088 / 124f completes faster with VRAM Lab fully bypassed.
      * 1920x1088 / 243f requires managed residency.

    The 11.7 GiB floor represents the W4A8 DiT weights.  Runtime/attention
    memory scales primarily with latent token count, approximated here by
    pixels * requested frames.  This intentionally errs slightly toward
    enabling protection on smaller cards.
    """
    width = max(32, int(width))
    height = max(32, int(height))
    frames = max(1, int(frames))
    reference_work = 1920.0 * 1088.0 * 124.0
    work_ratio = (width * height * frames) / reference_work

    model_floor = 11.70
    runtime_at_reference = 9.25
    required = model_floor + runtime_at_reference * work_ratio

    # Reference conditioning can add transient pressure before/during sampling.
    if str(mode).lower() == "ref2va":
        required += 0.60
    return required


def decide_vram_manager(width: int, height: int, frames: int, mode: str = "fl2va"):
    """Return a decision dict.  Failure to inspect the GPU fails safe to ON."""
    required = estimate_native_sampling_vram_gib(width, height, frames, mode)
    gpu = query_primary_gpu_vram()
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
    # Never budget the final slice of the card.  Also react to VRAM currently
    # occupied by another process instead of assuming the entire card is free.
    usable = max(0.0, min(total - 1.00, free - 0.50))
    use_manager = required > usable
    reason = (
        f"estimated native peak {required:.2f} GiB {'>' if use_manager else '<='} "
        f"usable dedicated VRAM {usable:.2f} GiB"
    )
    return {
        "use_manager": use_manager,
        "reason": reason,
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
