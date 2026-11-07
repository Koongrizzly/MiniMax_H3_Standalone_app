"""Register bundled Qt fonts in every process that paints BeatSyncFX overlays.

The editor and the GrizzlyMax queue run in different Python processes, so Qt's
application font registry must be populated separately in both processes.
"""
from __future__ import annotations

from PySide6.QtGui import QFontDatabase

from .core import ROOT


BUNDLED_FONTS = ("Anton-Regular.ttf", "Inter-Variable.ttf")


def register_fonts() -> None:
    """Load bundled fonts for this Qt application, failing loudly if unavailable."""
    for name in BUNDLED_FONTS:
        path = ROOT / "assets" / "fonts" / name
        if not path.is_file():
            raise RuntimeError(f"BeatSyncFX font file is missing: {path}")
        font_id = QFontDatabase.addApplicationFont(str(path))
        if font_id < 0 or not QFontDatabase.applicationFontFamilies(font_id):
            raise RuntimeError(f"BeatSyncFX could not register bundled font: {path}")
