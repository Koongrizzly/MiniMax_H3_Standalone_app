"""Standalone RefMod extraction for GrizzlyMax.
Original RefMod algorithm: Luisa (luisacaotica), ComfyUI-MiniMaxH3Mod.
Adaptation and testing shared by Gary, with ChatGPT collaboration.
Uses the EXISTING GrizzlyMax H3 environment and VAE; no LoRA training.
"""
from pathlib import Path
import sys

def main():
    # Initialize GrizzlyMax's existing embedded-Comfy bootstrap first.
    # It sets up vendor/ paths and the VAE normalization constants.
    from runtime import headless_h3  # noqa: F401
    sibling = Path(__file__).resolve().parent / "refmod_support"
    sys.path.insert(0, str(sibling))
    from extract_mod import main as extract_main
    extract_main()

if __name__ == "__main__":
    main()
