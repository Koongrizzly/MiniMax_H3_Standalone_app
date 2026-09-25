from __future__ import annotations

import copy
import hashlib
import json
import math
import re
import shutil
import subprocess
import uuid
from pathlib import Path

from PySide6.QtCore import Qt, Signal, QSize, QTimer, QRect, QUrl
from PySide6.QtGui import QColor, QPainter, QPen, QBrush, QFont, QFontMetrics, QImage, QPixmap
from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QFormLayout,
    QGroupBox,
    QLabel,
    QPushButton,
    QFrame,
    QSplitter,
    QSizePolicy,
    QScrollArea,
    QComboBox,
    QSpinBox,
    QDoubleSpinBox,
    QLineEdit,
    QPlainTextEdit,
    QListWidget,
    QListWidgetItem,
    QFileDialog,
    QMessageBox,
    QCheckBox,
    QRadioButton,
    QButtonGroup,
    QSlider,
    QInputDialog,
    QDialog,
    QDialogButtonBox,
    QApplication,
    QMenu,
)

try:
    from PySide6.QtMultimedia import QMediaPlayer, QAudioOutput
    from PySide6.QtMultimediaWidgets import QVideoWidget
except Exception:
    QMediaPlayer = QAudioOutput = QVideoWidget = None

FPS = 24.0
MIN_SEGMENT_SECONDS = 0.3
# Backward-compatible internal alias; project JSON still uses the existing "segments" key.
MIN_CUT_SECONDS = MIN_SEGMENT_SECONDS
TIMELINE_SCHEMA_VERSION = 3


class ClipTrimDialog(QDialog):
    """Non-destructive in/out editor for an existing rendered timeline clip."""

    def __init__(self, video_path: str, duration: float, trim_in: float = 0.0, trim_out: float | None = None, parent=None):
        super().__init__(parent)
        self.video_path = str(video_path)
        self.duration_seconds = max(0.01, float(duration or 0.01))
        self._previewing_range = False
        self._syncing = False
        self.setWindowTitle("Trim clip")
        self.resize(760, 610)

        root = QVBoxLayout(self)
        hint = QLabel("This trim is non-destructive. It changes only playback/assembly; the generated MP4 and generation duration stay unchanged.")
        hint.setWordWrap(True)
        root.addWidget(hint)

        self.video_widget = None
        self.player = None
        self.audio = None
        if QMediaPlayer is not None and QVideoWidget is not None:
            self.video_widget = QVideoWidget(self)
            self.video_widget.setMinimumHeight(330)
            self.video_widget.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
            root.addWidget(self.video_widget, 1)
            self.player = QMediaPlayer(self)
            self.audio = QAudioOutput(self)
            self.player.setAudioOutput(self.audio)
            self.player.setVideoOutput(self.video_widget)
            self.player.setSource(QUrl.fromLocalFile(str(Path(self.video_path).resolve())))
            self.player.positionChanged.connect(self._on_position_changed)
            self.player.durationChanged.connect(self._on_duration_changed)
        else:
            unavailable = QLabel("Video preview is unavailable in this Qt build. Start/end trimming still works.")
            unavailable.setAlignment(Qt.AlignmentFlag.AlignCenter)
            unavailable.setMinimumHeight(180)
            root.addWidget(unavailable, 1)

        self.scrub = QSlider(Qt.Orientation.Horizontal)
        self.scrub.setRange(0, max(1, int(round(self.duration_seconds * 1000))))
        self.scrub.sliderMoved.connect(self._seek_ms)
        root.addWidget(self.scrub)

        time_row = QHBoxLayout()
        self.position_label = QLabel("0.00s")
        self.range_label = QLabel()
        self.range_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        time_row.addWidget(self.position_label)
        time_row.addStretch(1)
        time_row.addWidget(self.range_label)
        root.addLayout(time_row)

        form = QFormLayout()
        self.start_spin = QDoubleSpinBox()
        self.end_spin = QDoubleSpinBox()
        for spin in (self.start_spin, self.end_spin):
            spin.setDecimals(3)
            spin.setSingleStep(0.1)
            spin.setRange(0.0, self.duration_seconds)
            spin.setSuffix(" s")
        initial_in = max(0.0, min(float(trim_in or 0.0), self.duration_seconds))
        initial_out = self.duration_seconds if trim_out is None else max(initial_in, min(float(trim_out), self.duration_seconds))
        self.start_spin.setValue(initial_in)
        self.end_spin.setValue(initial_out)
        self.start_spin.valueChanged.connect(self._range_changed)
        self.end_spin.valueChanged.connect(self._range_changed)
        form.addRow("Start", self.start_spin)
        form.addRow("End", self.end_spin)
        root.addLayout(form)

        controls = QHBoxLayout()
        self.play_btn = QPushButton("Play / Pause")
        self.preview_btn = QPushButton("Preview trim")
        self.set_start_btn = QPushButton("Set start at current")
        self.set_end_btn = QPushButton("Set end at current")
        self.reset_btn = QPushButton("Reset trim")
        controls.addWidget(self.play_btn)
        controls.addWidget(self.preview_btn)
        controls.addWidget(self.set_start_btn)
        controls.addWidget(self.set_end_btn)
        controls.addWidget(self.reset_btn)
        root.addLayout(controls)

        self.play_btn.clicked.connect(self._toggle_play)
        self.preview_btn.clicked.connect(self._preview_range)
        self.set_start_btn.clicked.connect(self._set_start_current)
        self.set_end_btn.clicked.connect(self._set_end_current)
        self.reset_btn.clicked.connect(self._reset_trim)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Apply | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Apply).clicked.connect(self._apply)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)
        self._range_changed()

    def _current_seconds(self):
        return (float(self.player.position()) / 1000.0) if self.player is not None else (float(self.scrub.value()) / 1000.0)

    def _seek_ms(self, value):
        if self.player is not None:
            self.player.setPosition(int(value))
        self.position_label.setText(f"{float(value)/1000.0:.2f}s")

    def _on_duration_changed(self, ms):
        if int(ms or 0) > 0:
            # Prefer the actual player duration if ffprobe rounded slightly differently.
            self.scrub.setMaximum(int(ms))

    def _on_position_changed(self, ms):
        if not self.scrub.isSliderDown():
            self.scrub.setValue(int(ms))
        sec = float(ms) / 1000.0
        self.position_label.setText(f"{sec:.2f}s")
        if self._previewing_range and sec >= float(self.end_spin.value()) - 0.015:
            self.player.pause()
            self.player.setPosition(int(round(float(self.start_spin.value()) * 1000)))
            self._previewing_range = False

    def _range_changed(self, *_args):
        if self._syncing:
            return
        self._syncing = True
        start = float(self.start_spin.value())
        end = float(self.end_spin.value())
        if end < start:
            sender = self.sender()
            if sender is self.start_spin:
                self.end_spin.setValue(start)
                end = start
            else:
                self.start_spin.setValue(end)
                start = end
        self._syncing = False
        used = max(0.0, end - start)
        self.range_label.setText(f"Selected: {start:.3f}s → {end:.3f}s   •   used {used:.3f}s")

    def _toggle_play(self):
        if self.player is None:
            return
        self._previewing_range = False
        if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.player.pause()
        else:
            self.player.play()

    def _preview_range(self):
        if self.player is None:
            return
        if float(self.end_spin.value()) - float(self.start_spin.value()) < 0.04:
            return
        self._previewing_range = True
        self.player.setPosition(int(round(float(self.start_spin.value()) * 1000)))
        self.player.play()

    def _set_start_current(self):
        pos = min(self._current_seconds(), float(self.end_spin.value()))
        self.start_spin.setValue(pos)

    def _set_end_current(self):
        pos = max(self._current_seconds(), float(self.start_spin.value()))
        self.end_spin.setValue(pos)

    def _reset_trim(self):
        self.start_spin.setValue(0.0)
        self.end_spin.setValue(self.duration_seconds)
        if self.player is not None:
            self.player.setPosition(0)

    def _apply(self):
        if float(self.end_spin.value()) - float(self.start_spin.value()) < 0.04:
            QMessageBox.warning(self, "Trim clip", "The selected range is too short. Choose a longer in/out range.")
            return
        self.accept()

    def trim_values(self):
        return float(self.start_spin.value()), float(self.end_spin.value())

    def closeEvent(self, event):
        if self.player is not None:
            self.player.stop()
        super().closeEvent(event)


def _safe_project_name(text: str) -> str:
    text = re.sub(r"[^A-Za-z0-9._ -]+", "_", str(text or "").strip())
    return text or "MiniMax Timeline"


def _clip_seconds(clip: dict) -> float:
    if str(clip.get("generation_mode") or "") == "source":
        try:
            duration = float(clip.get("source_duration_seconds") or 0.0)
            if duration > 0:
                return duration
        except Exception:
            pass
    return max(1, int(clip.get("frames") or 124)) / FPS


def _segment_seconds(clip: dict) -> list[float]:
    segments = clip.get("segments") or []
    if not segments:
        return []
    total = sum(max(0.0001, float(s.get("weight") or 1.0)) for s in segments) or 1.0
    duration = _clip_seconds(clip)
    return [duration * max(0.0001, float(s.get("weight") or 1.0)) / total for s in segments]


def _compiled_prompt(clip: dict) -> str:
    """Compile the repo-style timed-segment model into one H3 prompt.

    One segment stays a plain prompt. Multiple segments become timestamped instructions,
    which is the representation H3 already understands for visible changes inside
    a single generated clip.
    """
    segments = clip.get("segments") or []
    non_empty = [s for s in segments if str(s.get("prompt") or "").strip()]
    if not non_empty:
        return ""
    if len(segments) == 1:
        return str(segments[0].get("prompt") or "").strip()

    seconds = _segment_seconds(clip)
    cursor = 0.0
    lines: list[str] = []
    for seg, span in zip(segments, seconds):
        end = cursor + span
        prompt = str(seg.get("prompt") or "").strip()
        if prompt:
            lines.append(f"[{cursor:.1f}s - {end:.1f}s] {prompt}")
        cursor = end
    return "\n".join(lines).strip()


def _reference_entries(clip: dict) -> list[dict]:
    entries = []
    for item in (clip.get("reference_images") or []):
        if not isinstance(item, dict):
            continue
        path = str(item.get("path") or "").strip()
        if not path:
            continue
        entries.append({"path": path, "name": str(item.get("name") or Path(path).stem).strip()})
    return entries[:5]


def _subject_tokens_present(text: str) -> bool:
    return bool(re.search(r"<\s*Subject\s+\d+\s*>", str(text or ""), flags=re.IGNORECASE))


def _automatic_subject_usage_line(refs: list[dict]) -> str:
    if not refs:
        return ""
    tokens = [f"<Subject {idx}>" for idx in range(1, len(refs) + 1)]
    if len(tokens) == 1:
        return f"Feature {tokens[0]} as the main visible subject in the video."
    if len(tokens) == 2:
        joined = f"{tokens[0]} and {tokens[1]}"
    else:
        joined = ", ".join(tokens[:-1]) + f", and {tokens[-1]}"
    return f"Feature {joined} as visible subject references in the video."


def _compiled_reference_prompt(clip: dict) -> str:
    """Return the H3 prompt with stable Subject->Picture definitions for Ref2VA.

    MiniMax receives reference images as <Picture N> in input order.  The user-facing
    timeline uses <Subject N> for reusable visible content, matching MiniMax's official
    full-reference prompt guide.

    If the authored prompt body never mentions any <Subject N> token, automatically add
    one helper usage line so Ref2VA references still participate instead of being defined
    but never invoked.
    """
    body = _compiled_prompt(clip)
    if not bool(clip.get("use_reference_images", False)):
        return body
    refs = _reference_entries(clip)
    if not refs:
        return body
    defs = []
    for idx, ref in enumerate(refs, 1):
        friendly = str(ref.get("name") or f"Reference {idx}").strip() or f"Reference {idx}"
        defs.append(f'<Subject {idx}> is the reusable visible content named "{friendly}" from <Picture {idx}>.')
    auto_use = "" if _subject_tokens_present(body) else _automatic_subject_usage_line(refs)
    parts = defs + ([auto_use] if auto_use else []) + ([body] if body else [])
    return "\n".join(parts).strip()


