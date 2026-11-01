"""Stateless motion easing and transformation calculations.

All calculations depend only on absolute event timestamps. No animation state,
randomness or wall-clock dependency: preview and video export sample identically.
"""
from __future__ import annotations
import math

ENTRIES = ('Default', 'Spring Pop', 'Elastic Bounce', 'Slide Left', 'Slide Right',
           'Vertical Slam', 'Spin & Stop', 'Zoom Rush', 'Drop Bounce')
EXITS = ('Default Fade', 'Instant', 'Shrink', 'Slide Left', 'Slide Right', 'Spin Away')


def clamp(v: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return min(hi, max(lo, v))


def spring(p: float, oscillations: float = 2.4, damping: float = 7.5) -> float:
    """Underdamped decay from zero to one, settling at precisely one."""
    p = clamp(p)
    if p >= 1: return 1.0
    return 1.0 - math.exp(-damping * p) * math.cos(math.tau * oscillations * p)


def elastic(p: float) -> float:
    p = clamp(p)
    if p == 0 or p == 1: return p
    return 1.0 + pow(2.0, -9.0*p) * math.sin((p - .075) * math.tau * 2.6)


def ease_out(p: float) -> float:
    p = clamp(p)
    return 1.0 - (1.0-p)**3


def transform(entry: str, exit_style: str, local: float, remain: float,
              enter_seconds: float = .40, exit_seconds: float = .28) -> tuple[float,float,float,float,float]:
    """Return x, y, scale, rotation, opacity; translations in screen-width units.

    Entry and exit occupy only the event interval. Caller clips at [start, end).
    """
    duration = max(0.0001, float(enter_seconds))
    x=y=angle=0.0; scale=alpha=1.0
    p = clamp(local / duration)
    if entry == 'Spring Pop': scale = max(.001, spring(p, 1.1, 5.6))
    elif entry == 'Elastic Bounce': scale = max(.001, elastic(p))
    elif entry == 'Slide Left': x = -1.25 * (1-ease_out(p))
    elif entry == 'Slide Right': x = 1.25 * (1-ease_out(p))
    elif entry == 'Vertical Slam': y = -.85 * (1-spring(p, 1.0, 6))
    elif entry == 'Spin & Stop':
        angle = -480.0 * (1-ease_out(p))
        scale = .35 + .65*ease_out(p)
    elif entry == 'Zoom Rush': scale = 3.0 - 2.0*ease_out(p)
    elif entry == 'Drop Bounce': y = -.75*(1-spring(p, 1.6, 6.5))
    out_p=clamp(remain/max(.0001,float(exit_seconds)))
    if exit_style == 'Shrink': scale *= out_p
    elif exit_style == 'Slide Left': x -= .8*(1-ease_out(out_p))
    elif exit_style == 'Slide Right': x += .8*(1-ease_out(out_p))
    elif exit_style == 'Spin Away':
        angle += 270*(1-ease_out(out_p)); scale *= .3+.7*out_p
    # Default Fade is provided by the existing event_fade, and Instant by override.
    return x,y,max(.0001,scale),angle,alpha
