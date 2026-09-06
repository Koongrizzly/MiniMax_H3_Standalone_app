"""Small, fail-open Sol-Attn adapter used by the vendored MiniMax H3 model."""

from __future__ import annotations

import os
import torch

_SOL = None
_IMPORT_ERROR = None
_WARNED = set()
_ACTIVE_LOGGED = False
_SUPPORTED_ARCHES = {(8, 6), (8, 9), (9, 0), (10, 0), (12, 0), (12, 1)}


def sol_enabled() -> bool:
    return os.environ.get("H3_SOL_ATTENTION", "0").strip().lower() in {"1", "true", "yes", "on"}


def _warn_once(reason: str) -> None:
    if reason not in _WARNED:
        _WARNED.add(reason)
        print(f"[Sol-Attn] fallback to existing attention: {reason}", flush=True)


def _load_sol():
    global _SOL, _IMPORT_ERROR
    if _SOL is not None:
        return _SOL
    if _IMPORT_ERROR is not None:
        return None
    try:
        from .sol_kernel import sol_attn
        _SOL = sol_attn
        return _SOL
    except Exception as exc:
        _IMPORT_ERROR = exc
        _warn_once(f"kernel import unavailable ({type(exc).__name__}: {exc})")
        return None


def try_sol_attention(q, k, v, *, video_span=None, min_tokens: int = 4096, tau: float = 1.0):
    """Return a Sol attention result or None so the caller can use its normal backend.

    Inputs are native MiniMax H3 BTHD views. Conditioning KV blocks before the
    video span are forced exact. The conservative FrameVision integration keeps
    BF16 attention math and does not enable Sol's optional INT8 QK/PV paths.
    """
    global _ACTIVE_LOGGED
    if not sol_enabled():
        return None
    if not (torch.is_tensor(q) and torch.is_tensor(k) and torch.is_tensor(v)):
        return None
    if q.ndim != 4 or q.shape != k.shape or q.shape != v.shape:
        return None
    if q.shape[1] < int(min_tokens) or q.shape[-1] != 128:
        return None
    if q.dtype != torch.bfloat16 or k.dtype != torch.bfloat16 or v.dtype != torch.bfloat16:
        return None
    if q.device.type != "cuda" or k.device != q.device or v.device != q.device:
        return None
    try:
        arch = torch.cuda.get_device_capability(q.device)
    except Exception as exc:
        _warn_once(f"could not query CUDA capability ({type(exc).__name__}: {exc})")
        return None
    if arch not in _SUPPORTED_ARCHES:
        _warn_once(f"unsupported GPU SM{arch[0]}{arch[1]}")
        return None

    # Only the main H3 packed sequence publishes a video span. This keeps the
    # short token-refiner attention on Comfy/Sage even if it happens to exceed
    # the token threshold.
    if not video_span or len(video_span) != 2:
        return None
    try:
        video_start, video_stop = int(video_span[0]), int(video_span[1])
    except Exception:
        return None
    if video_start <= 0 or video_stop > q.shape[1] or video_start >= video_stop:
        return None

    sol = _load_sol()
    if sol is None:
        return None
    sink_blocks = (0, (video_start + 63) // 64)
    try:
        out = sol(
            q, k, v,
            tau=float(tau),
            thresh_type="diag",
            int8_qk=False,
            int8_pv=False,
            sink_blocks=sink_blocks,
            sink_q=(0, 0),
        )
    except Exception as exc:
        _warn_once(f"kernel unavailable for this call ({type(exc).__name__}: {exc})")
        return None
    if not _ACTIVE_LOGGED:
        _ACTIVE_LOGGED = True
        print(
            f"[Sol-Attn] active | tokens={q.shape[1]} heads={q.shape[2]} "
            f"head_dim={q.shape[3]} tau={float(tau):g} exact_conditioning_blocks={sink_blocks[1]}",
            flush=True,
        )
    return out
