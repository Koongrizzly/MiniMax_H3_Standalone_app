"""Vendored Sol-Attn runtime for FrameVision MiniMax H3.

Kernel source derived from Saganaki22/ComfyUI-sol-attn. See LICENSE.
"""

from .runtime import sol_enabled, try_sol_attention

__all__ = ["sol_enabled", "try_sol_attention"]