class TimelineCanvas(QWidget):
    """Compact NLE-like overview of MiniMax H3 generation jobs."""

    clipSelected = Signal(str)  # legacy/plain selection compatibility
    clipSelectionRequested = Signal(str, int)
    clipsReordered = Signal(int, int)
    clipFramesChanged = Signal(str, int)
    clipContextMenuRequested = Signal(str, object)

    RULER_H = 30
    CLIP_TOP = 40
    CLIP_H = 116
    BOTTOM_PAD = 16
    THUMB_W = 46
    THUMB_H = 36

    def __init__(self, parent=None):
        super().__init__(parent)
        self.clips: list[dict] = []
        self.selected_id: str | None = None
        self.selected_ids: set[str] = set()
        self.pixels_per_second = 38.0
        self.allowed_frames: list[int] = list(range(124, 720, 17))
        self._rects: list[tuple[str, float, float]] = []
        self._press_x = 0.0
        self._press_clip_index = -1
        self._dragging = False
        self._resizing = False
        self._thumb_cache: dict[str, QImage | None] = {}
        self._video_meta_cache: dict[str, dict] = {}
        self._thumb_cache_dir = Path(__file__).resolve().parent / "_timeline_thumb_cache"
        self.setMinimumHeight(self.CLIP_TOP + self.CLIP_H + self.BOTTOM_PAD)
        self.setMouseTracking(True)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def sizeHint(self):
        return QSize(max(900, self._content_width()), self.CLIP_TOP + self.CLIP_H + self.BOTTOM_PAD)

    def _content_width(self) -> int:
        total = sum(_clip_seconds(c) for c in self.clips)
        return int(max(900, 36 + total * self.pixels_per_second + 36))

    def set_clips(self, clips: list[dict], selected_id: str | None = None, selected_ids=None):
        self.clips = clips
        self.selected_id = selected_id
        if selected_ids is None:
            self.selected_ids = {str(selected_id)} if selected_id else set()
        else:
            self.selected_ids = {str(x) for x in selected_ids if x}
        self.setMinimumWidth(self._content_width())
        self.resize(self._content_width(), self.minimumHeight())
        self.update()

    def set_zoom(self, pixels_per_second: float):
        self.pixels_per_second = max(16.0, min(120.0, float(pixels_per_second)))
        self.setMinimumWidth(self._content_width())
        self.resize(self._content_width(), self.minimumHeight())
        self.update()

    def set_allowed_frames(self, values):
        vals = sorted({int(v) for v in (values or []) if int(v) > 0})
        if vals:
            self.allowed_frames = vals

    def _clip_at(self, x: float):
        for idx, (_, left, right) in enumerate(self._rects):
            if left <= x <= right:
                return idx, left, right
        return -1, 0.0, 0.0

    def _nearest_allowed_frames(self, wanted: int) -> int:
        if not self.allowed_frames:
            return max(1, int(wanted))
        return min(self.allowed_frames, key=lambda v: abs(v - wanted))

    def _ffmpeg_path(self) -> str:
        # The timeline helper can be imported from several different launch
        # locations, so do not rely on a single package-relative ffmpeg lookup.
        try:
            from runtime.ffmpeg_tools import tool_path as ffmpeg_tool_path
            for candidate in ("ffmpeg.exe", "ffmpeg"):
                try:
                    resolved = ffmpeg_tool_path(candidate)
                except Exception:
                    resolved = None
                if resolved:
                    path = Path(str(resolved))
                    if path.is_file():
                        return str(path)
        except Exception:
            pass

        found = shutil.which("ffmpeg.exe") or shutil.which("ffmpeg")
        if found:
            return found

        here = Path(__file__).resolve().parent
        roots = [here, *list(here.parents)[:5]]
        relative_candidates = (
            Path("presets/bin/ffmpeg.exe"),
            Path("presets/bin/ffmpeg"),
            Path("bin/ffmpeg.exe"),
            Path("bin/ffmpeg"),
            Path("ffmpeg.exe"),
            Path("ffmpeg"),
        )
        for root in roots:
            for rel in relative_candidates:
                candidate = root / rel
                if candidate.is_file():
                    return str(candidate)
        return ""

    def _ffprobe_path(self) -> str:
        try:
            from runtime.ffmpeg_tools import tool_path as ffmpeg_tool_path
            for candidate in ("ffprobe.exe", "ffprobe"):
                try:
                    resolved = ffmpeg_tool_path(candidate)
                except Exception:
                    resolved = None
                if resolved:
                    path = Path(str(resolved))
                    if path.is_file():
                        return str(path)
        except Exception:
            pass

        found = shutil.which("ffprobe.exe") or shutil.which("ffprobe")
        if found:
            return found

        here = Path(__file__).resolve().parent
        roots = [here, *list(here.parents)[:5]]
        for root in roots:
            for rel in (
                Path("presets/bin/ffprobe.exe"), Path("presets/bin/ffprobe"),
                Path("bin/ffprobe.exe"), Path("bin/ffprobe"),
                Path("ffprobe.exe"), Path("ffprobe"),
            ):
                candidate = root / rel
                if candidate.is_file():
                    return str(candidate)
        return ""

    @staticmethod
    def _resolution_height_from_text(value) -> int:
        text = str(value or "")
        # Covers values such as "1280 × 704", "1280x704" and preset labels
        # such as "High — 1792 × 768". Use the last WxH pair in the string.
        pairs = re.findall(r"(\d{2,5})\s*[x×X]\s*(\d{2,5})", text)
        if not pairs:
            return 0
        try:
            return int(pairs[-1][1])
        except Exception:
            return 0

    def _clip_rendered_height(self, clip: dict) -> int:
        """Best-effort height of the *active rendered clip*.

        Prefer the actual MP4/image dimensions, cached by file timestamp. This
        makes the HQ badge truthful for older projects too instead of relying on
        whether the clip happened to be produced through the HQ Restart button.
        Saved queue metadata is used only as a fallback.
        """
        path = self._thumbnail_source_path(clip)
        if path is not None:
            try:
                stat = path.stat()
                key = f"{path}|{int(stat.st_mtime_ns)}|{int(stat.st_size)}"
            except Exception:
                key = str(path)
            cached = self._video_meta_cache.get(key)
            if cached is not None:
                return int(cached.get("height") or 0)

            height = 0
            if path.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp", ".bmp"}:
                try:
                    img = QImage(str(path))
                    if not img.isNull():
                        height = int(img.height())
                except Exception:
                    height = 0
            else:
                ffprobe = self._ffprobe_path()
                if ffprobe:
                    try:
                        cp = subprocess.run(
                            [ffprobe, "-v", "error", "-select_streams", "v:0",
                             "-show_entries", "stream=height", "-of", "json", str(path)],
                            capture_output=True, text=True, timeout=8,
                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                        )
                        if cp.returncode == 0:
                            data = json.loads(cp.stdout or "{}")
                            stream = (data.get("streams") or [{}])[0]
                            height = int(stream.get("height") or 0)
                    except Exception:
                        height = 0
            self._video_meta_cache[key] = {"height": int(height)}
            if height:
                return int(height)

        generated = clip.get("last_generation_info") if isinstance(clip.get("last_generation_info"), dict) else {}
        for value in (
            generated.get("resolution"),
            (generated.get("settings") or {}).get("resolution") if isinstance(generated.get("settings"), dict) else None,
            (generated.get("settings") or {}).get("widescreen_quality") if isinstance(generated.get("settings"), dict) else None,
            (clip.get("settings") or {}).get("resolution") if isinstance(clip.get("settings"), dict) else None,
            (clip.get("settings") or {}).get("widescreen_quality") if isinstance(clip.get("settings"), dict) else None,
        ):
            height = self._resolution_height_from_text(value)
            if height:
                return height
        return 0

    @staticmethod
    def _actual_generation_settings(clip: dict) -> dict:
        generated = clip.get("last_generation_info") if isinstance(clip.get("last_generation_info"), dict) else {}
        saved = generated.get("settings") if isinstance(generated.get("settings"), dict) else None
        if isinstance(saved, dict):
            return saved
        settings = clip.get("settings")
        return settings if isinstance(settings, dict) else {}

    def _status_badges(self, idx: int, clip: dict) -> tuple[list[str], list[str]]:
        """Return compact left/right badge groups for the active clip."""
        left: list[str] = []
        right: list[str] = []
        settings = self._actual_generation_settings(clip)

        # HQ means actual 704p-or-higher output, independent of how it was made.
        if self._clip_rendered_height(clip) >= 704:
            left.append("HQ")

        # Provenance comes from the settings actually saved with the render when
        # available. Timeline continuation normally uses both latent history and
        # the exact final RGB frame, hence L and F may intentionally coexist.
        generated = clip.get("last_generation_info") if isinstance(clip.get("last_generation_info"), dict) else {}
        latent_used = bool(settings.get("latent_continuation") or generated.get("latent_continuation"))
        if latent_used:
            left.append("L")

        frame_used = bool(
            settings.get("last") or settings.get("first") or
            settings.get("continue_video") or settings.get("continue_last_result") or
            str(clip.get("generation_mode") or "") == "continue"
        )
        if frame_used:
            left.append("F")

        audio_used = bool(
            settings.get("continue_audio_memory") or settings.get("ref_audios") or
            settings.get("audio") or settings.get("audio_path") or settings.get("input_audio")
        )
        if audio_used:
            left.append("A")

        has_refs = bool(clip.get("use_reference_images", False)) and bool(_reference_entries(clip))
        if has_refs:
            right.append("Ref")

        broken = bool(clip.get("stale", False))
        mode = str(clip.get("generation_mode") or "")
        if mode == "continue":
            if idx <= 0:
                broken = True
            else:
                prev = self.clips[idx - 1]
                prev_path = self._thumbnail_source_path(prev)
                if bool(prev.get("stale", False)) or prev_path is None:
                    broken = True
        elif mode == "source":
            if self._thumbnail_source_path(clip) is None:
                broken = True
        if broken:
            right.insert(0, "⚠")
        return left, right

    def _thumbnail_source_path(self, clip: dict) -> Path | None:
        source = str(clip.get("output") or clip.get("start_source_video") or "").strip()
        if not source:
            return None
        path = Path(source).expanduser()
        if path.is_file():
            return path.resolve()
        # Older/saved timeline projects can contain relative result paths.
        for base in (Path.cwd(), Path(__file__).resolve().parent):
            candidate = (base / path).resolve()
            if candidate.is_file():
                return candidate
        return None

    @staticmethod
    def _load_thumbnail_image(path: Path) -> QImage | None:
        # Keep the timeline renderer entirely on QImage/raster data. QPixmap is
        # optimized for screen resources and proved unreliable here even though
        # Qt reported the extracted thumbnail as valid.
        try:
            data = path.read_bytes()
            if not data:
                return None
            image = QImage.fromData(data)
            if image.isNull() or image.width() < 2 or image.height() < 2:
                return None
            return image.convertToFormat(QImage.Format.Format_RGB32)
        except Exception:
            return None

    def _clip_thumbnail(self, clip: dict) -> QImage | None:
        path = self._thumbnail_source_path(clip)
        if path is None:
            return None
        try:
            stamp = int(path.stat().st_mtime_ns)
            file_size = int(path.stat().st_size)
        except Exception:
            stamp = 0
            file_size = 0

        # v4: keep decoded thumbnail pixels entirely in memory.  Previous
        # versions went through PNG/JPEG/BMP files and Qt image plugins; on the
        # user's Windows runtime those images reported as valid but painted as
        # empty rectangles.  FFmpeg now emits an exact THUMB_W x THUMB_H RGB24
        # frame to stdout and QImage owns a copied raster buffer.
        cache_key = f"v4raw|{path}|{stamp}|{file_size}|{self.THUMB_W}x{self.THUMB_H}"
        if cache_key in self._thumb_cache:
            return self._thumb_cache[cache_key]

        image: QImage | None = None
        if path.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp", ".bmp"}:
            loaded = self._load_thumbnail_image(path)
            if loaded is not None:
                image = loaded.scaled(
                    self.THUMB_W, self.THUMB_H,
                    Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                    Qt.TransformationMode.SmoothTransformation,
                ).copy(0, 0, self.THUMB_W, self.THUMB_H)
        else:
            ffmpeg = self._ffmpeg_path()
            if ffmpeg:
                try:
                    vf = (
                        f"scale={self.THUMB_W}:{self.THUMB_H}:"
                        "force_original_aspect_ratio=decrease,"
                        f"pad={self.THUMB_W}:{self.THUMB_H}:(ow-iw)/2:(oh-ih)/2:color=black"
                    )
                    proc = subprocess.run(
                        [
                            ffmpeg, "-hide_banner", "-loglevel", "error",
                            "-ss", "0.35", "-i", str(path), "-an",
                            "-frames:v", "1", "-vf", vf,
                            "-pix_fmt", "rgb24", "-f", "rawvideo", "pipe:1",
                        ],
                        capture_output=True,
                        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                    )
                    expected = self.THUMB_W * self.THUMB_H * 3
                    if proc.returncode == 0 and len(proc.stdout) >= expected:
                        raw = proc.stdout[:expected]
                        # QImage initially references Python's buffer; .copy() is
                        # essential so the pixels remain valid after this method
                        # returns and the subprocess/output objects are released.
                        wrapped = QImage(
                            raw, self.THUMB_W, self.THUMB_H, self.THUMB_W * 3,
                            QImage.Format.Format_RGB888,
                        )
                        if not wrapped.isNull():
                            image = wrapped.copy()
                except Exception:
                    image = None

        self._thumb_cache[cache_key] = image
        return image

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        palette = self.palette()
        bg = palette.color(palette.ColorRole.Base)
        panel = palette.color(palette.ColorRole.AlternateBase)
        text = palette.color(palette.ColorRole.Text)
        muted = palette.color(palette.ColorRole.PlaceholderText)
        border = palette.color(palette.ColorRole.Mid)
        accent = palette.color(palette.ColorRole.Highlight)
        accent_text = palette.color(palette.ColorRole.HighlightedText)
        painter.fillRect(self.rect(), bg)

        total_seconds = max(1.0, sum(_clip_seconds(c) for c in self.clips))
        ruler_step = 1
        if self.pixels_per_second < 25:
            ruler_step = 10
        elif self.pixels_per_second < 45:
            ruler_step = 5
        elif self.pixels_per_second < 80:
            ruler_step = 2
        x0 = 26.0
        painter.setPen(QPen(border, 1))
        painter.drawLine(int(x0), self.RULER_H - 3, self.width() - 10, self.RULER_H - 3)
        font = painter.font()
        fm = QFontMetrics(font)
        for sec in range(0, int(math.ceil(total_seconds)) + ruler_step, ruler_step):
            x = x0 + sec * self.pixels_per_second
            painter.drawLine(int(x), self.RULER_H - 8, int(x), self.RULER_H - 2)
            label = f"{sec // 60}:{sec % 60:02d}" if sec >= 60 else f"{sec}s"
            painter.setPen(muted)
            painter.drawText(int(x + 3), 16, label)
            painter.setPen(border)

        self._rects = []
        cursor = x0
        cut_palette = [
            QColor("#375a7f"), QColor("#5b4b8a"), QColor("#476c5e"),
            QColor("#72553d"), QColor("#6a4f67"), QColor("#465c78"),
        ]
        state_colors = {
            "finished": QColor("#2f6b4f"),
            "running": QColor("#236b84"),
            "pending": QColor("#5c6470"),
            "failed": QColor("#783c3c"),
            "cancelled": QColor("#6b5353"),
            "stale": QColor("#806528"),
            "draft": panel,
        }

        for idx, clip in enumerate(self.clips):
            duration = _clip_seconds(clip)
            width = max(74.0, duration * self.pixels_per_second)
            left, right = cursor, cursor + width
            self._rects.append((str(clip.get("id")), left, right))
            clip_id = str(clip.get("id"))
            selected = clip_id in self.selected_ids or clip_id == self.selected_id
            primary_selected = clip_id == self.selected_id
            status = "stale" if clip.get("stale") else str(clip.get("status") or "draft")
            fill = state_colors.get(status, panel)
            painter.setBrush(QBrush(fill))
            painter.setPen(QPen(accent if selected else border, 3 if primary_selected else (2 if selected else 1)))
            painter.drawRoundedRect(int(left), self.CLIP_TOP, int(width - 4), self.CLIP_H, 5, 5)

            # Clip header.
            painter.setPen(accent_text if selected else text)
            name = str(clip.get("name") or f"Clip {idx + 1}")
            name_width = int(max(20, width - (58 if bool(clip.get("locked", False)) else 18)))
            info_width = int(max(20, width - 18))
            elided = fm.elidedText(name, Qt.TextElideMode.ElideRight, name_width)
            painter.drawText(int(left + 8), self.CLIP_TOP + 18, elided)
            if bool(clip.get("locked", False)):
                lock_font = QFont(font)
                lock_font.setPointSize(max(7, font.pointSize() - 2))
                lock_font.setBold(True)
                painter.setFont(lock_font)
                lock_text = "LOCK"
                lock_w = painter.fontMetrics().horizontalAdvance(lock_text)
                painter.setPen(QColor("#f4d35e"))
                painter.drawText(int(right - lock_w - 10), self.CLIP_TOP + 18, lock_text)
                painter.setFont(font)
            if clip.get("generation_mode") == "source":
                mode = "▣ Loaded start clip"
            else:
                mode = "↪ Continue" if clip.get("generation_mode") == "continue" else "◆ New"
            edit_mode = str(clip.get("edit_mode") or "")
            bridge = "  •  ⇥ Next frame" if edit_mode in {"bridge_both", "anchor_next"} else ""
            ref_count = len(_reference_entries(clip)) if bool(clip.get("use_reference_images", False)) else 0
            refs_info = f"  •  Ref2VA ×{ref_count}" if ref_count else ""
            info = f"{mode}  •  {int(clip.get('frames') or 0)}f  •  {duration:.2f}s{bridge}{refs_info}"
            painter.setPen(muted if not selected else accent_text)
            painter.drawText(int(left + 8), self.CLIP_TOP + 38, fm.elidedText(info, Qt.TextElideMode.ElideRight, info_width))

            # Show a compact prompt preview. If an output clip already exists, also
            # draw a small thumbnail so timeline blocks are visually recognizable.
            prompt_y = self.CLIP_TOP + 53
            prompt_h = 42
            box_left = int(left + 5)
            box_w = max(1, int(width - 14))
            painter.setBrush(QBrush(QColor("#375a7f")))
            painter.setPen(QPen(bg, 1))
            painter.drawRect(box_left, prompt_y, box_w, prompt_h)
            if clip.get("generation_mode") == "source":
                source = str(clip.get("start_source_video") or clip.get("output") or "").strip()
                prompt = Path(source).name if source else "No start video loaded"
            else:
                prompt = _compiled_prompt(clip).replace("\n", " ").strip() or "Empty prompt"

            text_left = box_left + 6
            text_w = max(12, box_w - 12)
            image = self._clip_thumbnail(clip)
            if image is not None and box_w >= 70:
                thumb_rect = QRect(box_left + 4, prompt_y + 3, self.THUMB_W, self.THUMB_H)
                painter.fillRect(thumb_rect, QColor("#223548"))
                # The v4 extractor already returns an exact THUMB_W x THUMB_H
                # raster. Paint it 1:1: no Qt-side resize and no source-rect
                # overloads are involved.
                painter.drawImage(thumb_rect.x(), thumb_rect.y(), image)
                painter.setPen(QPen(QColor("#8ea7bf"), 1))
                # drawRect() uses both the current pen and brush. The prompt
                # preview left a solid blue brush active, which was filling this
                # rectangle *after* drawImage() and hiding the thumbnail.
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawRect(thumb_rect)
                text_left = thumb_rect.right() + 8
                text_w = max(12, box_left + box_w - text_left - 6)

                # Compact status/provenance badges below the thumbnail. They
                # describe the active rendered clip rather than merely the current
                # editor settings, so old clips remain truthful after later edits.
                badge_font = QFont(font)
                badge_font.setPointSize(max(7, font.pointSize() - 2))
                badge_font.setBold(True)
                painter.setFont(badge_font)
                badge_y = prompt_y + prompt_h + 13
                left_badges, right_badges = self._status_badges(idx, clip)
                painter.setPen(QColor("#f4f6f8"))
                if left_badges:
                    painter.drawText(thumb_rect.left(), badge_y, " ".join(left_badges))
                if right_badges:
                    right_text = " ".join(right_badges)
                    right_w = painter.fontMetrics().horizontalAdvance(right_text)
                    painter.drawText(thumb_rect.right() - right_w + 1, badge_y, right_text)
                painter.setFont(font)

            painter.setPen(QColor("#f4f6f8"))
            text_rect = QRect(text_left, prompt_y + 2, text_w, prompt_h - 4)
            painter.drawText(text_rect, int(Qt.AlignmentFlag.AlignVCenter | Qt.TextFlag.TextSingleLine), fm.elidedText(prompt, Qt.TextElideMode.ElideRight, text_w))

            if clip.get("generation_mode") == "continue" and idx > 0:
                painter.setPen(QPen(accent, 2))
                painter.drawLine(int(left - 12), self.CLIP_TOP + 20, int(left - 3), self.CLIP_TOP + 20)
                painter.drawLine(int(left - 7), self.CLIP_TOP + 16, int(left - 3), self.CLIP_TOP + 20)
                painter.drawLine(int(left - 7), self.CLIP_TOP + 24, int(left - 3), self.CLIP_TOP + 20)

            cursor = right

        if not self.clips:
            painter.setPen(muted)
            painter.drawText(28, self.CLIP_TOP + 50, "No clips yet — add a generation clip to start the timeline.")

    def contextMenuEvent(self, event):
        x = float(event.pos().x())
        idx, _left, _right = self._clip_at(x)
        if idx < 0:
            return super().contextMenuEvent(event)
        clip_id = str(self.clips[idx].get("id") or "")
        if not clip_id:
            return
        self.clipContextMenuRequested.emit(clip_id, event.globalPos())
        event.accept()

    def mousePressEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton:
            return super().mousePressEvent(event)
        x = event.position().x()
        idx, left, right = self._clip_at(x)
        if idx < 0:
            return
        clip = self.clips[idx]
        clip_id = str(clip.get("id"))
        modifiers = event.modifiers()
        try:
            modifier_value = int(modifiers.value)
        except Exception:
            modifier_value = int(modifiers)
        self.clipSelectionRequested.emit(clip_id, modifier_value)

        # Ctrl/Shift clicks are selection gestures, not resize/reorder gestures.
        # Locked clips can be selected but cannot be structurally dragged/resized.
        modified_click = modifiers != Qt.KeyboardModifier.NoModifier
        locked = bool(clip.get("locked", False))
        self._press_x = x
        self._press_clip_index = -1 if modified_click or locked else idx
        self._dragging = False
        self._resizing = (
            not modified_click and not locked and (right - x) <= 10
            and len(self.clips) > 0
            and str(clip.get("generation_mode") or "") != "source"
        )
        self.update()

    def mouseMoveEvent(self, event):
        if self._press_clip_index < 0 or not (event.buttons() & Qt.MouseButton.LeftButton):
            return super().mouseMoveEvent(event)
        x = event.position().x()
        if self._resizing:
            clip = self.clips[self._press_clip_index]
            _, left, _ = self._rects[self._press_clip_index]
            seconds = max(1.0, (x - left) / self.pixels_per_second)
            wanted = int(round(seconds * FPS))
            frames = self._nearest_allowed_frames(wanted)
            if frames != int(clip.get("frames") or 0):
                clip["frames"] = frames
                self.clipFramesChanged.emit(str(clip.get("id")), frames)
                self.set_clips(self.clips, self.selected_id)
            return
        if abs(x - self._press_x) > 6:
            self._dragging = True
            self.setCursor(Qt.CursorShape.ClosedHandCursor)

    def mouseReleaseEvent(self, event):
        try:
            if event.button() == Qt.MouseButton.LeftButton and self._dragging and self._press_clip_index >= 0:
                x = event.position().x()
                target = len(self.clips) - 1
                for i, (_, left, right) in enumerate(self._rects):
                    if x < (left + right) / 2:
                        target = i
                        break
                if target != self._press_clip_index:
                    self.clipsReordered.emit(self._press_clip_index, target)
        finally:
            self._press_clip_index = -1
            self._dragging = False
            self._resizing = False
            self.unsetCursor()
            self.update()


