"""MiniMax H3 Spectrum feature forecasting for the standalone worker.

This module is a clean-room MiniMax H3 integration built from the published
Spectrum method (Adaptive Spectral Feature Forecasting for Diffusion Sampling
Acceleration) and the native H3 execution contract.

Design goals for this standalone:
- keep only generated target audio/video rows in forecast history;
- store history in system RAM, not VRAM;
- use a conservative one-forecast / one-native-refresh schedule;
- use degree-1 Chebyshev ridge forecasting so 4-step Turbo runs can benefit;
- always keep the final denoise step native;
- fail closed to native execution if topology/timestep assumptions change.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import torch

# Conservative H3 defaults. Degree 1 needs two native anchors, allowing a
# 4-step run to become native/native/forecast/native.
DEGREE = 1
RIDGE_LAMBDA = 0.10
BLEND_WEIGHT = 0.50
MAX_HISTORY = 8
TAIL_ACTUAL_STEPS = 1
MAX_CONSECUTIVE_FORECASTS = 1
CHUNK_BYTES = 32 * 1024 * 1024
MIN_FIT_POINTS = DEGREE + 1


@dataclass(slots=True)
class _Anchor:
    coordinate: float
    feature_flat: torch.Tensor


class MiniMaxH3Spectrum:
    """Forecast H3 target hidden rows at selected denoising evaluations.

    The controller sees one H3 denoiser evaluation at a time. Actual evaluations
    archive the post-transformer target hidden state on CPU. Forecast evaluations
    reconstruct that target state from previous actual anchors and let H3's native
    current-step FinalLayer perform the output projection.
    """

    def __init__(self, total_steps: int, start_step: int = 0, verbose: bool = False):
        self.total_steps = max(1, int(total_steps))
        self.start_step = max(0, int(start_step))
        self.verbose = bool(verbose)

        self.actual_steps = 0
        self.skipped_steps = 0
        self.fallback_steps = 0

        self._history: list[_Anchor] = []
        self._target_shape: Optional[tuple[int, ...]] = None
        self._target_dtype: Optional[torch.dtype] = None
        self._next_eval = 0
        self._current_coordinate = 0.0
        self._current_sigma: Optional[float] = None
        self._previous_sigma: Optional[float] = None
        self._actual: Optional[bool] = None
        self._observed = False
        self._consecutive_forecasts = 0
        self._fallback_reason: Optional[str] = None

    @property
    def forecasting(self) -> bool:
        if self._actual is None:
            raise RuntimeError("Spectrum evaluation has not started")
        return not self._actual

    @property
    def history_length(self) -> int:
        return len(self._history)

    @property
    def fallback_reason(self) -> Optional[str]:
        return self._fallback_reason

    def _force_actual(self, reason: Optional[str] = None) -> None:
        self._actual = True
        if reason:
            self._fallback_reason = reason
            self.fallback_steps += 1

    def begin_step(self, sigma_value: float) -> None:
        if self._actual is not None:
            raise RuntimeError("Spectrum begin_step called before previous evaluation finished")

        step = self._next_eval
        sigma = float(sigma_value)
        self._current_sigma = sigma
        # H3's shifted video sigma is normally [0, 1]. The Chebyshev coordinate
        # is mapped to [-1, 1], while out-of-contract values fail closed.
        if not torch.isfinite(torch.tensor(sigma)) or sigma < -1e-6 or sigma > 1.000001:
            self._current_coordinate = 0.0
            self._force_actual(f"sigma out of expected H3 range: {sigma:g}")
        else:
            sigma = min(1.0, max(0.0, sigma))
            self._current_coordinate = 2.0 * sigma - 1.0

            tail_start = max(0, self.total_steps - TAIL_ACTUAL_STEPS)
            enough_history = len(self._history) >= MIN_FIT_POINTS
            must_refresh = self._consecutive_forecasts >= MAX_CONSECUTIVE_FORECASTS
            in_warmup = step < max(self.start_step, MIN_FIT_POINTS)
            in_tail = step >= tail_start

            # Repeated or reversed sigmas mean the sampler contract is not the
            # simple monotonic one this lightweight integration was designed for.
            monotonic = True
            if self._previous_sigma is not None:
                monotonic = sigma < self._previous_sigma - 1e-8

            if in_warmup or in_tail or not enough_history or must_refresh or not monotonic:
                self._actual = True
                if not monotonic and self._previous_sigma is not None:
                    self._fallback_reason = "non-monotonic/repeated sampler sigma"
            else:
                self._actual = False

        self._observed = False
        if self.verbose:
            mode = "ACTUAL" if self._actual else "FORECAST"
            extra = f" | fallback={self._fallback_reason}" if self._fallback_reason else ""
            print(
                f"[SPECTRUM] eval {step + 1}/{self.total_steps}: {mode} "
                f"| sigma={sigma_value:.6g} | anchors={len(self._history)}{extra}",
                flush=True,
            )

    def observe_target(self, feature: torch.Tensor) -> None:
        if not self._actual or self._observed:
            raise RuntimeError("Spectrum received an unexpected actual H3 target feature")
        if feature.ndim < 2:
            raise RuntimeError("Spectrum H3 target feature has invalid rank")

        shape = tuple(feature.shape)
        if self._target_shape is None:
            self._target_shape = shape
            self._target_dtype = feature.dtype
        elif shape != self._target_shape or feature.dtype != self._target_dtype:
            # Topology changes are unsafe for prediction. Reset history and make
            # this actual result the first anchor of the new topology.
            self._history.clear()
            self._target_shape = shape
            self._target_dtype = feature.dtype
            self._consecutive_forecasts = 0
            self._fallback_reason = "target topology changed; forecast history reset"
            self.fallback_steps += 1

        archived = feature.detach().to(device="cpu", copy=True, non_blocking=False).contiguous().reshape(-1)
        self._history.append(_Anchor(self._current_coordinate, archived))
        if len(self._history) > MAX_HISTORY:
            del self._history[:-MAX_HISTORY]
        self._observed = True

    @staticmethod
    def _design(coords: torch.Tensor) -> torch.Tensor:
        # Degree-1 Chebyshev basis: T0(x)=1, T1(x)=x.
        x = coords.reshape(-1, 1).to(dtype=torch.float32)
        return torch.cat((torch.ones_like(x), x), dim=1)

    def _forecast_weights(self) -> torch.Tensor:
        coords = torch.tensor([a.coordinate for a in self._history], dtype=torch.float32)
        design = self._design(coords)
        eye = torch.eye(DEGREE + 1, dtype=torch.float32)
        gram = design.T @ design + RIDGE_LAMBDA * eye
        phi = self._design(torch.tensor([self._current_coordinate], dtype=torch.float32))
        spectral = (phi @ torch.linalg.solve(gram, design.T)).reshape(-1)

        # Blend the global spectral fit with local linear extrapolation. This is
        # the robust post-publication Spectrum recommendation, but kept mild here.
        local = torch.zeros_like(spectral)
        prev = self._history[-2].coordinate
        last = self._history[-1].coordinate
        denom = last - prev
        ratio = 0.0 if abs(denom) < 1e-8 else (self._current_coordinate - last) / denom
        local[-2] = -ratio
        local[-1] = 1.0 + ratio
        return BLEND_WEIGHT * spectral + (1.0 - BLEND_WEIGHT) * local

    def predict_target(self, device, dtype) -> torch.Tensor:
        if self._actual is not False:
            raise RuntimeError("Spectrum target forecast requested during an actual evaluation")
        if len(self._history) < MIN_FIT_POINTS or self._target_shape is None:
            raise RuntimeError("Spectrum target forecast requested without sufficient H3 anchors")

        weights = [float(v) for v in self._forecast_weights()]
        result = torch.empty(self._target_shape, device=device, dtype=dtype)
        out = result.reshape(-1)
        # Chunked CPU -> GPU reconstruction keeps history off VRAM and bounds
        # temporary float32 storage for long clips.
        float_size = torch.empty((), dtype=torch.float32).element_size()
        chunk = max(1024, CHUNK_BYTES // float_size)
        for offset in range(0, out.numel(), chunk):
            n = min(chunk, out.numel() - offset)
            acc = torch.zeros(n, device=device, dtype=torch.float32)
            for weight, anchor in zip(weights, self._history, strict=True):
                src = anchor.feature_flat.narrow(0, offset, n).to(
                    device=device, dtype=torch.float32, non_blocking=False
                )
                acc.add_(src, alpha=weight)
                del src
            out.narrow(0, offset, n).copy_(acc.to(dtype=dtype))
            del acc
        return result

    def finish_step(self) -> None:
        if self._actual is None:
            raise RuntimeError("Spectrum finish_step called without begin_step")
        if self._actual and not self._observed:
            raise RuntimeError("Spectrum actual H3 evaluation finished without target observation")

        if self._actual:
            self.actual_steps += 1
            self._consecutive_forecasts = 0
        else:
            self.skipped_steps += 1
            self._consecutive_forecasts += 1

        self._previous_sigma = self._current_sigma
        self._next_eval += 1
        self._actual = None
        self._observed = False
        self._fallback_reason = None

    def reset(self) -> None:
        self._history.clear()
        self._target_shape = None
        self._target_dtype = None
        self._actual = None
        self._observed = False
        self._previous_sigma = None
        self._current_sigma = None
        self._consecutive_forecasts = 0

    def summary(self) -> str:
        return (
            f"actual={self.actual_steps} forecast={self.skipped_steps} "
            f"fallbacks={self.fallback_steps} total={self.total_steps}"
        )


__all__ = ["MiniMaxH3Spectrum", "MIN_FIT_POINTS"]
