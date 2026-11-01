"""Offline frame-accurate animated image decoding, independent of Qt.

Pillow seek() composites GIF disposal and transparency correctly. The returned
RGBA buffers can be safely copied into Qt images in both preview and export.
"""
from __future__ import annotations

from bisect import bisect_right
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageSequence

MAX_FRAMES = 600
MAX_DECODED_BYTES = 256 * 1024 * 1024


def decode_frames(filename: str):
    """Return ([(start_ms, width, height, rgba_bytes), ...], total_duration_ms)."""
    frames = []
    elapsed = 0
    used = 0
    with Image.open(filename) as im:
        count = min(getattr(im, 'n_frames', 1), MAX_FRAMES)
        for i in range(count):
            im.seek(i)
            rgba = im.convert('RGBA')
            width, height = rgba.size
            required = width * height * 4
            if used + required > MAX_DECODED_BYTES:
                break
            data = rgba.tobytes()
            frames.append((elapsed, width, height, data))
            used += required
            delay = im.info.get('duration', 100)
            try:
                delay = int(delay)
            except (ValueError, TypeError):
                delay = 100
            elapsed += max(20, delay)
    return tuple(frames), elapsed


def frame_index(frames, duration_ms, time_s, speed=1.0):
    if not frames:
        return -1
    position = int(max(0, time_s) * max(.01, speed) * 1000) % max(1, duration_ms)
    return max(0, bisect_right([f[0] for f in frames], position) - 1)