class TimelineTab(QWidget):
    """MiniMax H3 long-form planning/generation timeline.

    Director's reusable structure is intentionally retained here: a project owns
    generation chunks, each chunk owns proportional prompt segments, and segment duration edits
    redistribute weight while keeping the generation length fixed. FrameVision's
    existing H3 pipeline remains authoritative for sampling and continuation.
    """

    def __init__(
        self,
        parent=None,
        *,
        settings_provider=None,
        queue_timeline_callback=None,
        assemble_timeline_callback=None,
        preview_result_callback=None,
        open_output_callback=None,
        frame_values=None,
    ):
        super().__init__(parent)
        self.setObjectName("minimaxTimelineTab")
        self.setMinimumWidth(0)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.settings_provider = settings_provider
        self.queue_timeline_callback = queue_timeline_callback
        self.assemble_timeline_callback = assemble_timeline_callback
        self.preview_result_callback = preview_result_callback
        self.open_output_callback = open_output_callback
        self.frame_values = sorted({int(v) for v in (frame_values or range(124, 720, 17))})
        self.project = self._new_project_data()
        self.selected_clip_id: str | None = None
        self.selected_clip_ids: set[str] = set()
        self._selection_anchor_id: str | None = None
        self.selected_cut_index = 0
        self._loading_inspector = False
        self._project_path: Path | None = None

        # Session-wide Timeline edit history. Snapshots contain the complete project
        # model (including clip output paths/job ids), so Undo/Redo restores the
        # rendered clip version that belonged to that edit state instead of only
        # restoring the visible prompt/settings widgets. Video files themselves are
        # not duplicated; the Timeline already keeps previous renders on disk.
        self._undo_stack: list[dict] = []
        self._redo_stack: list[dict] = []
        self._history_restoring = False
        self._history_coalesce_key: str | None = None
        self._history_coalesce_timer = QTimer(self)
        self._history_coalesce_timer.setSingleShot(True)
        self._history_coalesce_timer.setInterval(700)
        self._history_coalesce_timer.timeout.connect(self._end_history_coalesce)

        # Timeline recovery paths must exist before _build_ui/_refresh_all because
        # the summary tooltip already refers to the recovery location during the
        # first refresh at startup.
        module_dir = Path(__file__).resolve().parent
        app_root = module_dir.parent if module_dir.name.lower() == "helpers" else module_dir
        self._timeline_root = app_root / "output" / "timeline"
        self._timeline_root.mkdir(parents=True, exist_ok=True)
        self._autosave_temp_path = self._timeline_root / "minimax_timeline_autosave_temp.json"
        # Share the application's persistent QFileDialog history file so the
        # Timeline Load dialog opens where the user last loaded a project, even
        # after GrizzlyMax has been restarted.
        self._file_dialog_history_path = app_root / "presets" / "setsave" / "minimax_file_dialog_history.json"

        self._build_ui()
        self._ensure_initial_clip()
        self._refresh_all(select_first=True)

        self._autosave_timer = QTimer(self)
        self._autosave_timer.setInterval(60_000)
        self._autosave_timer.timeout.connect(self._autosave_project)
        self._autosave_timer.start()

    # ------------------------------------------------------------------ model
    def _new_project_data(self):
        return {
            "schema_version": TIMELINE_SCHEMA_VERSION,
            "project_id": uuid.uuid4().hex,
            "name": "MiniMax Timeline",
            "project_folder": "",
            "clips": [],
            "assembled_output": "",
            "assembly_status": "",
            "auto_assemble": False,
            "auto_assemble_pending": False,
            "global_generation_settings": {},
            "hq_restart_override": {},
        }

    def _capture_settings(self):
        if callable(self.settings_provider):
            try:
                data = self.settings_provider()
                captured = copy.deepcopy(data) if isinstance(data, dict) else {}
                # Timeline always owns final assembly. Standalone FL2VA Glue is
                # intentionally ignored here so a Generation-tab checkbox can
                # never become a stale/fantasy Timeline setting.
                captured["glue_results"] = False
                return captured
            except Exception:
                return {}
        return {}

    def _timeline_global_settings(self, raw=None):
        """Return Generation-tab settings safe to use as a Timeline-wide baseline.

        Timeline owns prompt text, clip duration, Ref2VA inputs and continuation
        topology. Those fields must never be overwritten by the global settings
        capture button. Everything else (model, LoRAs, resolution, sampler,
        scheduler, seed, steps, VRAM/runtime options, etc.) remains global.
        """
        settings = copy.deepcopy(raw if isinstance(raw, dict) else self._capture_settings())
        for key in (
            "prompt", "frames", "experimental_long_duration",
            "ref_images", "ref_videos", "ref_audios",
            "first", "last", "continue_video", "continue_last_result",
        ):
            settings.pop(key, None)
        # Timeline decides T2VA / FL2VA / Ref2VA per block at queue time.
        settings["mode"] = 0
        settings["glue_results"] = False
        return settings

    def _blank_clip(self, *, continue_previous=False, source_settings=None):
        global_settings = self.project.get("global_generation_settings") if isinstance(getattr(self, "project", None), dict) else {}
        if isinstance(global_settings, dict) and global_settings:
            settings = copy.deepcopy(global_settings)
        elif isinstance(source_settings, dict):
            settings = copy.deepcopy(source_settings)
        else:
            settings = self._timeline_global_settings()
        # Duration belongs to the Timeline block itself. Global Generation settings
        # never get to change it. New blocks use the existing Timeline default.
        frames = 243
        frames = min(self.frame_values, key=lambda v: abs(v - frames)) if self.frame_values else frames
        prompt = ""
        # Timeline reference images are owned by each clip, not implicitly copied
        # from whatever happens to be loaded on the Generation tab.
        settings["ref_images"] = []
        settings["ref_videos"] = []
        settings["ref_audios"] = []
        settings["glue_results"] = False
        return {
            "id": uuid.uuid4().hex,
            "name": "",
            "frames": frames,
            "generation_mode": "continue" if continue_previous else "new",
            "start_source_video": "",
            "source_duration_seconds": 0.0,
            "edit_mode": "continue_previous" if continue_previous else "standalone",
            "match_next_first_frame": False,  # legacy compatibility; edit workflow owns this now
            "use_reference_images": False,
            "reference_images": [],
            "segments": [{"id": uuid.uuid4().hex, "prompt": prompt, "weight": 1.0}],
            "settings": settings,
            "status": "draft",
            "stale": False,
            "queue_job_id": None,
            "output": "",
            "hq_generated": False,
            "locked": False,
            "notes": "",
            "last_generation_info": {},
        }

    def _clips(self):
        return self.project.setdefault("clips", [])

    def _ensure_initial_clip(self):
        if not self._clips():
            clip = self._blank_clip(continue_previous=False)
            clip["name"] = "Clip 1"
            self._clips().append(clip)
            self.selected_clip_id = clip["id"]
            self.selected_clip_ids = {str(clip["id"])}
            self._selection_anchor_id = str(clip["id"])

    def _selected_index(self):
        for i, c in enumerate(self._clips()):
            if str(c.get("id")) == str(self.selected_clip_id):
                return i
        return -1

    def _selected_clip(self):
        idx = self._selected_index()
        return self._clips()[idx] if idx >= 0 else None

    def _invalidate_assembly(self, message="Timeline changed — assemble again after generation."):
        self.project["assembled_output"] = ""
        self.project["assembly_status"] = str(message or "")
        self.project["auto_assemble_pending"] = False

    def _auto_assemble_changed(self, checked):
        self.project["auto_assemble"] = bool(checked)
        if not checked:
            self.project["auto_assemble_pending"] = False

    def _renumber_default_names(self):
        for i, clip in enumerate(self._clips(), 1):
            name = str(clip.get("name") or "").strip()
            if not name or re.fullmatch(r"Clip\s+\d+", name, flags=re.I):
                clip["name"] = f"Clip {i}"

    def _touch_clip(self, index: int, propagate=True):
        # The initial startup Timeline intentionally has no project folder yet.
        # On the first real edit, ask where this project belongs before generation
        # can continue writing clips to the generic main output directory.
        self._ensure_project_setup_for_first_edit()
        clips = self._clips()
        if not (0 <= index < len(clips)):
            return
        clip = clips[index]
        if bool(clip.get("locked", False)):
            return
        self._invalidate_assembly()
        clip.pop("_preserve_downstream_on_finish", None)
        if clip.get("queue_job_id") or clip.get("status") in {"pending", "running", "finished"}:
            clip["stale"] = True
        # Edit topology determines whether the already-rendered next block is
        # intentionally preserved. Only "continue previous -> free ending" creates
        # a new outgoing boundary that invalidates an existing continuation chain.
        edit_mode = str(clip.get("edit_mode") or ("continue_previous" if clip.get("generation_mode") == "continue" else "standalone"))
        preserve_next = edit_mode in {"bridge_both", "anchor_next", "standalone"}
        if propagate and not preserve_next:
            for j in range(index + 1, len(clips)):
                if clips[j].get("generation_mode") != "continue":
                    break
                if clips[j].get("queue_job_id") or clips[j].get("status") in {"pending", "running", "finished"}:
                    clips[j]["stale"] = True

    # --------------------------------------------------------------- undo/redo
    def _history_snapshot(self) -> dict:
        return {
            "project": copy.deepcopy(self.project),
            "selected_clip_id": self.selected_clip_id,
            "selected_cut_index": int(self.selected_cut_index or 0),
        }

    @staticmethod
    def _history_same(a: dict, b: dict) -> bool:
        return (
            isinstance(a, dict)
            and isinstance(b, dict)
            and a.get("project") == b.get("project")
            and a.get("selected_clip_id") == b.get("selected_clip_id")
            and int(a.get("selected_cut_index") or 0) == int(b.get("selected_cut_index") or 0)
        )

    def _end_history_coalesce(self):
        self._history_coalesce_key = None

    def _record_undo_state(self, label: str, *, coalesce_key: str | None = None):
        """Store the state immediately before a user edit.

        Repeated text/spin edits can be coalesced for a short interval, while
        structural actions always create their own undo step. Any new edit clears
        Redo, matching normal editor behaviour.
        """
        if self._history_restoring:
            return
        key = str(coalesce_key) if coalesce_key else None
        if key and key == self._history_coalesce_key:
            self._history_coalesce_timer.start()
            return

        snap = self._history_snapshot()
        if not self._undo_stack or not self._history_same(self._undo_stack[-1].get("snapshot", {}), snap):
            self._undo_stack.append({"label": str(label or "Edit"), "snapshot": snap})
        self._redo_stack.clear()
        self._history_coalesce_key = key
        if key:
            self._history_coalesce_timer.start()
        else:
            self._history_coalesce_timer.stop()
        self._update_history_buttons()

    def _reset_history(self):
        self._undo_stack.clear()
        self._redo_stack.clear()
        self._history_coalesce_key = None
        self._history_coalesce_timer.stop()
        self._update_history_buttons()

    def _update_history_buttons(self):
        if not hasattr(self, "undo_btn"):
            return
        self.undo_btn.setEnabled(bool(self._undo_stack))
        self.redo_btn.setEnabled(bool(self._redo_stack))
        if self._undo_stack:
            self.undo_btn.setToolTip(f"Undo: {self._undo_stack[-1].get('label') or 'last Timeline edit'}")
        else:
            self.undo_btn.setToolTip("Nothing to undo in this Timeline session.")
        if self._redo_stack:
            self.redo_btn.setToolTip(f"Redo: {self._redo_stack[-1].get('label') or 'last undone Timeline edit'}")
        else:
            self.redo_btn.setToolTip("Nothing to redo in this Timeline session.")

    def _restore_history_snapshot(self, snap: dict):
        self._history_restoring = True
        self._history_coalesce_key = None
        self._history_coalesce_timer.stop()
        try:
            self.project = copy.deepcopy(snap.get("project") or self._new_project_data())
            self.selected_clip_id = snap.get("selected_clip_id")
            self.selected_clip_ids = {str(self.selected_clip_id)} if self.selected_clip_id else set()
            self._selection_anchor_id = str(self.selected_clip_id) if self.selected_clip_id else None
            self.selected_cut_index = int(snap.get("selected_cut_index") or 0)
            self._refresh_all(select_first=True)
        finally:
            self._history_restoring = False
        self._update_history_buttons()

    def undo_timeline(self):
        self._end_history_coalesce()
        current = self._history_snapshot()
        # Skip accidental no-op entries (for example a cancelled file picker).
        while self._undo_stack and self._history_same(self._undo_stack[-1].get("snapshot", {}), current):
            self._undo_stack.pop()
        if not self._undo_stack:
            self._update_history_buttons()
            return False
        entry = self._undo_stack.pop()
        self._redo_stack.append({"label": entry.get("label") or "Edit", "snapshot": current})
        self._restore_history_snapshot(entry["snapshot"])
        return True

    def redo_timeline(self):
        self._end_history_coalesce()
        current = self._history_snapshot()
        while self._redo_stack and self._history_same(self._redo_stack[-1].get("snapshot", {}), current):
            self._redo_stack.pop()
        if not self._redo_stack:
            self._update_history_buttons()
            return False
        entry = self._redo_stack.pop()
        self._undo_stack.append({"label": entry.get("label") or "Edit", "snapshot": current})
        self._restore_history_snapshot(entry["snapshot"])
        return True

    def initialize_from_current_settings(self):
        """Populate the initial clip after the main window finished building.

        Timeline is inserted before Settings in the tab bar, so its constructor
        runs before every Generation setting widget exists.  Deferring this one
        capture avoids a half-empty first clip while keeping the desired tab order.
        """
        clips = self._clips()
        if len(clips) != 1:
            return
        clip = clips[0]
        if clip.get("settings"):
            return
        settings = self._capture_settings()
        if not settings:
            return
        settings = self._timeline_global_settings(settings)
        clip["settings"] = settings
        self._refresh_all()

    # --------------------------------------------------------------------- UI
    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(8)

        toolbar = QHBoxLayout()
        toolbar.setSpacing(6)
        self.new_btn = QPushButton("New")
        self.save_btn = QPushButton("Save")
        self.load_btn = QPushButton("Load")
        self.undo_btn = QPushButton("↶ Undo")
        self.redo_btn = QPushButton("↷ Redo")
        self.undo_btn.setEnabled(False)
        self.redo_btn.setEnabled(False)
        self.add_clip_btn = QPushButton("+ Clip")
        self.dup_clip_btn = QPushButton("Duplicate")
        self.del_clip_btn = QPushButton("Delete")
        self.lock_clip_btn = QPushButton("Lock selected")
        self.lock_clip_btn.setToolTip("Lock selected timeline blocks so generation and editing actions cannot replace them.")
        self.selection_btn = QPushButton("Selection")
        self.selection_menu = QMenu(self.selection_btn)
        self.select_all_action = self.selection_menu.addAction("Select all")
        self.select_from_action = self.selection_menu.addAction("Select from here")
        self.select_to_action = self.selection_menu.addAction("Select to here")
        self.select_missing_action = self.selection_menu.addAction("Select all missing")
        self.select_stale_action = self.selection_menu.addAction("Select stale")
        self.selection_menu.addSeparator()
        self.clear_selection_action = self.selection_menu.addAction("Clear selection")
        self.selection_btn.setMenu(self.selection_menu)
        self.left_btn = QPushButton("◀")
        self.right_btn = QPushButton("▶")
        for w in (self.new_btn, self.save_btn, self.load_btn, self.undo_btn, self.redo_btn, self.add_clip_btn, self.dup_clip_btn, self.del_clip_btn, self.lock_clip_btn, self.selection_btn, self.left_btn, self.right_btn):
            toolbar.addWidget(w)
        toolbar.addSpacing(10)
        toolbar.addWidget(QLabel("Zoom"))
        self.zoom_slider = QSlider(Qt.Orientation.Horizontal)
        self.zoom_slider.setRange(16, 100)
        self.zoom_slider.setValue(38)
        self.zoom_slider.setFixedWidth(130)
        toolbar.addWidget(self.zoom_slider)
        toolbar.addStretch(1)
        self.summary_label = QLabel()
        toolbar.addWidget(self.summary_label)
        root.addLayout(toolbar)

        # Timeline actions live inside the Timeline tab so the main workflow is
        # obvious even when the global bottom Generate button is out of view.
        runbar = QHBoxLayout()
        runbar.setSpacing(8)
        self.generate_timeline_btn = QPushButton("▶ Generate Timeline")
        self.generate_timeline_btn.setMinimumHeight(36)
        self.generate_timeline_btn.setToolTip(
            "Choose which part of the timeline to generate."
        )
        self.generate_timeline_menu = QMenu(self.generate_timeline_btn)
        self.generate_all_action = self.generate_timeline_menu.addAction("Generate all clips")
        self.generate_missing_action = self.generate_timeline_menu.addAction("Generate all missing clips")
        self.generate_selected_only_action = self.generate_timeline_menu.addAction("Generate selected clips only")
        self.generate_from_selected_action = self.generate_timeline_menu.addAction("Start generate from selected block")
        self.generate_timeline_menu.addSeparator()
        self.generate_cancel_action = self.generate_timeline_menu.addAction("Cancel")
        self.generate_timeline_btn.setMenu(self.generate_timeline_menu)
        self.generate_all_action.triggered.connect(lambda: self.generate_timeline("all"))
        self.generate_missing_action.triggered.connect(lambda: self.generate_timeline("missing"))
        self.generate_selected_only_action.triggered.connect(lambda: self.generate_timeline("selected_only"))
        self.generate_from_selected_action.triggered.connect(lambda: self.generate_timeline("selected_onward"))
        self.hq_restart_btn = QPushButton("HQ restart")
        self.hq_restart_btn.setMinimumHeight(36)
        self.hq_restart_btn.setToolTip(
            "Use the HQ restart feature when your timeline is tested on a low resolution and results look good."
        )
        self.assemble_timeline_btn = QPushButton("Assemble Video")
        self.assemble_timeline_btn.setMinimumHeight(36)
        self.assemble_timeline_btn.setToolTip(
            "Join the finished timeline clip outputs, in timeline order, into one final MP4."
        )
        self.use_generation_settings_btn = QPushButton("Use current Generation settings")
        self.use_generation_settings_btn.setMinimumHeight(36)
        self.use_generation_settings_btn.setToolTip(
            "Capture the current Generation-tab settings and apply them to every Timeline generation block. "
            "Timeline prompts, reference images and clip durations are preserved. New clips will inherit this global settings snapshot."
        )
        runbar.addWidget(self.generate_timeline_btn)
        runbar.addWidget(self.hq_restart_btn)
        runbar.addWidget(self.assemble_timeline_btn)
        runbar.addWidget(self.use_generation_settings_btn)
        runbar.addStretch(1)
        root.addLayout(runbar)

        self.workspace_splitter = QSplitter(Qt.Orientation.Horizontal, self)
        self.workspace_splitter.setChildrenCollapsible(False)
        self.workspace_splitter.setHandleWidth(6)

        timeline_panel = QWidget(self.workspace_splitter)
        tl = QVBoxLayout(timeline_panel)
        tl.setContentsMargins(0, 0, 0, 0)
        tl.setSpacing(6)

        self.chain_help = QLabel(
            "Each block is a new generation, You can drag blocks to reorder them or drag the right side of a block to make duration shorter/longer."
        )
        self.chain_help.setWordWrap(True)
        tl.addWidget(self.chain_help)

        self.timeline_scroll = QScrollArea()
        self.timeline_scroll.setWidgetResizable(False)
        self.timeline_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.timeline_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.timeline_scroll.setFrameShape(QFrame.Shape.StyledPanel)
        self.canvas = TimelineCanvas()
        self.canvas.set_allowed_frames(self.frame_values)
        self.timeline_scroll.setWidget(self.canvas)
        tl.addWidget(self.timeline_scroll, 1)

        # Compact edit/replacement panel. Only the option list scrolls; the
        # Regenerate button stays permanently visible at the bottom so this panel
        # cannot steal height from the actual timeline as more edit modes are added.
        edit_box = QGroupBox("Edit selected block")
        edit_outer = QVBoxLayout(edit_box)
        edit_outer.setContentsMargins(10, 8, 10, 10)
        edit_outer.setSpacing(6)

        edit_scroll_contents = QWidget()
        ev = QVBoxLayout(edit_scroll_contents)
        ev.setContentsMargins(2, 2, 8, 2)
        ev.setSpacing(6)
        self.edit_selected_label = QLabel("Select a block to choose how it should be regenerated.")
        self.edit_selected_label.setWordWrap(True)
        ev.addWidget(self.edit_selected_label)

        self.edit_mode_group = QButtonGroup(self)
        self.edit_mode_group.setExclusive(True)
        self.edit_bridge_both = QRadioButton("Continue previous block and end with start frame of the next block")
        self.edit_continue_previous = QRadioButton("Continue from previous block but do not end with the start frame of next block")
        self.edit_anchor_next = QRadioButton("Do not use previous block to start but end with the first frame of the next block")
        self.edit_standalone = QRadioButton("Do not use previous and next block")
        self.edit_bridge_both.setToolTip("Continue from the previous video + use first frame of the next video as the end frame")
        self.edit_continue_previous.setToolTip("Continue from the previous video and create a new free ending. Existing continuation clips after this block may need regeneration.")
        self.edit_anchor_next.setToolTip("Start independently from the previous timeline block and use the first frame of the next video as the end frame.")
        self.edit_standalone.setToolTip("Standalone clip")
        for button, mode in (
            (self.edit_bridge_both, "bridge_both"),
            (self.edit_continue_previous, "continue_previous"),
            (self.edit_anchor_next, "anchor_next"),
            (self.edit_standalone, "standalone"),
        ):
            self.edit_mode_group.addButton(button)
            button.setProperty("edit_mode", mode)
            ev.addWidget(button)
        # Keep a real scroll tail below the final radio button.  Qt can otherwise
        # report a content size that stops at the last control's top edge while
        # the sticky footer sits immediately below the viewport, making the final
        # option impossible to scroll fully above the Regenerate button.
        ev.addSpacing(28)
        edit_scroll_contents.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum)
        # Four radio rows + the selected-clip label need more vertical content than
        # the intentionally compact viewport.  Use an explicit floor so the scroll
        # range always reaches past the final option on all font/DPI scales.
        edit_scroll_contents.setMinimumHeight(max(220, edit_scroll_contents.sizeHint().height() + 28))

        self.edit_scroll = QScrollArea()
        self.edit_scroll.setWidgetResizable(True)
        self.edit_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.edit_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.edit_scroll.setFrameShape(QFrame.Shape.NoFrame)
        # Keep this deliberately short: the group title + roughly two/three
        # option rows are visible at once.  Everything else belongs to the
        # inner vertical scrollbar, while the Regenerate button lives in a
        # separate footer and can never cover the scroll contents.
        self.edit_scroll.setFixedHeight(82)
        self.edit_scroll.setWidget(edit_scroll_contents)
        edit_outer.addWidget(self.edit_scroll, 0)

        self.generate_selected_btn = QPushButton("(Re)generate selected block")
        self.generate_selected_btn.setFixedHeight(36)
        self.generate_selected_btn.setToolTip("Regenerate only the selected block using the replacement continuity mode selected above.")
        edit_outer.addWidget(self.generate_selected_btn, 0)

        # Fixed overall height prevents this panel from expanding and forcing
        # the main timeline itself to scroll vertically.  The option list owns
        # its own scrollbar instead.
        edit_box.setFixedHeight(154)
        tl.addWidget(edit_box, 0)

        inspector_scroll = QScrollArea(self.workspace_splitter)
        inspector_scroll.setWidgetResizable(True)
        inspector_scroll.setMinimumWidth(330)
        inspector_scroll.setFrameShape(QFrame.Shape.NoFrame)
        inspector = QWidget()
        iv = QVBoxLayout(inspector)
        iv.setContentsMargins(4, 0, 4, 4)
        iv.setSpacing(8)
        inspector_scroll.setWidget(inspector)

        clip_box = QGroupBox("Selected generation clip")
        cf = QFormLayout(clip_box)
        self.clip_form = cf
        self.clip_name = QLineEdit()
        self.gen_mode = QComboBox()
        self.frames_combo = QComboBox()
        for frames in self.frame_values:
            self.frames_combo.addItem(f"{frames} frames — {frames / FPS:.2f} s", frames)
        self.state_label = QLabel("Draft")
        self.clip_output_label = QLabel("—")
        self.clip_output_label.setWordWrap(True)
        self.clip_output_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        clip_result_actions = QWidget()
        clip_result_l = QHBoxLayout(clip_result_actions)
        clip_result_l.setContentsMargins(0, 0, 0, 0)
        clip_result_l.setSpacing(6)
        self.preview_clip_btn = QPushButton("Preview result")
        self.open_clip_btn = QPushButton("Open folder")
        clip_result_l.addWidget(self.preview_clip_btn)
        clip_result_l.addWidget(self.open_clip_btn)
        clip_result_l.addStretch(1)
        cf.addRow("Name", self.clip_name)
        cf.addRow("Generation", self.gen_mode)
        self.start_source_label = QLabel("—")
        self.start_source_label.setWordWrap(True)
        self.start_source_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        cf.addRow("Start video", self.start_source_label)
        cf.addRow("Duration", self.frames_combo)
        cf.addRow("State", self.state_label)
        cf.addRow("Output", self.clip_output_label)
        cf.addRow("", clip_result_actions)
        iv.addWidget(clip_box)

        self.prompt_box = QGroupBox("Prompt")
        prompt_box = self.prompt_box
        pv = QVBoxLayout(prompt_box)
        self.cut_prompt = QPlainTextEdit()
        self.cut_prompt.setPlaceholderText(
            "Prompt for this H3 generation. You can use the normal Prompt Builder output here, including multiple shots or timestamps."
        )
        self.cut_prompt.setMinimumHeight(240)
        pv.addWidget(self.cut_prompt)
        iv.addWidget(prompt_box)

        self.references_box = QGroupBox("Reference images")
        rv = QVBoxLayout(self.references_box)
        rv.setSpacing(6)
        self.use_refs_check = QCheckBox("Use reference images (Ref2VA)")
        self.use_refs_warning = QLabel("Using reference image(s) disables the selection to continue from a previous clip.")
        self.use_refs_warning.setWordWrap(True)
        self.use_refs_warning.setVisible(False)
        self.use_refs_guide = QLabel(
            "Tip: the Insert buttons add a ready-to-use subject snippet. If your prompt body never mentions any <Subject N>, Timeline now automatically adds one helper sentence so the loaded reference image(s) are actually invoked."
        )
        self.use_refs_guide.setWordWrap(True)
        self.use_refs_guide.setVisible(False)
        self.add_refs_btn = QPushButton("+ Add reference images")
        self.add_refs_btn.setToolTip("Add up to 5 reference images for this timeline clip.")
        self.refs_rows_widget = QWidget()
        self.refs_rows_layout = QVBoxLayout(self.refs_rows_widget)
        self.refs_rows_layout.setContentsMargins(0, 0, 0, 0)
        self.refs_rows_layout.setSpacing(6)
        rv.addWidget(self.use_refs_check)
        rv.addWidget(self.use_refs_warning)
        rv.addWidget(self.use_refs_guide)
        rv.addWidget(self.add_refs_btn)
        rv.addWidget(self.refs_rows_widget)
        iv.addWidget(self.references_box)

        self.settings_box = QGroupBox("MiniMax settings")
        settings_box = self.settings_box
        sf = QFormLayout(settings_box)
        self.seed_spin = QSpinBox(); self.seed_spin.setRange(-1, 2147483647)
        self.steps_spin = QSpinBox(); self.steps_spin.setRange(1, 100)
        self.scheduler_combo = QComboBox(); self.scheduler_combo.addItems(["simple", "beta"])
        self.audio_memory_check = QCheckBox("Carry audio memory")
        self.model_label = QLabel("—"); self.model_label.setWordWrap(True)
        self.refs_label = QLabel("—"); self.refs_label.setWordWrap(True)
        self.loras_label = QLabel("—"); self.loras_label.setWordWrap(True)
        sf.addRow("Seed", self.seed_spin)
        sf.addRow("Steps", self.steps_spin)
        sf.addRow("Scheduler", self.scheduler_combo)
        sf.addRow("", self.audio_memory_check)
        sf.addRow("Model", self.model_label)
        sf.addRow("References", self.refs_label)
        sf.addRow("LoRAs", self.loras_label)
        iv.addWidget(settings_box)
        iv.addStretch(1)

        self.workspace_splitter.addWidget(timeline_panel)
        self.workspace_splitter.addWidget(inspector_scroll)
        self.workspace_splitter.setStretchFactor(0, 4)
        self.workspace_splitter.setStretchFactor(1, 1)
        self.workspace_splitter.setSizes([950, 390])
        root.addWidget(self.workspace_splitter, 1)

        # Connections.
        self.new_btn.clicked.connect(self.new_project)
        self.save_btn.clicked.connect(self.save_project)
        self.load_btn.clicked.connect(self.load_project)
        self.undo_btn.clicked.connect(self.undo_timeline)
        self.redo_btn.clicked.connect(self.redo_timeline)
        self.hq_restart_btn.clicked.connect(self.hq_restart)
        self.generate_selected_btn.clicked.connect(self.generate_selected)
        self.assemble_timeline_btn.clicked.connect(self.assemble_timeline)
        self.use_generation_settings_btn.clicked.connect(self.use_current_generation_settings)
        self.preview_clip_btn.clicked.connect(self.preview_selected_result)
        self.open_clip_btn.clicked.connect(self.open_selected_output)
        self.add_clip_btn.clicked.connect(self.add_clip)
        self.dup_clip_btn.clicked.connect(self.duplicate_clip)
        self.del_clip_btn.clicked.connect(self.delete_clip)
        self.lock_clip_btn.clicked.connect(self.toggle_lock_selected)
        self.select_all_action.triggered.connect(self.select_all_clips)
        self.select_from_action.triggered.connect(self.select_from_here)
        self.select_to_action.triggered.connect(self.select_to_here)
        self.select_missing_action.triggered.connect(self.select_all_missing)
        self.select_stale_action.triggered.connect(self.select_all_stale)
        self.clear_selection_action.triggered.connect(self.clear_selection)
        self.left_btn.clicked.connect(lambda: self.move_clip(-1))
        self.right_btn.clicked.connect(lambda: self.move_clip(1))
        self.zoom_slider.valueChanged.connect(self.canvas.set_zoom)
        self.canvas.clipSelected.connect(self.select_clip)
        self.canvas.clipSelectionRequested.connect(self._selection_requested)
        self.canvas.clipsReordered.connect(self._reorder_clips)
        self.canvas.clipFramesChanged.connect(self._canvas_frames_changed)
        self.canvas.clipContextMenuRequested.connect(self._show_clip_context_menu)
        self.clip_name.textEdited.connect(self._clip_name_changed)
        self.gen_mode.currentIndexChanged.connect(self._mode_changed)
        self.frames_combo.currentIndexChanged.connect(self._frames_changed)
        self.seed_spin.valueChanged.connect(self._settings_changed)
        self.steps_spin.valueChanged.connect(self._settings_changed)
        self.scheduler_combo.currentTextChanged.connect(self._settings_changed)
        self.audio_memory_check.toggled.connect(self._settings_changed)
        self.edit_mode_group.buttonClicked.connect(self._edit_mode_changed)
        self.cut_prompt.textChanged.connect(self._cut_prompt_changed)
        self.use_refs_check.toggled.connect(self._reference_mode_changed)
        self.add_refs_btn.clicked.connect(self._add_reference_images)

    # -------------------------------------------------------------- refreshers
    def _refresh_all(self, *, select_first=False):
        self._update_history_buttons()
        valid_ids = {str(c.get("id")) for c in self._clips()}
        self.selected_clip_ids = {str(x) for x in self.selected_clip_ids if str(x) in valid_ids}
        if select_first and self._clips() and not self.selected_clip_id:
            self.selected_clip_id = str(self._clips()[0]["id"])
        if self.selected_clip_id and str(self.selected_clip_id) not in valid_ids:
            self.selected_clip_id = next(iter(self.selected_clip_ids), None)
        if self.selected_clip_id:
            self.selected_clip_ids.add(str(self.selected_clip_id))
        elif self.selected_clip_ids:
            self.selected_clip_id = next((str(c.get("id")) for c in self._clips() if str(c.get("id")) in self.selected_clip_ids), None)
        if select_first and self.selected_clip_id:
            self.selected_clip_ids.add(str(self.selected_clip_id))
            self._selection_anchor_id = str(self.selected_clip_id)
        total = sum(_clip_seconds(c) for c in self._clips())
        selection_suffix = f"  •  {len(self.selected_clip_ids)} selected" if len(self.selected_clip_ids) > 1 else ""
        locked_count = sum(1 for c in self._clips() if bool(c.get("locked", False)))
        lock_suffix = f"  •  {locked_count} locked" if locked_count else ""
        self.summary_label.setText(f"{len(self._clips())} clips  •  {total:.2f}s  •  {round(total * FPS)} timeline frames{selection_suffix}{lock_suffix}")
        self.generate_timeline_btn.setEnabled(bool(self._clips()))
        self.hq_restart_btn.setEnabled(bool(self._clips()))
        ready, reason = self._assembly_ready()
        self.assemble_timeline_btn.setEnabled(
            bool(self._clips()) and not bool(getattr(self, "_timeline_assemble_click_busy", False))
        )
        self.assemble_timeline_btn.setToolTip(
            "Assemble every Timeline block that has an existing output file, in Timeline order. Stale status is ignored."
            if ready else reason
        )
        self.summary_label.setToolTip(
            str(self._project_path) if self._project_path
            else f"Autosaving recovery project to {self._autosave_temp_path}"
        )
        self.canvas.set_clips(self._clips(), self.selected_clip_id, self.selected_clip_ids)
        selected_clips = [c for c in self._clips() if str(c.get("id")) in self.selected_clip_ids]
        any_selected = bool(selected_clips)
        all_locked = any_selected and all(bool(c.get("locked", False)) for c in selected_clips)
        self.lock_clip_btn.setEnabled(any_selected)
        self.lock_clip_btn.setText("Unlock selected" if all_locked else "Lock selected")
        self.del_clip_btn.setEnabled(any(not bool(c.get("locked", False)) for c in selected_clips))
        primary = self._selected_clip()
        primary_locked = bool((primary or {}).get("locked", False))
        self.left_btn.setEnabled(bool(primary) and not primary_locked)
        self.right_btn.setEnabled(bool(primary) and not primary_locked)
        self._load_inspector()

    def _load_inspector(self):
        clip = self._selected_clip()
        self._loading_inspector = True
        try:
            enabled = clip is not None
            for w in (self.clip_name, self.gen_mode, self.frames_combo, self.seed_spin, self.steps_spin,
                      self.scheduler_combo, self.audio_memory_check, self.cut_prompt):
                w.setEnabled(enabled)
            if clip is None:
                self.state_label.setText("No clip selected")
                self.clip_output_label.setText("—")
                self.start_source_label.setText("—")
                self.start_source_label.setVisible(False)
                if hasattr(self.clip_form, "setRowVisible"):
                    self.clip_form.setRowVisible(self.start_source_label, False)
                self.prompt_box.setEnabled(False)
                self.references_box.setEnabled(False)
                self._clear_reference_rows()
                self.use_refs_warning.setVisible(False)
                self.use_refs_guide.setVisible(False)
                self.settings_box.setEnabled(False)
                self.preview_clip_btn.setEnabled(False); self.open_clip_btn.setEnabled(False)
                self._refresh_edit_workflow(None)
                self.cut_prompt.clear()
                return
            settings = clip.setdefault("settings", {})
            locked = bool(clip.get("locked", False))
            self.clip_name.setText(str(clip.get("name") or ""))
            selected_index = self._selected_index()
            self._populate_generation_modes(selected_index, str(clip.get("generation_mode") or "new"))
            is_source = selected_index == 0 and str(clip.get("generation_mode") or "") == "source"
            source_path = str(clip.get("start_source_video") or clip.get("output") or "")
            self.start_source_label.setText(source_path if is_source and source_path else "—")
            self.start_source_label.setVisible(is_source)
            if hasattr(self.clip_form, "setRowVisible"):
                self.clip_form.setRowVisible(self.start_source_label, is_source)
            editable = enabled and not is_source and not locked
            self.clip_name.setEnabled(enabled and not locked)
            self.gen_mode.setEnabled(enabled and not locked)
            self.prompt_box.setEnabled(editable)
            self.references_box.setEnabled(editable)
            self.settings_box.setEnabled(editable)
            self.use_refs_check.blockSignals(True)
            self.use_refs_check.setChecked(bool(clip.get("use_reference_images", False)))
            self.use_refs_check.blockSignals(False)
            refs_enabled = bool(clip.get("use_reference_images", False))
            self.use_refs_warning.setVisible(refs_enabled)
            self.use_refs_guide.setVisible(refs_enabled)
            self._refresh_reference_rows(clip)
            # A loaded start video is already the first timeline result; it needs no H3 prompt or generation settings.
            for w in (self.frames_combo, self.seed_spin, self.steps_spin, self.scheduler_combo,
                      self.audio_memory_check, self.cut_prompt):
                w.setEnabled(editable)
            fidx = self.frames_combo.findData(int(clip.get("frames") or 243))
            if fidx >= 0: self.frames_combo.setCurrentIndex(fidx)
            self.seed_spin.setValue(int(settings.get("seed", -1)))
            self.steps_spin.setValue(int(settings.get("steps", 15)))
            sched = str(settings.get("scheduler") or "beta")
            if self.scheduler_combo.findText(sched) < 0: self.scheduler_combo.addItem(sched)
            self.scheduler_combo.setCurrentText(sched)
            # Old projects may still contain glue_results=True. Timeline no longer
            # exposes or honors that flag; clips remain separate until assembly.
            settings["glue_results"] = False
            self.audio_memory_check.setChecked(bool(settings.get("continue_audio_memory", True)))
            self._refresh_edit_workflow(clip)
            state = str(clip.get("status") or "draft").title()
            if clip.get("stale"): state = "Stale — edited after queue/render"
            if locked: state = f"Locked — {state}"
            self.state_label.setText(state)
            output = str(clip.get("output") or "")
            output_exists = bool(output and Path(output).is_file())
            self.clip_output_label.setText(output if output else "—")
            self.preview_clip_btn.setEnabled(output_exists and str(clip.get("status") or "") == "finished")
            self.open_clip_btn.setEnabled(output_exists)
            self._refresh_settings_summary(clip)
            self._load_prompt_editor()
        finally:
            self._loading_inspector = False

    def _refresh_settings_summary(self, clip):
        s = clip.get("settings") or {}
        use_hybrid = bool(s.get("use_hybrid_model"))
        use_ref2va = bool(clip.get("use_reference_images", False)) or int(s.get("mode", 0)) == 2
        model = s.get("hybrid_model") if use_hybrid else (s.get("ref2va_model") if use_ref2va else s.get("fl2va_model"))
        self.model_label.setText(Path(str(model)).name if model else "Default / auto-resolved")
        timeline_refs = _reference_entries(clip) if bool(clip.get("use_reference_images", False)) else []
        refs = list(s.get("ref_videos") or []) + list(s.get("ref_audios") or [])
        if s.get("first"): refs.insert(0, s.get("first"))
        if s.get("last"): refs.append(s.get("last"))
        total_refs = len(timeline_refs) + len(refs)
        self.refs_label.setText(f"{len(timeline_refs)} timeline image reference(s)" if timeline_refs else (f"{total_refs} source/reference item(s)" if total_refs else "None"))
        loras = [x for x in (s.get("loras") or []) if isinstance(x, dict) and str(x.get("path") or "").strip()]
        if loras:
            self.loras_label.setText("; ".join(f"{Path(str(x.get('path'))).name} @ {float(x.get('strength', 1.0)):.2f}" for x in loras))
        else:
            self.loras_label.setText("None")

    def _load_prompt_editor(self):
        clip = self._selected_clip()
        self.cut_prompt.blockSignals(True)
        try:
            if not clip:
                self.cut_prompt.clear()
                self.cut_prompt.setEnabled(False)
                return
            if str(clip.get("generation_mode") or "") == "source":
                self.cut_prompt.clear()
                self.cut_prompt.setPlaceholderText("Loaded start clips do not need a prompt.")
                self.cut_prompt.setEnabled(False)
                return
            self.cut_prompt.setPlaceholderText("Prompt for this H3 generation. You can use the normal Prompt Builder output here, including multiple shots or timestamps.")
            # Timeline v2 presents one normal H3 prompt per generation. Legacy
            # multi-segment projects are flattened into their timestamped compiled
            # prompt once, so no authored content is lost.
            segments = clip.get("segments") or []
            if len(segments) > 1:
                merged = _compiled_prompt(clip)
                clip["segments"] = [{"id": uuid.uuid4().hex, "prompt": merged, "weight": 1.0}]
                segments = clip["segments"]
            if not segments:
                clip["segments"] = [{"id": uuid.uuid4().hex, "prompt": "", "weight": 1.0}]
                segments = clip["segments"]
            self.cut_prompt.setEnabled(True)
            self.cut_prompt.setPlainText(str(segments[0].get("prompt") or ""))
        finally:
            self.cut_prompt.blockSignals(False)

    # ------------------------------------------------------------- project I/O
    def _write_project_json(self, path: Path):
        """Write the current timeline atomically so an interrupted autosave cannot corrupt it."""
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.project["schema_version"] = TIMELINE_SCHEMA_VERSION
        payload = json.dumps(self.project, indent=2, ensure_ascii=False)
        tmp_path = path.with_name(path.name + ".tmp")
        tmp_path.write_text(payload, encoding="utf-8")
        tmp_path.replace(path)

    def _autosave_project(self):
        """Silent 60-second autosave. Unsaved projects use a temporary JSON file."""
        try:
            target = self._project_path or self._autosave_temp_path
            self._write_project_json(target)
        except Exception:
            # Autosave must never interrupt generation or editing with a modal error.
            pass

    def _new_project_setup_dialog(self, *, first_edit=False):
        dialog = QDialog(self)
        dialog.setWindowTitle("New timeline project")
        dialog.setMinimumWidth(560)
        layout = QVBoxLayout(dialog)

        if first_edit:
            intro = QLabel(
                "You started a new project. Please enter a project name and "
                "(optional) an output folder."
            )
            intro.setWordWrap(True)
            layout.addWidget(intro)

        form = QFormLayout()
        name_edit = QLineEdit()
        name_edit.setText("MiniMax Timeline")
        name_edit.selectAll()

        folder_row = QWidget()
        folder_layout = QHBoxLayout(folder_row)
        folder_layout.setContentsMargins(0, 0, 0, 0)
        folder_layout.setSpacing(6)
        folder_edit = QLineEdit()
        folder_edit.setPlaceholderText(
            f"Leave empty for {self._timeline_root / '<project name>'}"
        )
        browse_btn = QPushButton("Browse…")

        def browse_folder():
            start_dir = folder_edit.text().strip() or str(self._timeline_root)
            chosen = QFileDialog.getExistingDirectory(
                dialog, "Choose timeline project folder", start_dir
            )
            if chosen:
                folder_edit.setText(chosen)

        browse_btn.clicked.connect(browse_folder)
        folder_layout.addWidget(folder_edit, 1)
        folder_layout.addWidget(browse_btn)
        form.addRow("Project name:", name_edit)
        form.addRow("Project folder:", folder_row)
        layout.addLayout(form)

        note = QLabel(
            f"Project folders start from {self._timeline_root}. "
            "If Project folder is left empty, a folder named after the project is created there automatically."
        )
        note.setWordWrap(True)
        layout.addWidget(note)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)

        while True:
            if dialog.exec() != QDialog.DialogCode.Accepted:
                return None
            project_name = str(name_edit.text() or "").strip()
            if not project_name:
                QMessageBox.warning(dialog, "Project name required", "Enter a name for the new timeline project.")
                continue
            folder_text = str(folder_edit.text() or "").strip()
            if folder_text:
                project_folder = Path(folder_text).expanduser()
                if not project_folder.is_absolute():
                    project_folder = self._timeline_root / project_folder
            else:
                project_folder = self._timeline_root / _safe_project_name(project_name)
            try:
                project_folder.mkdir(parents=True, exist_ok=True)
            except Exception as exc:
                QMessageBox.critical(dialog, "Project folder", f"Could not create the project folder:\n{project_folder}\n\n{exc}")
                continue
            return project_name, project_folder

    def _ensure_project_setup_for_first_edit(self):
        """Ask for project identity the first time the startup Timeline is edited.

        Pressing New already performs this setup. This guard exists for the common
        case where the user starts editing the default Timeline immediately after
        opening the app.
        """
        current_folder = str(self.project.get("project_folder") or "").strip()
        if current_folder:
            return True

        setup = self._new_project_setup_dialog(first_edit=True)
        if setup is None:
            return False

        project_name, project_folder = setup
        self.project["name"] = project_name
        self.project["project_folder"] = str(project_folder)

        # Keep the recovery file in output/timeline/ as requested. Only its content
        # changes to reflect the newly named project.
        try:
            self._write_project_json(self._autosave_temp_path)
        except Exception:
            pass

        # Refresh the visible project name without turning the programmatic update
        # into another user edit.
        if hasattr(self, "project_name"):
            self.project_name.blockSignals(True)
            try:
                self.project_name.setText(project_name)
            finally:
                self.project_name.blockSignals(False)
        self.summary_label.setToolTip(
            f"Project folder: {project_folder}\nRecovery: {self._autosave_temp_path}"
        )
        return True

    def new_project(self):
        # If the current Timeline contains work, make the close decision explicit.
        # Save Before Close never discards the recovery copy unless the real save
        # completed successfully; Cancel leaves the current project untouched.
        has_content = bool(self._clips()) and (
            len(self._clips()) > 1
            or any(str(s.get("prompt") or "").strip() for c in self._clips() for s in c.get("segments") or [])
            or any(str(c.get("output") or "").strip() for c in self._clips())
        )
        if has_content:
            box = QMessageBox(self)
            box.setWindowTitle("New timeline")
            box.setIcon(QMessageBox.Icon.Question)
            box.setText("Close the current timeline and start a new project?")
            box.setInformativeText("Save the current project first or discard it.")
            save_btn = box.addButton("Save before close", QMessageBox.ButtonRole.AcceptRole)
            discard_btn = box.addButton("Discard", QMessageBox.ButtonRole.DestructiveRole)
            cancel_btn = box.addButton(QMessageBox.StandardButton.Cancel)
            box.exec()
            clicked = box.clickedButton()
            if clicked is cancel_btn or clicked is None:
                return
            if clicked is save_btn:
                if not self.save_project():
                    return
            elif clicked is not discard_btn:
                return

        setup = self._new_project_setup_dialog()
        if setup is None:
            return
        project_name, project_folder = setup

        # The previous recovery file belongs to the project being closed. Once the
        # user has explicitly saved or discarded it, remove it before starting a
        # fresh recovery stream for the new project.
        try:
            if self._autosave_temp_path.exists():
                self._autosave_temp_path.unlink()
        except Exception:
            pass

        self.project = self._new_project_data()
        self.project["name"] = project_name
        self.project["project_folder"] = str(project_folder)
        self._project_path = None
        self.selected_clip_id = None
        self.selected_clip_ids = set()
        self._selection_anchor_id = None
        self.selected_cut_index = 0
        self._ensure_initial_clip()
        self._reset_history()
        # Write the first recovery snapshot immediately; subsequent autosaves keep
        # updating the same file under output/timeline/.
        try:
            self._write_project_json(self._autosave_temp_path)
        except Exception:
            pass
        self._refresh_all(select_first=True)

    def save_project(self):
        # The temporary autosave is only a recovery file. It must never silently
        # become the user's "saved project" and it must never be deleted until a
        # real user-chosen destination has been written successfully.
        current_path = Path(self._project_path) if self._project_path else None
        needs_destination = (
            current_path is None
            or current_path == self._autosave_temp_path
            or current_path.name == self._autosave_temp_path.name
        )

        if needs_destination:
            suggested_name = _safe_project_name(self.project.get("name")).replace(" ", "_") + ".json"
            project_folder = str(self.project.get("project_folder") or "").strip()
            suggested = str((Path(project_folder) if project_folder else self._timeline_root) / suggested_name)
            name, _ = QFileDialog.getSaveFileName(
                self,
                "Save MiniMax timeline",
                suggested,
                "MiniMax Timeline (*.json);;JSON (*.json)",
            )
            if not name:
                # User cancelled: keep the recovery autosave untouched.
                return False
            chosen_path = Path(name)
        else:
            chosen_path = current_path

        try:
            self._write_project_json(chosen_path)
            if not chosen_path.is_file():
                raise OSError(f"Timeline save did not create the requested file:\n{chosen_path}")
        except Exception as exc:
            QMessageBox.critical(self, "Save timeline failed", str(exc))
            return False

        # Only now commit the real project path.
        self._project_path = chosen_path
        self.project["project_folder"] = str(chosen_path.parent)

        # Delete the temporary recovery copy only after a distinct real save file
        # definitely exists. If cleanup fails, leave it in place; an extra recovery
        # file is safer than losing the project.
        try:
            if (
                self._autosave_temp_path.exists()
                and self._autosave_temp_path.resolve() != chosen_path.resolve()
            ):
                self._autosave_temp_path.unlink()
        except Exception:
            pass

        self.summary_label.setToolTip(str(self._project_path))
        QMessageBox.information(
            self,
            "Timeline saved",
            f"Timeline project saved to:\n{self._project_path}",
        )
        return True

    def _timeline_load_start_dir(self) -> str:
        """Return the last successfully used Timeline Load folder."""
        try:
            path = self._file_dialog_history_path
            if path.is_file():
                data = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    folders = data.get("folders")
                    if isinstance(folders, dict):
                        candidate = folders.get("minimax_timeline_load")
                        if candidate and Path(candidate).is_dir():
                            return str(Path(candidate))
        except Exception:
            pass
        # Existing project folder is a useful fallback for older installs that
        # do not have dialog history yet. Otherwise use the Timeline output root.
        try:
            if self._project_path and self._project_path.parent.is_dir():
                return str(self._project_path.parent)
        except Exception:
            pass
        return str(self._timeline_root)

    def _remember_timeline_load_folder(self, selected: str | Path) -> None:
        """Persist the directory of a successfully selected Timeline JSON."""
        if not selected:
            return
        try:
            q = Path(str(selected)).expanduser()
            folder = q if q.is_dir() else q.parent
            if not folder.is_dir():
                return
            path = self._file_dialog_history_path
            data = {}
            if path.is_file():
                try:
                    data = json.loads(path.read_text(encoding="utf-8"))
                except Exception:
                    data = {}
            if not isinstance(data, dict):
                data = {}
            folders = data.get("folders")
            if not isinstance(folders, dict):
                folders = {}
            folders["minimax_timeline_load"] = str(folder)
            data["folders"] = folders
            # Keep compatibility with the rest of GrizzlyMax's file dialogs.
            data["last_folder"] = str(folder)
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(path.suffix + ".tmp")
            tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
            tmp.replace(path)
        except Exception:
            # File-dialog history should never stop a project from loading.
            pass

    def load_project(self):
        name, _ = QFileDialog.getOpenFileName(
            self,
            "Load MiniMax timeline",
            self._timeline_load_start_dir(),
            "MiniMax Timeline (*.json);;JSON (*.json)",
        )
        if not name:
            return
        try:
            data = json.loads(Path(name).read_text(encoding="utf-8"))
            if not isinstance(data, dict) or not isinstance(data.get("clips"), list):
                raise ValueError("File does not contain a MiniMax timeline project.")
            self.project = data
            loaded_schema_version = int(self.project.get("schema_version") or 1)
            self.project["schema_version"] = TIMELINE_SCHEMA_VERSION
            self.project.setdefault("project_id", uuid.uuid4().hex)
            self.project.setdefault("name", Path(name).stem)
            self.project.setdefault("project_folder", str(Path(name).parent))
            self.project.setdefault("assembled_output", "")
            self.project.setdefault("assembly_status", "")
            self.project["auto_assemble"] = False
            self.project["auto_assemble_pending"] = False
            self.project.setdefault("global_generation_settings", {})
            self.project.setdefault("hq_restart_override", {})
            for clip in self.project.get("clips") or []:
                legacy_match = bool(clip.get("match_next_first_frame", False))
                if not clip.get("edit_mode"):
                    if legacy_match and str(clip.get("generation_mode") or "new") == "continue":
                        clip["edit_mode"] = "bridge_both"
                    elif legacy_match:
                        clip["edit_mode"] = "anchor_next"
                    elif str(clip.get("generation_mode") or "new") == "continue":
                        clip["edit_mode"] = "continue_previous"
                    else:
                        clip["edit_mode"] = "standalone"
                clip["match_next_first_frame"] = False
            for clip in self._clips():
                clip.setdefault("id", uuid.uuid4().hex)
                clip.setdefault("name", "")
                clip.setdefault("generation_mode", "new")
                clip.setdefault("start_source_video", "")
                clip.setdefault("source_duration_seconds", 0.0)
                clip.setdefault("frames", 243)
                clip.setdefault("use_reference_images", False)
                clip.setdefault("reference_images", [])
                clip.setdefault("settings", {})
                # Legacy Timeline projects may contain a captured standalone Glue
                # flag. Generated Timeline outputs were already forced to remain
                # separate, so clearing this metadata is safe and must not mark the
                # finished clip stale or require regeneration.
                clip["settings"]["glue_results"] = False
                clip.setdefault("status", "draft")
                clip.setdefault("stale", False)
                clip.setdefault("queue_job_id", None)
                clip.setdefault("output", "")
                clip.setdefault("hq_generated", False)
                clip.setdefault("locked", False)
                clip.setdefault("notes", "")
                clip.setdefault("last_generation_info", {})
                if not clip.get("segments"):
                    clip["segments"] = [{"id": uuid.uuid4().hex, "prompt": "", "weight": 1.0}]
                for seg in clip["segments"]:
                    seg.setdefault("id", uuid.uuid4().hex); seg.setdefault("prompt", ""); seg.setdefault("weight", 1.0)
                if len(clip["segments"]) > 1:
                    merged_prompt = _compiled_prompt(clip)
                    clip["segments"] = [{"id": uuid.uuid4().hex, "prompt": merged_prompt, "weight": 1.0}]

            # Schema v1 could accidentally persist every rendered continuation clip
            # as stale after deleting an unrelated final clip. There was no stale
            # reason/fingerprint in that schema, so those saved flags cannot be
            # distinguished from a real edit after reload. Recover the specific
            # legacy "mass stale continuation chain" signature once:
            #   - Clip 1 is still valid
            #   - every rendered continuation clip from Clip 2 onward is stale
            #   - every one of those clips still has its rendered output on disk
            # This is the exact state produced by the old delete-last-clip bug.
            if loaded_schema_version < 2:
                clips = self._clips()
                rendered_continuations = [
                    c for c in clips[1:]
                    if str(c.get("generation_mode") or "") == "continue"
                    and str(c.get("status") or "") == "finished"
                    and str(c.get("output") or "").strip()
                ]
                legacy_mass_stale = bool(rendered_continuations)
                if clips and bool(clips[0].get("stale")):
                    legacy_mass_stale = False
                if legacy_mass_stale:
                    for c in rendered_continuations:
                        out_path = Path(str(c.get("output") or "")).expanduser()
                        if not bool(c.get("stale")) or not out_path.is_file():
                            legacy_mass_stale = False
                            break
                if legacy_mass_stale:
                    for c in rendered_continuations:
                        c["stale"] = False
                    self.project["assembly_status"] = ""
                    print(
                        f"[TIMELINE] Recovered {len(rendered_continuations)} legacy stale "
                        "continuation clip(s) from the old delete-last-clip bug.",
                        flush=True,
                    )

            self._project_path = Path(name)
            self._remember_timeline_load_folder(self._project_path)
            self.selected_clip_id = self._clips()[0]["id"] if self._clips() else None
            self.selected_clip_ids = {str(self.selected_clip_id)} if self.selected_clip_id else set()
            self._selection_anchor_id = str(self.selected_clip_id) if self.selected_clip_id else None
            self.selected_cut_index = 0
            self._reset_history()
            self._refresh_all(select_first=True)
        except Exception as exc:
            QMessageBox.critical(self, "Load timeline failed", str(exc))

    # ------------------------------------------------------------- clip actions
    def add_clip(self):
        if not self._ensure_project_setup_for_first_edit():
            return
        self._record_undo_state("Add clip")
        clips = self._clips()
        idx = self._selected_index()
        insert_at = len(clips) if idx < 0 else idx + 1
        base_settings = clips[idx].get("settings") if idx >= 0 else None
        clip = self._blank_clip(continue_previous=bool(clips), source_settings=base_settings)
        clip["name"] = f"Clip {insert_at + 1}"
        # A newly continued clip starts with an empty prompt instead of silently copying stale action.
        if clip["generation_mode"] == "continue":
            clip["segments"] = [{"id": uuid.uuid4().hex, "prompt": "", "weight": 1.0}]
        clips.insert(insert_at, clip)
        self._invalidate_assembly()
        self.selected_clip_id = clip["id"]
        self.selected_clip_ids = {str(clip["id"])}
        self._selection_anchor_id = str(clip["id"])
        self.selected_cut_index = 0
        self._renumber_default_names()
        self._refresh_all()

    def duplicate_clip(self):
        if not self._ensure_project_setup_for_first_edit():
            return
        idx = self._selected_index()
        if idx < 0: return
        if str(self._clips()[idx].get("generation_mode") or "") == "source":
            QMessageBox.information(self, "Timeline", "The loaded start clip is a source video and cannot be duplicated as a generation block.")
            return
        self._record_undo_state("Duplicate clip")
        clone = copy.deepcopy(self._clips()[idx])
        clone["id"] = uuid.uuid4().hex
        clone["name"] = str(clone.get("name") or f"Clip {idx + 1}") + " copy"
        clone["queue_job_id"] = None; clone["output"] = ""; clone["status"] = "draft"; clone["stale"] = False
        clone.pop("trim_in", None); clone.pop("trim_out", None)
        clone["locked"] = False
        clone["last_generation_info"] = {}
        for seg in clone.get("segments") or []: seg["id"] = uuid.uuid4().hex
        self._clips().insert(idx + 1, clone)
        self._invalidate_assembly()
        self.selected_clip_id = clone["id"]
        self.selected_clip_ids = {str(clone["id"])}
        self._selection_anchor_id = str(clone["id"])
        self._refresh_all()

    def delete_clip(self):
        indices = self._selected_indices()
        if not indices:
            return
        unlocked = [i for i in indices if not bool(self._clips()[i].get("locked", False))]
        if not unlocked:
            QMessageBox.information(self, "Delete clip", "The selected block(s) are locked. Unlock them before deleting.")
            return
        named = [str(self._clips()[i].get("name") or f"Clip {i + 1}") for i in unlocked]
        has_content = any(
            any(str(seg.get("prompt") or "").strip() for seg in self._clips()[i].get("segments") or [])
            for i in unlocked
        )
        locked_skipped = len(indices) - len(unlocked)
        if has_content or locked_skipped:
            if len(unlocked) > 1:
                msg = f"Delete {len(unlocked)} selected unlocked blocks?"
            else:
                msg = f"Delete {named[0]}?"
            if locked_skipped:
                msg += f"\n\n{locked_skipped} locked selected block(s) will be kept."
            if QMessageBox.question(self, "Delete clip", msg) != QMessageBox.StandardButton.Yes:
                return
        self._record_undo_state("Delete clips" if len(unlocked) > 1 else "Delete clip")
        first = min(unlocked)
        removed_ids = {str(self._clips()[i].get("id")) for i in unlocked}
        for i in sorted(unlocked, reverse=True):
            self._clips().pop(i)
        self.selected_clip_ids -= removed_ids
        self._invalidate_assembly()
        self._renumber_default_names()
        if self._clips():
            fallback = self._clips()[min(first, len(self._clips()) - 1)]
            self.selected_clip_id = str(fallback.get("id"))
            self.selected_clip_ids = {self.selected_clip_id}
            self._selection_anchor_id = self.selected_clip_id
        else:
            self.selected_clip_id = None
            self.selected_clip_ids = set()
            self._selection_anchor_id = None
        self.selected_cut_index = 0
        stale_from = first if first < len(self._clips()) else None
        self._repair_continuation_chain(mark_stale=True, stale_from=stale_from)
        self._refresh_all()

    def move_clip(self, delta):
        idx = self._selected_index()
        target = idx + int(delta)
        if idx < 0 or bool((self._selected_clip() or {}).get("locked", False)) or not (0 <= target < len(self._clips())): return
        self._reorder_clips(idx, target)

    def _reorder_clips(self, old, new):
        clips = self._clips()
        if not (0 <= old < len(clips) and 0 <= new < len(clips)): return
        if bool(clips[old].get("locked", False)):
            return
        lo, hi = sorted((old, new))
        if any(bool(clips[i].get("locked", False)) for i in range(lo, hi + 1) if i != old):
            return
        # A user-loaded start video is the root of the chain and must remain first.
        if clips and str(clips[0].get("generation_mode") or "") == "source" and (old == 0 or new == 0):
            return
        self._record_undo_state("Reorder clips")
        item = clips.pop(old); clips.insert(new, item)
        self._invalidate_assembly()
        # Reordering only changes continuation ancestry starting at the earliest
        # position involved in the move. Earlier rendered clips stay valid.
        self._repair_continuation_chain(mark_stale=True, stale_from=min(old, new))
        self._refresh_all()

    def _repair_continuation_chain(self, mark_stale=False, stale_from=None):
        """Repair impossible continuation topology without invalidating unrelated clips.

        ``stale_from`` is the first index whose incoming continuation boundary may
        have changed. Previously this routine marked every rendered continuation
        clip stale, which meant deleting Clip 10 incorrectly invalidated Clips 2-9.
        """
        for i, clip in enumerate(self._clips()):
            if i == 0 and clip.get("generation_mode") == "continue":
                clip["generation_mode"] = "new"
                if mark_stale:
                    clip["stale"] = True
                continue

            if not mark_stale or stale_from is None or i < int(stale_from):
                continue

            if clip.get("generation_mode") == "continue" and (
                clip.get("queue_job_id") or clip.get("status") in {"pending", "running", "finished"}
            ):
                clip["stale"] = True

    def _ordered_selected_ids(self):
        return [str(c.get("id")) for c in self._clips() if str(c.get("id")) in self.selected_clip_ids]

    def _selected_indices(self):
        return [i for i, c in enumerate(self._clips()) if str(c.get("id")) in self.selected_clip_ids]

    def _apply_selection(self, ids, primary=None, *, anchor=None):
        valid = {str(c.get("id")) for c in self._clips()}
        self.selected_clip_ids = {str(x) for x in ids if str(x) in valid}
        if primary is not None and str(primary) in self.selected_clip_ids:
            self.selected_clip_id = str(primary)
        elif self.selected_clip_ids:
            self.selected_clip_id = next((str(c.get("id")) for c in self._clips() if str(c.get("id")) in self.selected_clip_ids), None)
        else:
            self.selected_clip_id = None
        if anchor is not None:
            self._selection_anchor_id = str(anchor) if str(anchor) in valid else None
        elif not self.selected_clip_ids:
            self._selection_anchor_id = None
        self.selected_cut_index = 0
        self._refresh_all()

    def select_clip(self, clip_id):
        # Legacy/plain selection path.
        clip_id = str(clip_id)
        self._apply_selection({clip_id}, clip_id, anchor=clip_id)

    def _selection_requested(self, clip_id, modifiers_value=0):
        clip_id = str(clip_id)
        ctrl = bool(int(modifiers_value) & int(Qt.KeyboardModifier.ControlModifier.value))
        shift = bool(int(modifiers_value) & int(Qt.KeyboardModifier.ShiftModifier.value))
        clips = self._clips()
        ids = [str(c.get("id")) for c in clips]
        if clip_id not in ids:
            return

        if shift:
            anchor = self._selection_anchor_id or self.selected_clip_id or clip_id
            try:
                a = ids.index(str(anchor)); b = ids.index(clip_id)
            except ValueError:
                a = b = ids.index(clip_id)
            lo, hi = sorted((a, b))
            range_ids = set(ids[lo:hi + 1])
            if ctrl:
                range_ids |= set(self.selected_clip_ids)
            self._apply_selection(range_ids, clip_id, anchor=anchor)
            return

        if ctrl:
            new_ids = set(self.selected_clip_ids)
            if clip_id in new_ids:
                new_ids.remove(clip_id)
                primary = self.selected_clip_id if self.selected_clip_id in new_ids else None
            else:
                new_ids.add(clip_id)
                primary = clip_id
            self._apply_selection(new_ids, primary, anchor=clip_id if clip_id in new_ids else self._selection_anchor_id)
            return

        self._apply_selection({clip_id}, clip_id, anchor=clip_id)

    def select_all_clips(self):
        ids = [str(c.get("id")) for c in self._clips()]
        self._apply_selection(ids, self.selected_clip_id or (ids[0] if ids else None), anchor=self.selected_clip_id or (ids[0] if ids else None))

    def select_from_here(self):
        idx = self._selected_index()
        if idx < 0:
            return
        ids = [str(c.get("id")) for c in self._clips()[idx:]]
        self._apply_selection(ids, self.selected_clip_id, anchor=self.selected_clip_id)

    def select_to_here(self):
        idx = self._selected_index()
        if idx < 0:
            return
        ids = [str(c.get("id")) for c in self._clips()[:idx + 1]]
        self._apply_selection(ids, self.selected_clip_id, anchor=self.selected_clip_id)

    def select_all_missing(self):
        ids = [str(c.get("id")) for c in self._clips() if not self._clip_has_usable_output(c)]
        self._apply_selection(ids, ids[0] if ids else None, anchor=ids[0] if ids else None)

    def select_all_stale(self):
        ids = [str(c.get("id")) for c in self._clips() if bool(c.get("stale", False))]
        self._apply_selection(ids, ids[0] if ids else None, anchor=ids[0] if ids else None)

    def clear_selection(self):
        self._apply_selection(set(), None, anchor=None)

    def toggle_lock_selected(self):
        selected = [c for c in self._clips() if str(c.get("id")) in self.selected_clip_ids]
        if not selected:
            return False
        unlock = all(bool(c.get("locked", False)) for c in selected)
        self._record_undo_state("Unlock clips" if unlock else "Lock clips")
        for clip in selected:
            clip["locked"] = not unlock
        self._refresh_all()
        return True

    def _selected_clip_locked(self):
        clip = self._selected_clip()
        return bool((clip or {}).get("locked", False))

    # ---------------------------------------------------------- inspector slots
    def _project_name_changed(self, text):
        self.project["name"] = text

    def _clip_name_changed(self, text):
        if self._loading_inspector: return
        clip = self._selected_clip()
        if not clip or bool(clip.get("locked", False)): return
        self._record_undo_state("Rename clip", coalesce_key=f"clip-name:{clip.get('id')}")
        clip["name"] = text
        self.canvas.update()

    def _populate_generation_modes(self, index: int, current_mode: str = "new"):
        self.gen_mode.blockSignals(True)
        try:
            clip = self._selected_clip()
            using_refs = bool((clip or {}).get("use_reference_images", False))
            self.gen_mode.clear()
            self.gen_mode.addItem("New generation / new chain", "new")
            if index == 0:
                self.gen_mode.addItem("Load a start clip", "source")
            elif not using_refs:
                self.gen_mode.addItem("Continue previous clip", "continue")
            target = self.gen_mode.findData(current_mode)
            if target < 0:
                target = 0
            self.gen_mode.setCurrentIndex(target)
            self.gen_mode.setToolTip(
                "Continue previous clip is disabled while this block uses Ref2VA reference images."
                if using_refs and index > 0 else ""
            )
        finally:
            self.gen_mode.blockSignals(False)

    # --------------------------------------------------------- Ref2VA references
    def _clear_reference_rows(self):
        layout = getattr(self, "refs_rows_layout", None)
        if layout is None:
            return
        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

    def _refresh_reference_rows(self, clip=None):
        self._clear_reference_rows()
        clip = clip or self._selected_clip()
        if not clip:
            return
        refs = _reference_entries(clip)
        for idx, ref in enumerate(refs, 1):
            row = QWidget()
            hl = QHBoxLayout(row)
            hl.setContentsMargins(0, 0, 0, 0)
            hl.setSpacing(6)

            thumb = QLabel()
            thumb.setFixedSize(58, 58)
            thumb.setAlignment(Qt.AlignmentFlag.AlignCenter)
            thumb.setFrameShape(QFrame.Shape.StyledPanel)
            pix = QPixmap(ref["path"])
            if not pix.isNull():
                thumb.setPixmap(pix.scaled(54, 54, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
            else:
                thumb.setText("Image")
            thumb.setToolTip(ref["path"])

            token = QLabel(f"<Subject {idx}>\n<Picture {idx}>")
            token.setToolTip(
                f"Use <Subject {idx}> in the prompt for reusable visible content. "
                f"MiniMax receives this file as <Picture {idx}>."
            )
            token.setMinimumWidth(105)

            name_edit = QLineEdit(ref.get("name") or Path(ref["path"]).stem)
            name_edit.setPlaceholderText(f"Reference {idx} name")
            name_edit.setToolTip("Friendly name used in the automatic <Subject N> definition sent to MiniMax.")
            name_edit.editingFinished.connect(lambda i=idx-1, w=name_edit: self._reference_name_changed(i, w.text()))

            insert_btn = QPushButton(f"Insert <Subject {idx}>")
            insert_btn.setToolTip(f"Insert a ready-to-use Ref2VA prompt snippet for <Subject {idx}> at the prompt cursor.")
            insert_btn.clicked.connect(lambda _=False, n=idx: self._insert_reference_token(n))
            remove_btn = QPushButton("Remove")
            remove_btn.clicked.connect(lambda _=False, i=idx-1: self._remove_reference_image(i))

            hl.addWidget(thumb)
            hl.addWidget(token)
            hl.addWidget(name_edit, 1)
            hl.addWidget(insert_btn)
            hl.addWidget(remove_btn)
            self.refs_rows_layout.addWidget(row)

        if not refs:
            empty = QLabel("No reference images loaded.")
            self.refs_rows_layout.addWidget(empty)
        self.add_refs_btn.setEnabled(bool(clip) and len(refs) < 5 and str(clip.get("generation_mode") or "") != "source")

    def _reference_mode_changed(self, enabled):
        if self._loading_inspector:
            return
        clip = self._selected_clip(); idx = self._selected_index()
        if not clip or bool(clip.get("locked", False)) or str(clip.get("generation_mode") or "") == "source":
            return
        enabled = bool(enabled)
        self._record_undo_state("Toggle reference images")
        clip["use_reference_images"] = enabled
        self.use_refs_warning.setVisible(enabled)
        self.use_refs_guide.setVisible(enabled)
        if enabled and str(clip.get("generation_mode") or "new") == "continue":
            clip["generation_mode"] = "new"
            clip["edit_mode"] = "standalone"
        self._touch_clip(idx)
        self._populate_generation_modes(idx, str(clip.get("generation_mode") or "new"))
        self._refresh_edit_workflow(clip)
        self._refresh_settings_summary(clip)
        self.canvas.update()

    def _add_reference_images(self):
        clip = self._selected_clip(); idx = self._selected_index()
        if not clip or bool(clip.get("locked", False)) or str(clip.get("generation_mode") or "") == "source":
            return
        refs = _reference_entries(clip)
        remaining = 5 - len(refs)
        if remaining <= 0:
            QMessageBox.information(self, "Reference images", "This timeline clip already has the maximum of 5 reference images.")
            return
        names, _ = QFileDialog.getOpenFileNames(
            self, "Add reference images", "",
            "Images (*.png *.jpg *.jpeg *.webp *.bmp);;All files (*.*)"
        )
        if not names:
            return
        self._record_undo_state("Add reference images")
        added = 0
        existing = {str(Path(r["path"]).resolve()) for r in refs if Path(r["path"]).exists()}
        for name in names:
            if added >= remaining:
                break
            path = Path(name)
            if not path.is_file():
                continue
            key = str(path.resolve())
            if key in existing:
                continue
            refs.append({"path": str(path), "name": path.stem})
            existing.add(key)
            added += 1
        clip["reference_images"] = refs
        if refs:
            clip["use_reference_images"] = True
            if str(clip.get("generation_mode") or "new") == "continue":
                clip["generation_mode"] = "new"
                clip["edit_mode"] = "standalone"
        self._touch_clip(idx)
        self._refresh_all()

    def _remove_reference_image(self, ref_index: int):
        clip = self._selected_clip(); idx = self._selected_index()
        if not clip or bool(clip.get("locked", False)):
            return
        refs = _reference_entries(clip)
        if not (0 <= ref_index < len(refs)):
            return
        old_count = len(refs)
        self._record_undo_state("Remove reference image")
        refs.pop(ref_index)
        clip["reference_images"] = refs
        # Ref2VA Picture numbering is positional. Renumber Subject tokens in the
        # authored prompt so deleting a middle image cannot silently point later
        # subjects at the wrong picture.
        prompt = _compiled_prompt(clip)
        if prompt and ref_index < old_count - 1:
            placeholders = {}
            for old_n in range(ref_index + 2, old_count + 1):
                placeholder = f"__MMH3_SUBJECT_RENUMBER_{old_n}__"
                prompt = prompt.replace(f"<Subject {old_n}>", placeholder)
                placeholders[placeholder] = f"<Subject {old_n - 1}>"
            for placeholder, token in placeholders.items():
                prompt = prompt.replace(placeholder, token)
            segments = clip.setdefault("segments", [{"id": uuid.uuid4().hex, "prompt": "", "weight": 1.0}])
            if not segments:
                segments.append({"id": uuid.uuid4().hex, "prompt": "", "weight": 1.0})
            clip["segments"] = [{"id": segments[0].get("id") or uuid.uuid4().hex, "prompt": prompt, "weight": 1.0}]
        if not refs:
            clip["use_reference_images"] = False
        self._touch_clip(idx)
        self._refresh_all()

    def _reference_name_changed(self, ref_index: int, text: str):
        clip = self._selected_clip(); idx = self._selected_index()
        if not clip or bool(clip.get("locked", False)):
            return
        refs = _reference_entries(clip)
        if not (0 <= ref_index < len(refs)):
            return
        value = str(text or "").strip() or Path(refs[ref_index]["path"]).stem
        if value == str(refs[ref_index].get("name") or ""):
            return
        self._record_undo_state("Rename reference image")
        refs[ref_index]["name"] = value
        clip["reference_images"] = refs
        self._touch_clip(idx, propagate=False)
        self._refresh_settings_summary(clip)
        self.canvas.update()

    def _insert_reference_token(self, number: int):
        if not self.cut_prompt.isEnabled():
            return
        clip = self._selected_clip() or {}
        refs = _reference_entries(clip)
        friendly = ""
        if 1 <= int(number) <= len(refs):
            friendly = str(refs[int(number) - 1].get("name") or "").strip()
        snippet = f"Feature <Subject {int(number)}> prominently in the scene."
        if friendly:
            snippet = f"Feature <Subject {int(number)}> prominently in the scene as {friendly}."
        cursor = self.cut_prompt.textCursor()
        current_text = self.cut_prompt.toPlainText()
        if cursor.position() > 0 and current_text and not current_text.endswith((" ", "\n")):
            cursor.insertText(" ")
        cursor.insertText(snippet + " ")
        self.cut_prompt.setTextCursor(cursor)
        self.cut_prompt.setFocus()

    def _probe_start_video_duration(self, path: str) -> float:
        try:
            from runtime.ffmpeg_tools import tool_path as ffmpeg_tool_path
            exe = str(ffmpeg_tool_path("ffprobe.exe"))
            cp = subprocess.run(
                [exe, "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
                capture_output=True, text=True, timeout=15, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            if cp.returncode == 0:
                value = float((cp.stdout or "0").strip() or 0)
                return value if value > 0 else 0.0
        except Exception:
            pass
        return 0.0

    def _choose_start_video(self, clip: dict) -> bool:
        name, _ = QFileDialog.getOpenFileName(
            self, "Load start video", "",
            "Video files (*.mp4 *.mkv *.mov *.avi *.webm *.m4v);;All files (*.*)"
        )
        if not name:
            return False
        path = Path(name)
        if not path.is_file():
            return False
        clip["generation_mode"] = "source"
        clip["start_source_video"] = str(path)
        clip["source_duration_seconds"] = self._probe_start_video_duration(str(path))
        clip["output"] = str(path)
        clip.pop("trim_in", None); clip.pop("trim_out", None)
        clip["queue_job_id"] = None
        clip["status"] = "finished"
        clip["stale"] = False
        clip["segments"] = [{"id": uuid.uuid4().hex, "prompt": "", "weight": 1.0}]
        self._invalidate_assembly("Start video changed — assemble again after generation.")
        return True

    def _mode_changed(self):
        if self._loading_inspector:
            return
        idx = self._selected_index(); clip = self._selected_clip()
        if not clip or bool(clip.get("locked", False)):
            return
        mode = str(self.gen_mode.currentData() or "new")
        if idx == 0 and mode == "source":
            previous_mode = str(clip.get("generation_mode") or "new")
            self._record_undo_state("Load start clip")
            if not self._choose_start_video(clip):
                self._loading_inspector = True
                try:
                    self._populate_generation_modes(idx, previous_mode)
                finally:
                    self._loading_inspector = False
                return
            self._refresh_all()
            return
        # Switching back from a loaded start clip turns block 1 into a normal draft generation.
        if idx == 0 and str(clip.get("generation_mode") or "") == "source" and mode == "new":
            self._record_undo_state("Change generation mode")
            clip["generation_mode"] = "new"
            clip["start_source_video"] = ""
            clip["source_duration_seconds"] = 0.0
            clip["output"] = ""
            clip["queue_job_id"] = None
            clip["status"] = "draft"
            clip["stale"] = False
            self._touch_clip(idx)
            self._refresh_all()
            return
        if mode != str(clip.get("generation_mode") or "new"):
            self._record_undo_state("Change generation mode")
        clip["generation_mode"] = mode
        self._touch_clip(idx)
        self._refresh_all()

    def _frames_changed(self):
        if self._loading_inspector: return
        clip = self._selected_clip(); idx = self._selected_index()
        if not clip or bool(clip.get("locked", False)): return
        frames = int(self.frames_combo.currentData() or clip.get("frames") or 243)
        if frames != int(clip.get("frames") or 0):
            self._record_undo_state("Change clip duration")
            clip["frames"] = frames
            clip.setdefault("settings", {})["frames"] = frames
            clip["settings"]["experimental_long_duration"] = frames > 719
            self._touch_clip(idx)
            self._refresh_all()

    def _canvas_frames_changed(self, clip_id, frames):
        for i, clip in enumerate(self._clips()):
            if str(clip.get("id")) == str(clip_id):
                if bool(clip.get("locked", False)):
                    break
                if int(frames) == int(clip.get("frames") or 0):
                    break
                self._record_undo_state("Resize clip")
                clip["frames"] = int(frames)
                clip.setdefault("settings", {})["frames"] = int(frames)
                clip["settings"]["experimental_long_duration"] = int(frames) > 719
                self._touch_clip(i)
                break
        self._refresh_all()

    def _settings_changed(self, *args):
        if self._loading_inspector: return
        clip = self._selected_clip(); idx = self._selected_index()
        if not clip or bool(clip.get("locked", False)): return
        self._record_undo_state("Change clip settings", coalesce_key=f"settings:{clip.get('id')}")
        s = clip.setdefault("settings", {})
        s["seed"] = self.seed_spin.value(); s["steps"] = self.steps_spin.value(); s["scheduler"] = self.scheduler_combo.currentText()
        s["glue_results"] = False
        s["continue_audio_memory"] = self.audio_memory_check.isChecked()
        self._touch_clip(idx)
        self._refresh_all()

    def _refresh_edit_workflow(self, clip):
        buttons = (self.edit_bridge_both, self.edit_continue_previous, self.edit_anchor_next, self.edit_standalone)
        idx = self._selected_index()
        if clip is None or idx < 0:
            self.edit_selected_label.setText("Select a block to choose how it should be regenerated.")
            for b in buttons:
                b.setEnabled(False)
            self.generate_selected_btn.setEnabled(False)
            return

        if bool(clip.get("locked", False)):
            self.edit_selected_label.setText("Locked block — unlock it before changing regeneration mode or recreating it.")
            for b in buttons:
                b.setEnabled(False)
            self.generate_selected_btn.setEnabled(False)
            return

        if str(clip.get("generation_mode") or "") == "source":
            self.edit_selected_label.setText("Loaded start clip — this block is the source for the continuation chain and is not regenerated by H3.")
            for b in buttons:
                b.setEnabled(False)
            self.generate_selected_btn.setEnabled(False)
            return

        has_prev = idx > 0
        has_next = idx < len(self._clips()) - 1
        using_refs = bool(clip.get("use_reference_images", False))
        self.edit_bridge_both.setEnabled(has_prev and has_next and not using_refs)
        self.edit_continue_previous.setEnabled(has_prev and not using_refs)
        self.edit_anchor_next.setEnabled(has_next)
        self.edit_standalone.setEnabled(True)
        self.generate_selected_btn.setEnabled(True)

        mode = str(clip.get("edit_mode") or ("continue_previous" if clip.get("generation_mode") == "continue" else "standalone"))
        valid = {
            "bridge_both": self.edit_bridge_both,
            "continue_previous": self.edit_continue_previous,
            "anchor_next": self.edit_anchor_next,
            "standalone": self.edit_standalone,
        }
        button = valid.get(mode, self.edit_standalone)
        if not button.isEnabled():
            if using_refs:
                mode = "standalone"
            else:
                mode = "anchor_next" if has_next and not has_prev else ("continue_previous" if has_prev and not has_next else "standalone")
            clip["edit_mode"] = mode
            button = valid[mode]
        button.setChecked(True)

        prev_state = "available" if has_prev else "none"
        next_state = "available" if has_next else "none"
        if has_prev:
            prev = self._clips()[idx - 1]
            prev_state = "ready" if str(prev.get("status") or "") == "finished" and bool(prev.get("output")) and not bool(prev.get("stale")) else "not rendered / stale"
        if has_next:
            nxt = self._clips()[idx + 1]
            next_state = "ready" if str(nxt.get("status") or "") == "finished" and bool(nxt.get("output")) and not bool(nxt.get("stale")) else "not rendered / stale"
        ref_note = "  •  Ref2VA: previous continuation disabled" if using_refs else ""
        self.edit_selected_label.setText(
            f"{clip.get('name') or f'Clip {idx + 1}'}  •  previous: {prev_state}  •  next: {next_state}{ref_note}"
        )

    def _edit_mode_changed(self, button):
        if self._loading_inspector or button is None:
            return
        clip = self._selected_clip()
        if not clip or bool(clip.get("locked", False)):
            return
        mode = str(button.property("edit_mode") or "standalone")
        if mode == str(clip.get("edit_mode") or ""):
            return
        self._record_undo_state("Change regeneration mode")
        clip["edit_mode"] = mode
        # This is an edit strategy, not a content change. Choosing it must not
        # stale the clip or mutate the normal first-time creation workflow.
        self.canvas.update()

    def use_current_generation_settings(self):
        """Apply one Generation-tab snapshot globally without touching Timeline content/duration."""
        if not self._ensure_project_setup_for_first_edit():
            return False
        settings = self._timeline_global_settings()
        if not settings:
            QMessageBox.warning(self, "Timeline settings", "No Generation-tab settings were available to capture.")
            return False

        self._record_undo_state("Use current Generation settings")
        self.project["global_generation_settings"] = copy.deepcopy(settings)
        changed_finished = False
        for idx, clip in enumerate(self._clips()):
            if bool(clip.get("locked", False)) or str(clip.get("generation_mode") or "") == "source":
                continue
            # Preserve all Timeline-owned values. Queue-time logic will add the
            # block's frames, prompt, refs and continuation source separately.
            clip["settings"] = copy.deepcopy(settings)
            clip["settings"]["glue_results"] = False
            if clip.get("queue_job_id") or clip.get("status") in {"pending", "running", "finished"}:
                clip["stale"] = True
                changed_finished = True

        self._invalidate_assembly(
            "Generation settings changed — regenerate affected clips before assembling."
            if changed_finished else "Generation settings updated."
        )
        self._refresh_all()
        return True

    # Compatibility for older signal/project code; this is now Timeline-global.
    def capture_current_settings(self):
        return self.use_current_generation_settings()

    def _cut_prompt_changed(self):
        if self._loading_inspector:
            return
        clip = self._selected_clip(); idx = self._selected_index()
        if not clip or bool(clip.get("locked", False)):
            return
        self._record_undo_state("Edit prompt", coalesce_key=f"prompt:{clip.get('id')}")
        segments = clip.setdefault("segments", [])
        if not segments:
            segments.append({"id": uuid.uuid4().hex, "prompt": "", "weight": 1.0})
        if len(segments) > 1:
            merged = _compiled_prompt(clip)
            clip["segments"] = [{"id": uuid.uuid4().hex, "prompt": merged, "weight": 1.0}]
            segments = clip["segments"]
        segments[0]["prompt"] = self.cut_prompt.toPlainText()
        segments[0]["weight"] = 1.0
        self._touch_clip(idx)
        self.canvas.update()

    def _accept_preserved_downstream_chain(self, anchor_index: int) -> int:
        """Accept the already-rendered chain after an anchored replacement.

        bridge_both / anchor_next deliberately regenerate one clip *toward* the
        existing next clip. Once that replacement has actually finished, the
        existing next result and its unchanged continuation chain are valid
        again and must not remain stale just because an earlier delete/recreate
        operation marked them so.
        """
        clips = self._clips()
        if not (0 <= int(anchor_index) < len(clips) - 1):
            return 0
        anchor_clip = clips[int(anchor_index)]
        if str(anchor_clip.get("edit_mode") or "") not in {"bridge_both", "anchor_next"}:
            return 0
        if str(anchor_clip.get("status") or "") != "finished":
            return 0
        anchor_output = Path(str(anchor_clip.get("output") or ""))
        if not anchor_output.is_file():
            return 0

        cleared = 0
        for j in range(int(anchor_index) + 1, len(clips)):
            downstream = clips[j]
            if j > int(anchor_index) + 1 and str(downstream.get("generation_mode") or "") != "continue":
                break
            output = Path(str(downstream.get("output") or ""))
            if str(downstream.get("status") or "") != "finished" or not output.is_file():
                break
            if bool(downstream.get("stale")):
                downstream["stale"] = False
                cleared += 1
        return cleared

    def _recover_old_anchor_preserve_state(self) -> int:
        """Recover projects made before preserve-on-finish was recorded.

        Older builds could regenerate Clip N toward the already-existing
        Clip N+1 correctly, but leave Clip N+1.. stale forever.  A strong
        signature is that the anchored replacement's output is newer than the
        preserved next clip's output.  Recover that already-rendered chain once
        so the user does not have to regenerate it again just to assemble.
        """
        clips = self._clips()
        total = 0
        for i in range(len(clips) - 1):
            clip = clips[i]
            nxt = clips[i + 1]
            if str(clip.get("edit_mode") or "") not in {"bridge_both", "anchor_next"}:
                continue
            if str(clip.get("status") or "") != "finished" or str(nxt.get("status") or "") != "finished":
                continue
            if not bool(nxt.get("stale")):
                continue
            cur_out = Path(str(clip.get("output") or ""))
            next_out = Path(str(nxt.get("output") or ""))
            if not cur_out.is_file() or not next_out.is_file():
                continue
            try:
                # The preserved target existed first; the replacement was
                # rendered afterwards specifically to meet it.
                if cur_out.stat().st_mtime + 0.001 < next_out.stat().st_mtime:
                    continue
            except OSError:
                continue
            cleared = self._accept_preserved_downstream_chain(i)
            if cleared:
                total += cleared
                clip["_anchor_preserve_recovered"] = True
        if total:
            print(f"[TIMELINE] Recovered {total} preserved finished clip(s) from obsolete stale state.", flush=True)
        return total

    # --------------------------------------------------------------- results
    def _assembly_ready(self):
        """Assembly is intentionally independent from Timeline dependency state.

        "Stale" only means a generated clip may no longer match the current
        continuation plan. It must never prevent the user from concatenating
        files that already exist. Assembly therefore checks only whether every
        visible Timeline block has a real output file.
        """
        clips = self._clips()
        if not clips:
            return False, "Timeline has no clips."
        for i, clip in enumerate(clips):
            name = str(clip.get("name") or f"Clip {i + 1}")
            output = str(clip.get("output") or "")
            if not output or not Path(output).is_file():
                return False, f"{name} has no usable output file."
        return True, ""

    def assemble_timeline(self):
        # Assembly is a read-only operation on the Timeline project. Repeated
        # clicks while FFmpeg is being started/running are ignored so the action
        # cannot trigger duplicate requests or interact with edit-state logic.
        if getattr(self, "_timeline_assemble_click_busy", False):
            return False

        ready, reason = self._assembly_ready()
        if not ready:
            QMessageBox.information(self, "Timeline assembly", reason)
            return False
        if not callable(self.assemble_timeline_callback):
            QMessageBox.warning(self, "Timeline assembly", "Timeline assembly is not connected to the standalone GUI.")
            return False

        self._timeline_assemble_click_busy = True
        self.project["assembly_status"] = "Assembling…"
        self.project["assembled_output"] = ""
        self.assemble_timeline_btn.setEnabled(False)
        self.assemble_timeline_btn.setText("Assembling…")
        self._refresh_all()
        try:
            result = self.assemble_timeline_callback(copy.deepcopy(self.project))
        except Exception as exc:
            self._timeline_assemble_click_busy = False
            self.project["assembly_status"] = f"Assembly failed: {exc}"
            self.assemble_timeline_btn.setText("Assemble Video")
            self._refresh_all()
            QMessageBox.critical(self, "Timeline assembly failed", str(exc))
            return False

        if not result:
            self._timeline_assemble_click_busy = False
            if self.project.get("assembly_status") == "Assembling…":
                self.project["assembly_status"] = "Assembly did not start."
            self.assemble_timeline_btn.setText("Assemble Video")
            self._refresh_all()
        return bool(result)

    def mark_assembly_started(self, output):
        self.project["assembled_output"] = str(output or "")
        self.project["assembly_status"] = "Assembling…"
        self._refresh_all()

    def mark_assembly_finished(self, output):
        self.project["assembled_output"] = str(output or "")
        self.project["assembly_status"] = "Finished"
        self._timeline_assemble_click_busy = False
        if hasattr(self, "assemble_timeline_btn"):
            self.assemble_timeline_btn.setText("Assemble Video")
        self._refresh_all()

    def mark_assembly_failed(self, message):
        self.project["assembled_output"] = ""
        self.project["assembly_status"] = "Assembly failed: " + str(message or "Unknown error")
        self._timeline_assemble_click_busy = False
        if hasattr(self, "assemble_timeline_btn"):
            self.assemble_timeline_btn.setText("Assemble Video")
        self._refresh_all()

    def _clip_by_id(self, clip_id):
        target = str(clip_id or "")
        for i, clip in enumerate(self._clips()):
            if str(clip.get("id") or "") == target:
                return i, clip
        return -1, None

    def _show_clip_context_menu(self, clip_id, global_pos):
        idx, clip = self._clip_by_id(clip_id)
        if clip is None:
            return

        # Right click makes that block the primary target, but does not destroy an
        # existing multi-selection when the clicked block is already part of it.
        clip_id = str(clip_id)
        if clip_id not in self.selected_clip_ids:
            self._apply_selection({clip_id}, clip_id, anchor=clip_id)
        elif self.selected_clip_id != clip_id:
            self.selected_clip_id = clip_id
            self._refresh_all()

        menu = QMenu(self)
        regenerate = menu.addAction("(Re)generate this clip")
        preview = menu.addAction("Preview clip")
        trim = menu.addAction("Trim clip…")
        remove = menu.addAction("Remove clip from timeline")
        menu.addSeparator()
        notes = menu.addAction("Add / read notes")
        info = menu.addAction("Info")

        locked = bool(clip.get("locked", False))
        source = str(clip.get("generation_mode") or "") == "source"
        output = str(clip.get("output") or "").strip()
        output_exists = bool(output and Path(output).is_file())
        regenerate.setEnabled(not locked and not source)
        preview.setEnabled(output_exists)
        trim.setEnabled(output_exists and not locked)
        remove.setEnabled(not locked)

        chosen = menu.exec(global_pos)
        if chosen is regenerate:
            self.selected_clip_id = clip_id
            self.generate_selected()
        elif chosen is preview:
            self._preview_clip_by_id(clip_id)
        elif chosen is trim:
            self._trim_clip_by_id(clip_id)
        elif chosen is remove:
            self._remove_clip_by_id(clip_id)
        elif chosen is notes:
            self._edit_clip_notes(clip_id)
        elif chosen is info:
            self._show_clip_info(clip_id)

    def _preview_clip_by_id(self, clip_id):
        _idx, clip = self._clip_by_id(clip_id)
        if not clip:
            return False
        output = str(clip.get("output") or "").strip()
        if not output or not Path(output).is_file():
            return False
        if callable(self.preview_result_callback):
            return bool(self.preview_result_callback(output, clip.get("queue_job_id")))
        return False


    def _trim_clip_by_id(self, clip_id):
        _idx, clip = self._clip_by_id(clip_id)
        if not clip:
            return False
        output = str(clip.get("output") or "").strip()
        if not output or not Path(output).is_file():
            return False
        details = self._probe_video_details(output)
        duration = float(details.get("duration") or 0.0)
        if duration <= 0:
            duration = _clip_seconds(clip)
        trim_in = max(0.0, float(clip.get("trim_in") or 0.0))
        saved_out = clip.get("trim_out")
        try:
            trim_out = float(saved_out) if saved_out is not None else duration
        except Exception:
            trim_out = duration
        trim_out = max(trim_in, min(trim_out, duration))
        dlg = ClipTrimDialog(output, duration, trim_in, trim_out, self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return False
        new_in, new_out = dlg.trim_values()
        # Treat a full-source range as no trim so old/default projects stay clean.
        full_range = new_in <= 0.001 and abs(new_out - duration) <= 0.02
        old_in = float(clip.get("trim_in") or 0.0)
        old_out_raw = clip.get("trim_out")
        old_out = duration if old_out_raw is None else float(old_out_raw)
        if abs(old_in - new_in) <= 0.001 and abs(old_out - new_out) <= 0.001:
            return True
        self._record_undo_state("Trim clip")
        if full_range:
            clip.pop("trim_in", None)
            clip.pop("trim_out", None)
        else:
            clip["trim_in"] = round(new_in, 3)
            clip["trim_out"] = round(new_out, 3)
        self._invalidate_assembly("Clip trim changed — assemble again.")
        self._refresh_all()
        return True

    def _remove_clip_by_id(self, clip_id):
        idx, clip = self._clip_by_id(clip_id)
        if clip is None or bool(clip.get("locked", False)):
            return False
        name = str(clip.get("name") or f"Clip {idx + 1}")
        answer = QMessageBox.question(
            self,
            "Remove clip from timeline",
            f"Remove {name} from the timeline?\n\nThe rendered video file will not be deleted from disk.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return False
        self._record_undo_state("Remove clip from timeline")
        removed_id = str(clip.get("id") or "")
        self._clips().pop(idx)
        self._renumber_default_names()
        self._repair_continuation_chain(mark_stale=True, stale_from=(idx if idx < len(self._clips()) else None))
        self._invalidate_assembly()
        self.selected_clip_ids.discard(removed_id)
        if self._clips():
            fallback_index = min(idx, len(self._clips()) - 1)
            fallback = self._clips()[fallback_index]
            self.selected_clip_id = str(fallback.get("id"))
            self.selected_clip_ids.add(self.selected_clip_id)
            self._selection_anchor_id = self.selected_clip_id
        else:
            self.selected_clip_id = None
            self.selected_clip_ids.clear()
            self._selection_anchor_id = None
            self._ensure_initial_clip()
        self.selected_cut_index = 0
        self._refresh_all()
        return True

    def _edit_clip_notes(self, clip_id):
        _idx, clip = self._clip_by_id(clip_id)
        if clip is None:
            return
        dlg = QDialog(self)
        dlg.setWindowTitle(f"Notes — {clip.get('name') or 'Timeline clip'}")
        dlg.resize(620, 420)
        layout = QVBoxLayout(dlg)
        hint = QLabel("Director notes are stored with this timeline block and are never sent to MiniMax for generation.")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        editor = QPlainTextEdit(dlg)
        editor.setPlaceholderText("Add continuity reminders, fixes to make later, performance notes, story intent, etc.")
        editor.setPlainText(str(clip.get("notes") or ""))
        layout.addWidget(editor, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel, parent=dlg)
        buttons.accepted.connect(dlg.accept)
        buttons.rejected.connect(dlg.reject)
        layout.addWidget(buttons)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        value = editor.toPlainText().rstrip()
        if value == str(clip.get("notes") or ""):
            return
        self._record_undo_state("Edit clip notes")
        clip["notes"] = value
        self._refresh_all()

    @staticmethod
    def _format_bytes(value):
        try:
            size = float(value)
        except Exception:
            return "Unknown"
        units = ["B", "KB", "MB", "GB", "TB"]
        for unit in units:
            if size < 1024.0 or unit == units[-1]:
                return f"{size:.0f} {unit}" if unit == "B" else f"{size:.2f} {unit}"
            size /= 1024.0
        return "Unknown"

    def _probe_video_details(self, output):
        path = Path(str(output or ""))
        if not path.is_file():
            return {}
        details = {"file_size": path.stat().st_size}
        try:
            from runtime.ffmpeg_tools import tool_path as ffmpeg_tool_path
            exe = str(ffmpeg_tool_path("ffprobe.exe"))
            cp = subprocess.run(
                [exe, "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height,r_frame_rate:format=duration", "-of", "json", str(path)],
                capture_output=True, text=True, timeout=15,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            if cp.returncode == 0:
                data = json.loads(cp.stdout or "{}")
                stream = (data.get("streams") or [{}])[0]
                fmt = data.get("format") or {}
                details["width"] = int(stream.get("width") or 0)
                details["height"] = int(stream.get("height") or 0)
                details["duration"] = float(fmt.get("duration") or 0.0)
                details["fps"] = str(stream.get("r_frame_rate") or "")
        except Exception:
            pass
        return details

    @staticmethod
    def _generation_info_from_job(job):
        if not isinstance(job, dict):
            return {}
        info = {}
        for key in (
            "id", "job_number", "mode_name", "model_label", "output", "seed", "actual_seed",
            "resolution", "frames", "steps", "prompt", "created_at", "started_at", "finished_at",
        ):
            value = job.get(key)
            if value is not None:
                info[key] = copy.deepcopy(value)
        settings = job.get("settings")
        if isinstance(settings, dict):
            try:
                info["settings"] = json.loads(json.dumps(settings, default=str))
            except Exception:
                info["settings"] = {str(k): str(v) for k, v in settings.items()}
        return info

    @staticmethod
    def _append_settings_lines(lines, settings):
        if not isinstance(settings, dict):
            return
        preferred = (
            "model", "model_path", "checkpoint", "checkpoint_path", "hybrid_checkpoint", "aspect", "resolution",
            "widescreen_quality", "seed", "steps", "cfg", "shift", "audio_shift", "sampler", "scheduler",
            "lora", "loras", "lora_paths", "lora_strength", "ref_images", "continue_audio_memory", "latent_continuation",
        )
        shown = set()
        for key in preferred:
            if key not in settings:
                continue
            value = settings.get(key)
            if value in (None, "", [], {}):
                continue
            label = key.replace("_", " ").title()
            lines.append(f"{label}: {value}")
            shown.add(key)
        extras = []
        for key in sorted(settings):
            if key in shown:
                continue
            value = settings.get(key)
            if value in (None, "", [], {}):
                continue
            extras.append(f"{key.replace('_', ' ').title()}: {value}")
        if extras:
            lines.append("")
            lines.append("Other saved settings:")
            lines.extend(extras)

    def _show_clip_info(self, clip_id):
        idx, clip = self._clip_by_id(clip_id)
        if clip is None:
            return
        output = str(clip.get("output") or "").strip()
        disk = self._probe_video_details(output)
        trim_in = float(clip.get("trim_in") or 0.0)
        trim_out = clip.get("trim_out")
        if trim_out is not None:
            try:
                trim_text = f"{trim_in:.3f}s → {float(trim_out):.3f}s ({max(0.0, float(trim_out)-trim_in):.3f}s used)"
            except Exception:
                trim_text = "Invalid trim metadata"
        else:
            trim_text = "None"
        generated = clip.get("last_generation_info") if isinstance(clip.get("last_generation_info"), dict) else {}
        settings = generated.get("settings") if isinstance(generated.get("settings"), dict) else clip.get("settings", {})
        prompt = str(generated.get("prompt") or _compiled_reference_prompt(clip) or _compiled_prompt(clip) or "")

        lines = [
            f"Clip: {clip.get('name') or f'Clip {idx + 1}'}",
            f"Timeline position: {idx + 1}",
            f"Status: {clip.get('status') or 'draft'}",
            f"Locked: {'Yes' if clip.get('locked') else 'No'}",
            f"HQ: {'Yes' if clip.get('hq_generated') else 'No'}",
            f"References: {len(_reference_entries(clip))}",
            f"Assembly trim: {trim_text}",
        ]
        if generated:
            if generated.get("job_number") is not None:
                lines.append(f"Queue job: {generated.get('job_number')}")
            if generated.get("model_label"):
                lines.append(f"Model / checkpoint: {generated.get('model_label')}")
            seed = generated.get("actual_seed") if generated.get("actual_seed") is not None else generated.get("seed")
            if seed is not None:
                lines.append(f"Seed used: {seed}")
            if generated.get("steps") is not None:
                lines.append(f"Steps: {generated.get('steps')}")
            if generated.get("resolution"):
                lines.append(f"Queued resolution: {generated.get('resolution')}")
            if generated.get("frames") is not None:
                lines.append(f"Frames: {generated.get('frames')}")
        else:
            lines.append(f"Seed: {(clip.get('settings') or {}).get('seed', 'Unknown')}")
            lines.append(f"Frames: {clip.get('frames') or 'Unknown'}")

        if output:
            lines.append("")
            lines.append(f"Output: {output}")
            if Path(output).is_file():
                lines.append(f"Disk size: {self._format_bytes(disk.get('file_size'))}")
                if disk.get("duration"):
                    lines.append(f"Actual duration: {disk['duration']:.3f} s")
                if disk.get("width") and disk.get("height"):
                    lines.append(f"Actual resolution: {disk['width']} × {disk['height']}")
                if disk.get("fps"):
                    lines.append(f"Video frame rate: {disk['fps']}")
            else:
                lines.append("Output file: Missing from disk")

        lines.append("")
        lines.append("Prompt used:" if generated else "Current saved prompt:")
        lines.append(prompt or "(empty)")
        lines.append("")
        lines.append("Generation settings:" if generated else "Current saved generation settings:")
        self._append_settings_lines(lines, settings)

        notes = str(clip.get("notes") or "").strip()
        if notes:
            lines.extend(["", "Director notes:", notes])

        dlg = QDialog(self)
        dlg.setWindowTitle(f"Clip info — {clip.get('name') or f'Clip {idx + 1}'}")
        dlg.resize(760, 680)
        layout = QVBoxLayout(dlg)
        view = QPlainTextEdit(dlg)
        view.setReadOnly(True)
        view.setPlainText("\n".join(str(x) for x in lines))
        layout.addWidget(view, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close, parent=dlg)
        buttons.rejected.connect(dlg.reject)
        buttons.accepted.connect(dlg.accept)
        buttons.clicked.connect(lambda _button: dlg.accept())
        layout.addWidget(buttons)
        dlg.exec()

    def preview_selected_result(self):
        clip = self._selected_clip()
        if not clip:
            return
        output = str(clip.get("output") or "")
        if callable(self.preview_result_callback) and output:
            self.preview_result_callback(output, clip.get("queue_job_id"))

    def open_selected_output(self):
        clip = self._selected_clip()
        output = str((clip or {}).get("output") or "")
        if callable(self.open_output_callback) and output:
            self.open_output_callback(output)

    def preview_final_result(self):
        output = str(self.project.get("assembled_output") or "")
        if callable(self.preview_result_callback) and output:
            self.preview_result_callback(output, None)

    def open_final_output(self):
        output = str(self.project.get("assembled_output") or "")
        if callable(self.open_output_callback) and output:
            self.open_output_callback(output)

    # -------------------------------------------------------------- generation
    def validate_timeline(self):
        clips = self._clips()
        if not clips:
            return False, "Timeline has no clips."
        for i, clip in enumerate(clips):
            mode = str(clip.get("generation_mode") or "new")
            if i == 0 and mode == "continue":
                return False, "Clip 1 cannot continue a previous timeline clip."
            if mode == "source":
                if i != 0:
                    return False, "Only Clip 1 can be a loaded start clip."
                source = str(clip.get("start_source_video") or clip.get("output") or "")
                if not source or not Path(source).is_file():
                    return False, "Clip 1 is set to Load a start clip, but the video file is missing."
                continue
            prompt = _compiled_prompt(clip)
            if not prompt:
                return False, f"{clip.get('name') or f'Clip {i + 1}'} has no prompt."
            if bool(clip.get("use_reference_images", False)):
                refs = _reference_entries(clip)
                if not refs:
                    return False, f"{clip.get('name') or f'Clip {i + 1}'} has reference mode enabled but no reference images."
                if len(refs) > 5:
                    return False, f"{clip.get('name') or f'Clip {i + 1}'} has more than 5 reference images."
                missing = [r["path"] for r in refs if not Path(r["path"]).is_file()]
                if missing:
                    return False, f"{clip.get('name') or f'Clip {i + 1}'} has a missing reference image: {missing[0]}"
                if mode == "continue":
                    return False, f"{clip.get('name') or f'Clip {i + 1}'} uses reference images and cannot Continue Previous Clip."
            if int(clip.get("frames") or 0) not in self.frame_values:
                return False, f"{clip.get('name') or f'Clip {i + 1}'} has an invalid H3 frame count."
        return True, ""

    def generation_specs(self):
        specs = []
        clips = self._clips()
        hq_override = self.project.get("hq_restart_override") or {}
        if not isinstance(hq_override, dict):
            hq_override = {}
        for i, clip in enumerate(clips):
            spec = copy.deepcopy(clip)
            spec["timeline_index"] = i
            spec["compiled_prompt"] = _compiled_reference_prompt(clip)
            spec["timeline_reference_images"] = _reference_entries(clip)
            # HQ restart is a project-level generation mode. Once selected, keep
            # using the same override for later single-clip regenerations and
            # subsequent timeline generation, including after save/reload.
            if hq_override and str(clip.get("generation_mode") or "") != "source":
                spec["timeline_hq_override"] = copy.deepcopy(hq_override)
            # Edit/replacement topology is used only by Regenerate selected block.
            spec["match_next_first_frame"] = False
            if i > 0:
                prev = clips[i - 1]
                spec["timeline_previous_output"] = str(prev.get("output") or "")
                spec["timeline_previous_job_id"] = str(prev.get("queue_job_id") or "")
                spec["timeline_previous_status"] = str(prev.get("status") or "")
                spec["timeline_previous_stale"] = bool(prev.get("stale"))
                spec["timeline_previous_is_source"] = str(prev.get("generation_mode") or "") == "source"
            if i + 1 < len(clips):
                nxt = clips[i + 1]
                spec["timeline_next_output"] = str(nxt.get("output") or "")
                spec["timeline_next_status"] = str(nxt.get("status") or "")
                spec["timeline_next_stale"] = bool(nxt.get("stale"))
            specs.append(spec)
        return specs

    def generate_selected(self):
        if not self._ensure_project_setup_for_first_edit():
            return False

        # Guard this synchronous preparation path. Whatever happens after the
        # button enters "Preparing regeneration…", the finally block below must
        # restore it. Validation warnings and queue-preparation failures used to
        # return early before cleanup, leaving Timeline permanently locked until
        # the app was restarted.
        if getattr(self, "_regenerate_selected_busy", False):
            return False
        self._regenerate_selected_busy = True
        old_button_text = self.generate_selected_btn.text() if hasattr(self, "generate_selected_btn") else "(Re)generate selected block"
        if hasattr(self, "generate_selected_btn"):
            self.generate_selected_btn.setEnabled(False)
            self.generate_selected_btn.setText("Preparing regeneration…")
        QApplication.processEvents()

        try:
            clip = self._selected_clip()
            idx = self._selected_index()
            if clip is None or idx < 0:
                return False
            if bool(clip.get("locked", False)):
                QMessageBox.information(self, "Timeline edit", "This block is locked. Unlock it before regenerating it.")
                return False

            if str(clip.get("generation_mode") or "") == "source":
                QMessageBox.information(
                    self,
                    "Timeline edit",
                    "The loaded start clip is a source video, not an H3 generation. Choose another block to regenerate it.",
                )
                return False

            prompt = _compiled_prompt(clip)
            if not prompt:
                QMessageBox.warning(self, "Timeline not ready", "The selected clip has no prompt.")
                return False
            if int(clip.get("frames") or 0) not in self.frame_values:
                QMessageBox.warning(self, "Timeline not ready", "The selected clip has an invalid H3 frame count.")
                return False

            specs = self.generation_specs()
            spec = specs[idx]
            spec["timeline_single_regeneration"] = True
            edit_mode = str(
                clip.get("edit_mode")
                or ("continue_previous" if clip.get("generation_mode") == "continue" else "standalone")
            )
            use_previous = edit_mode in {"bridge_both", "continue_previous"}
            use_next = edit_mode in {"bridge_both", "anchor_next"}

            if bool(clip.get("use_reference_images", False)) and use_previous:
                QMessageBox.warning(
                    self,
                    "Timeline edit",
                    "Reference images use Ref2VA and cannot continue from the previous block. Choose a non-previous edit mode.",
                )
                return False

            spec["edit_mode"] = edit_mode
            spec["generation_mode"] = "continue" if use_previous else "new"
            spec["match_next_first_frame"] = bool(use_next)

            if use_previous:
                if idx == 0:
                    QMessageBox.warning(
                        self,
                        "Timeline edit",
                        "This replacement mode needs a previous block, but the selected block is first.",
                    )
                    return False
                prev = self._clips()[idx - 1]
                prev_output = Path(str(prev.get("output") or ""))
                if (
                    str(prev.get("status") or "") != "finished"
                    or bool(prev.get("stale"))
                    or not prev_output.is_file()
                ):
                    QMessageBox.warning(
                        self,
                        "Timeline edit",
                        "The previous block must have a valid finished result before this replacement can continue from it.",
                    )
                    return False

            if use_next:
                if idx + 1 >= len(self._clips()):
                    QMessageBox.warning(
                        self,
                        "Timeline edit",
                        "This replacement mode needs a next block, but the selected block is last.",
                    )
                    return False
                nxt = self._clips()[idx + 1]
                next_output = Path(str(nxt.get("output") or ""))

                # A next block used only as an END ANCHOR does not need to be
                # current in the continuation chain. Deleting/recreating earlier
                # clips may legitimately mark it stale, but its saved first frame
                # is still a perfectly valid visual destination. Require only an
                # existing finished result file here.
                if str(nxt.get("status") or "") != "finished" or not next_output.is_file():
                    QMessageBox.warning(
                        self,
                        "Timeline edit",
                        "The next block must have a finished result file before its first frame can anchor this replacement.",
                    )
                    return False

            if not callable(self.queue_timeline_callback):
                QMessageBox.warning(self, "Timeline", "The timeline is not connected to the MiniMax queue.")
                return False

            self._record_undo_state("Regenerate selected clip")
            try:
                result = self.queue_timeline_callback([spec])
            except Exception as exc:
                QMessageBox.critical(
                    self,
                    "Timeline regeneration failed",
                    f"Could not prepare the selected block for regeneration:\n\n{exc}",
                )
                return False

            if result:
                active_hq = self.project.get("hq_restart_override") or {}
                if isinstance(active_hq, dict) and active_hq:
                    clip["hq_generated"] = True
                self._invalidate_assembly("Selected clip regenerated — assemble again when ready.")

                # A free-ending continuation changes the boundary consumed by the
                # next continuation clip, so downstream continuation results really
                # do become stale.
                if edit_mode == "continue_previous":
                    for j in range(idx + 1, len(self._clips())):
                        if self._clips()[j].get("generation_mode") != "continue":
                            break
                        if (
                            self._clips()[j].get("queue_job_id")
                            or self._clips()[j].get("status") in {"pending", "running", "finished"}
                        ):
                            self._clips()[j]["stale"] = True

                # bridge_both / anchor_next are specifically chosen to PRESERVE the
                # existing next clip by targeting its exact first frame. If that
                # next clip was marked stale only because an earlier clip was
                # deleted/recreated, keeping that stale flag defeats the whole point
                # of the anchored replacement and makes Assemble Video demand an
                # unnecessary re-render.
                #
                # The next clip's output is not changed here. Once it is accepted as
                # the destination anchor, its outgoing boundary is also unchanged, so
                # an existing contiguous finished continuation chain after it remains
                # valid as well. Clear only clips that still have real finished files.
                elif edit_mode in {"bridge_both", "anchor_next"}:
                    # Do not decide this at queue time. The replacement still
                    # has to render successfully. sync_queue_jobs() will clear
                    # the preserved chain when this clip reaches "finished".
                    clip["_preserve_downstream_on_finish"] = True

                self._refresh_all()
            else:
                QMessageBox.warning(
                    self,
                    "Timeline regeneration did not queue",
                    "The selected block was not added to the queue. Check the MiniMax log for the validation message; no duplicate regeneration request was started.",
                )
            return bool(result)

        finally:
            self._regenerate_selected_busy = False
            if hasattr(self, "generate_selected_btn"):
                self.generate_selected_btn.setText(old_button_text)
                self._refresh_edit_workflow(self._selected_clip())

    def _clip_has_usable_output(self, clip: dict) -> bool:
        """Return True only when this block already has a video that can be reused."""
        if str(clip.get("generation_mode") or "") == "source":
            source = str(clip.get("start_source_video") or clip.get("output") or "")
            return bool(source and Path(source).is_file())
        output = str(clip.get("output") or "")
        return bool(output and Path(output).is_file())

    def _validate_generation_indices(self, indices: list[int]):
        """Validate only blocks that are part of this Generate Timeline request.

        This deliberately does not validate unrelated earlier blocks for
        'start from selected'. A selected continuation block is allowed to run
        without its previous block; _generation_specs_for_indices() detaches the
        first queued block when no usable predecessor exists.
        """
        clips = self._clips()
        if not clips:
            return False, "Timeline has no clips."
        if not indices:
            return False, "There are no clips to generate for this selection."

        for i in indices:
            if i < 0 or i >= len(clips):
                continue
            clip = clips[i]
            mode = str(clip.get("generation_mode") or "new")
            name = str(clip.get("name") or f"Clip {i + 1}")
            if mode == "source":
                source = str(clip.get("start_source_video") or clip.get("output") or "")
                if not source or not Path(source).is_file():
                    return False, f"{name} is a loaded start clip, but its video file is missing."
                continue
            prompt = _compiled_prompt(clip)
            if not prompt:
                return False, f"{name} has no prompt."
            if bool(clip.get("use_reference_images", False)):
                refs = _reference_entries(clip)
                if not refs:
                    return False, f"{name} has reference mode enabled but no reference images."
                if len(refs) > 5:
                    return False, f"{name} has more than 5 reference images."
                missing = [r["path"] for r in refs if not Path(r["path"]).is_file()]
                if missing:
                    return False, f"{name} has a missing reference image: {missing[0]}"
                if mode == "continue":
                    return False, f"{name} uses reference images and cannot Continue Previous Clip."
            if int(clip.get("frames") or 0) not in self.frame_values:
                return False, f"{name} has an invalid H3 frame count."
        return True, ""

    def _generation_specs_for_indices(self, indices: list[int]) -> list[dict]:
        """Build a queue batch for an arbitrary subset of Timeline blocks.

        Original timeline_index values are preserved so queue/result mapping
        still targets the correct blocks. If the first selected continuation
        block has no usable predecessor outside the batch, generate it as a
        standalone start for this run instead of refusing to queue the batch.
        """
        all_specs = self.generation_specs()
        wanted = {int(i) for i in indices}
        specs = [copy.deepcopy(all_specs[i]) for i in indices if 0 <= i < len(all_specs)]
        clips = self._clips()

        for spec in specs:
            i = int(spec.get("timeline_index", -1))
            if i <= 0 or str(spec.get("generation_mode") or "") != "continue":
                continue
            prev_index = i - 1
            # If the predecessor is also part of this queue batch, normal queue
            # chaining can connect the jobs in sequence. If it is not in this
            # batch, only keep Continue mode when a real previous video exists.
            if prev_index in wanted:
                continue
            previous = clips[prev_index] if 0 <= prev_index < len(clips) else None
            if previous is not None and self._clip_has_usable_output(previous):
                continue
            spec["generation_mode"] = "new"
            spec["edit_mode"] = "standalone"
            spec["timeline_previous_output"] = ""
            spec["timeline_previous_job_id"] = ""
            spec["timeline_previous_status"] = ""
            spec["timeline_previous_stale"] = False
            spec["timeline_previous_is_source"] = False
            spec["timeline_detached_start"] = True
        return specs

    def generate_timeline(self, mode="all"):
        if not self._ensure_project_setup_for_first_edit():
            return False
        if not callable(self.queue_timeline_callback):
            QMessageBox.warning(self, "Timeline", "The timeline is not connected to the MiniMax queue.")
            return False

        clips = self._clips()
        if mode == "missing":
            indices = [i for i, clip in enumerate(clips) if not bool(clip.get("locked", False)) and not self._clip_has_usable_output(clip)]
            if not indices:
                if any(bool(c.get("locked", False)) and not self._clip_has_usable_output(c) for c in clips):
                    QMessageBox.information(self, "Generate Timeline", "There are no unlocked missing clips to generate. Locked missing clips were left untouched.")
                else:
                    QMessageBox.information(self, "Generate Timeline", "All timeline clips already have usable output files.")
                return False
            undo_label = "Generate missing timeline clips"
        elif mode == "selected_only":
            selected_indices = self._selected_indices()
            if not selected_indices:
                QMessageBox.information(self, "Generate Timeline", "Select one or more timeline blocks first.")
                return False
            indices = [i for i in selected_indices if not bool(clips[i].get("locked", False))]
            if not indices:
                QMessageBox.information(self, "Generate Timeline", "All selected clips are locked. Unlock at least one selected clip to generate it.")
                return False
            undo_label = "Generate selected timeline clips"
        elif mode == "selected_onward":
            selected = self._selected_index()
            if selected < 0:
                QMessageBox.information(self, "Generate Timeline", "Select a timeline block first.")
                return False
            indices = [i for i in range(selected, len(clips)) if not bool(clips[i].get("locked", False))]
            undo_label = "Generate timeline from selected block"
        else:
            indices = [i for i, clip in enumerate(clips) if not bool(clip.get("locked", False))]
            undo_label = "Generate all timeline clips"

        if not indices:
            QMessageBox.information(self, "Generate Timeline", "There are no unlocked clips to generate for this selection.")
            return False
        ok, error = self._validate_generation_indices(indices)
        if not ok:
            QMessageBox.warning(self, "Timeline not ready", error)
            return False

        specs = self._generation_specs_for_indices(indices)
        if not specs:
            QMessageBox.information(self, "Generate Timeline", "There are no clips to generate for this selection.")
            return False

        self._record_undo_state(undo_label)
        result = self.queue_timeline_callback(specs)
        if result:
            active_hq = self.project.get("hq_restart_override") or {}
            if isinstance(active_hq, dict) and active_hq:
                for i in indices:
                    if 0 <= i < len(clips) and str(clips[i].get("generation_mode") or "") != "source":
                        clips[i]["hq_generated"] = True
            self.project["assembled_output"] = ""
            self.project["assembly_status"] = ""
            self.project["auto_assemble"] = False
            self.project["auto_assemble_pending"] = False
            self._refresh_all()
        return bool(result)

    def hq_restart(self):
        """Requeue the complete timeline with one HQ resolution override.

        Every other generation setting remains owned by each timeline clip. The
        orientation is taken from the live MiniMax GUI for normal HQ presets so a
        9:16 or 1:1 project stays in that orientation. Explicit 21:9 choices force
        21:9 because those entries already describe their complete output shape.
        """
        if not self._ensure_project_setup_for_first_edit():
            return False
        unlocked_indices = [i for i, clip in enumerate(self._clips()) if not bool(clip.get("locked", False))]
        if not unlocked_indices:
            QMessageBox.information(self, "HQ restart", "Every timeline block is locked. Unlock the blocks you want to recreate in HQ.")
            return False
        ok, error = self._validate_generation_indices(unlocked_indices)
        if not ok:
            QMessageBox.warning(self, "Timeline not ready", error)
            return False
        if not callable(self.queue_timeline_callback):
            QMessageBox.warning(self, "Timeline", "The timeline is not connected to the MiniMax queue.")
            return False

        choices = [
            "1280x704",
            "1344x768",
            "1920x1080",
            "1344x576 (21:9)",
            "1792x768 (21:9)",
        ]
        choice, accepted = QInputDialog.getItem(
            self,
            "HQ restart",
            "Select HQ resolution:",
            choices,
            0,
            False,
        )
        if not accepted:
            return False

        current = self._capture_settings()
        current_aspect = str(current.get("aspect") or "16:9")
        if current_aspect not in {"9:16", "1:1"}:
            current_aspect = "16:9"

        if choice == "1280x704":
            override = {"aspect": current_aspect, "resolution": "1280 × 720"}
        elif choice == "1344x768":
            override = {"aspect": current_aspect, "resolution": "1344 × 768"}
        elif choice == "1920x1080":
            # The GUI's 1080p choice intentionally maps to MiniMax's valid
            # 1920x1088 tensor size, exactly like the normal Generation tab.
            override = {"aspect": current_aspect, "resolution": "1920 × 1088"}
        elif choice == "1344x576 (21:9)":
            override = {"aspect": "21:9", "widescreen_quality": "Medium — 1344 × 576"}
        else:
            override = {"aspect": "21:9", "widescreen_quality": "High — 1792 × 768"}

        specs = self._generation_specs_for_indices(unlocked_indices)
        for spec in specs:
            spec["timeline_hq_override"] = copy.deepcopy(override)

        self._record_undo_state("HQ restart")
        result = self.queue_timeline_callback(specs)
        if result:
            # Persist the chosen HQ mode at project level. This is saved in the
            # timeline JSON and is automatically re-applied by generation_specs()
            # for later single-clip regeneration after an app restart.
            self.project["hq_restart_override"] = copy.deepcopy(override)
            for i in unlocked_indices:
                clip = self._clips()[i]
                if str(clip.get("generation_mode") or "") != "source":
                    clip["hq_generated"] = True
            self.project["assembled_output"] = ""
            self.project["assembly_status"] = ""
            self.project["auto_assemble"] = False
            self.project["auto_assemble_pending"] = False
            self._refresh_all()
        return bool(result)

    def mark_queued(self, clip_id, job_id, output="", job_info=None):
        for clip in self._clips():
            if str(clip.get("id")) == str(clip_id):
                clip["queue_job_id"] = str(job_id)
                clip["status"] = "pending"
                clip["stale"] = False
                clip["output"] = str(output or "")
                # A trim belongs to the previous rendered version. A new render
                # gets a clean full-range assembly state.
                clip.pop("trim_in", None); clip.pop("trim_out", None)
                info = self._generation_info_from_job(job_info)
                if info:
                    clip["last_generation_info"] = info
                break
        self._refresh_all()

    def sync_queue_jobs(self, jobs):
        by_id = {str(j.get("id")): j for j in (jobs or []) if j.get("id")}
        changed = False
        clips = self._clips()
        for idx, clip in enumerate(clips):
            job_id = clip.get("queue_job_id")
            if not job_id or str(job_id) not in by_id:
                continue
            job = by_id[str(job_id)]
            state = str(job.get("state") or "pending")
            previous_state = str(clip.get("status") or "")
            if previous_state != state:
                clip["status"] = state; changed = True
            output = str(job.get("output") or "")
            if output and clip.get("output") != output:
                clip["output"] = output; changed = True
            job_info = self._generation_info_from_job(job)
            if job_info and clip.get("last_generation_info") != job_info:
                clip["last_generation_info"] = job_info; changed = True

            if state == "finished" and bool(clip.get("_preserve_downstream_on_finish")):
                clip.pop("_preserve_downstream_on_finish", None)
                cleared = self._accept_preserved_downstream_chain(idx)
                if cleared:
                    print(
                        f"[TIMELINE] Anchored replacement finished; preserved {cleared} existing downstream clip(s).",
                        flush=True,
                    )
                    changed = True
            elif state in {"failed", "cancelled", "canceled"}:
                clip.pop("_preserve_downstream_on_finish", None)

        if changed:
            self._refresh_all()
