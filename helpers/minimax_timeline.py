from __future__ import annotations

import copy
import json
import math
import re
import uuid
from pathlib import Path

from PySide6.QtCore import Qt, Signal, QSize, QTimer
from PySide6.QtGui import QColor, QPainter, QPen, QBrush, QFontMetrics
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
    QSlider,
)

FPS = 24.0
MIN_SEGMENT_SECONDS = 0.3
# Backward-compatible internal alias; project JSON still uses the existing "segments" key.
MIN_CUT_SECONDS = MIN_SEGMENT_SECONDS
TIMELINE_SCHEMA_VERSION = 1


def _safe_project_name(text: str) -> str:
    text = re.sub(r"[^A-Za-z0-9._ -]+", "_", str(text or "").strip())
    return text or "MiniMax Timeline"


def _clip_seconds(clip: dict) -> float:
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


class TimelineCanvas(QWidget):
    """Compact NLE-like overview of MiniMax H3 generation jobs."""

    clipSelected = Signal(str)
    clipsReordered = Signal(int, int)
    clipFramesChanged = Signal(str, int)

    RULER_H = 30
    CLIP_TOP = 40
    CLIP_H = 116
    BOTTOM_PAD = 16

    def __init__(self, parent=None):
        super().__init__(parent)
        self.clips: list[dict] = []
        self.selected_id: str | None = None
        self.pixels_per_second = 38.0
        self.allowed_frames: list[int] = list(range(124, 720, 17))
        self._rects: list[tuple[str, float, float]] = []
        self._press_x = 0.0
        self._press_clip_index = -1
        self._dragging = False
        self._resizing = False
        self.setMinimumHeight(self.CLIP_TOP + self.CLIP_H + self.BOTTOM_PAD)
        self.setMouseTracking(True)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def sizeHint(self):
        return QSize(max(900, self._content_width()), self.CLIP_TOP + self.CLIP_H + self.BOTTOM_PAD)

    def _content_width(self) -> int:
        total = sum(_clip_seconds(c) for c in self.clips)
        return int(max(900, 36 + total * self.pixels_per_second + 36))

    def set_clips(self, clips: list[dict], selected_id: str | None = None):
        self.clips = clips
        if selected_id is not None:
            self.selected_id = selected_id
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
        fm = QFontMetrics(painter.font())
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
            selected = str(clip.get("id")) == self.selected_id
            status = "stale" if clip.get("stale") else str(clip.get("status") or "draft")
            fill = state_colors.get(status, panel)
            painter.setBrush(QBrush(fill))
            painter.setPen(QPen(accent if selected else border, 2 if selected else 1))
            painter.drawRoundedRect(int(left), self.CLIP_TOP, int(width - 4), self.CLIP_H, 5, 5)

            # Clip header.
            painter.setPen(accent_text if selected else text)
            name = str(clip.get("name") or f"Clip {idx + 1}")
            name_width = int(max(20, width - 18))
            elided = fm.elidedText(name, Qt.TextElideMode.ElideRight, name_width)
            painter.drawText(int(left + 8), self.CLIP_TOP + 18, elided)
            mode = "↪ Continue" if clip.get("generation_mode") == "continue" else "◆ New"
            bridge = "  •  ⇥ Next frame" if clip.get("match_next_first_frame") else ""
            info = f"{mode}  •  {int(clip.get('frames') or 0)}f  •  {duration:.2f}s{bridge}"
            painter.setPen(muted if not selected else accent_text)
            painter.drawText(int(left + 8), self.CLIP_TOP + 38, fm.elidedText(info, Qt.TextElideMode.ElideRight, name_width))

            # Show a compact prompt preview. A timeline block is one complete H3 job;
            # shot/timestamp structure stays inside the normal H3 prompt itself.
            prompt_y = self.CLIP_TOP + 53
            prompt_h = 42
            painter.setBrush(QBrush(QColor("#375a7f")))
            painter.setPen(QPen(bg, 1))
            painter.drawRect(int(left + 5), prompt_y, max(1, int(width - 14)), prompt_h)
            prompt = _compiled_prompt(clip).replace("\n", " ").strip() or "Empty prompt"
            painter.setPen(QColor("#f4f6f8"))
            painter.drawText(
                int(left + 10), prompt_y + 26,
                fm.elidedText(prompt, Qt.TextElideMode.ElideRight, int(max(12, width - 24)))
            )

            if clip.get("generation_mode") == "continue" and idx > 0:
                painter.setPen(QPen(accent, 2))
                painter.drawLine(int(left - 12), self.CLIP_TOP + 20, int(left - 3), self.CLIP_TOP + 20)
                painter.drawLine(int(left - 7), self.CLIP_TOP + 16, int(left - 3), self.CLIP_TOP + 20)
                painter.drawLine(int(left - 7), self.CLIP_TOP + 24, int(left - 3), self.CLIP_TOP + 20)

            cursor = right

        if not self.clips:
            painter.setPen(muted)
            painter.drawText(28, self.CLIP_TOP + 50, "No clips yet — add a generation clip to start the timeline.")

    def mousePressEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton:
            return super().mousePressEvent(event)
        x = event.position().x()
        idx, left, right = self._clip_at(x)
        if idx < 0:
            return
        self._press_x = x
        self._press_clip_index = idx
        self._dragging = False
        self._resizing = (right - x) <= 10 and len(self.clips) > 0
        clip_id = str(self.clips[idx].get("id"))
        self.selected_id = clip_id
        self.clipSelected.emit(clip_id)
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
        self.selected_cut_index = 0
        self._loading_inspector = False
        self._project_path: Path | None = None
        self._build_ui()
        self._ensure_initial_clip()
        self._refresh_all(select_first=True)

        # Autosave timeline projects once per minute. Before the user chooses a
        # project file, keep a quiet recovery copy next to this module; after a
        # normal Save/Load establishes a project path, autosave writes there.
        self._autosave_temp_path = Path(__file__).resolve().parent / "minimax_timeline_autosave_temp.json"
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
            "clips": [],
            "assembled_output": "",
            "assembly_status": "",
            "auto_assemble": True,
            "auto_assemble_pending": False,
        }

    def _capture_settings(self):
        if callable(self.settings_provider):
            try:
                data = self.settings_provider()
                return copy.deepcopy(data) if isinstance(data, dict) else {}
            except Exception:
                return {}
        return {}

    def _blank_clip(self, *, continue_previous=False, source_settings=None):
        settings = copy.deepcopy(source_settings if isinstance(source_settings, dict) else self._capture_settings())
        frames = int(settings.get("frames") or 243)
        frames = min(self.frame_values, key=lambda v: abs(v - frames)) if self.frame_values else frames
        prompt = str(settings.get("prompt") or "")
        return {
            "id": uuid.uuid4().hex,
            "name": "",
            "frames": frames,
            "generation_mode": "continue" if continue_previous else "new",
            "match_next_first_frame": False,
            "segments": [{"id": uuid.uuid4().hex, "prompt": prompt, "weight": 1.0}],
            "settings": settings,
            "status": "draft",
            "stale": False,
            "queue_job_id": None,
            "output": "",
        }

    def _clips(self):
        return self.project.setdefault("clips", [])

    def _ensure_initial_clip(self):
        if not self._clips():
            clip = self._blank_clip(continue_previous=False)
            clip["name"] = "Clip 1"
            self._clips().append(clip)
            self.selected_clip_id = clip["id"]

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
        clips = self._clips()
        if not (0 <= index < len(clips)):
            return
        clip = clips[index]
        self._invalidate_assembly()
        if clip.get("queue_job_id") or clip.get("status") in {"pending", "running", "finished"}:
            clip["stale"] = True
        # A bridge replacement is deliberately anchored to the already-rendered
        # first frame of the next clip.  In that case the edited clip itself is
        # stale, but the preserved downstream video does not have to be thrown
        # away just because this clip changed.
        if propagate and not bool(clip.get("match_next_first_frame", False)):
            for j in range(index + 1, len(clips)):
                if clips[j].get("generation_mode") != "continue":
                    break
                if clips[j].get("queue_job_id") or clips[j].get("status") in {"pending", "running", "finished"}:
                    clips[j]["stale"] = True

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
        clip["settings"] = settings
        frames = int(settings.get("frames") or clip.get("frames") or 243)
        if self.frame_values:
            frames = min(self.frame_values, key=lambda v: abs(v - frames))
        clip["frames"] = frames
        if len(clip.get("segments") or []) == 1 and not str(clip["segments"][0].get("prompt") or "").strip():
            clip["segments"][0]["prompt"] = str(settings.get("prompt") or "")
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
        self.add_clip_btn = QPushButton("+ Clip")
        self.dup_clip_btn = QPushButton("Duplicate")
        self.del_clip_btn = QPushButton("Delete")
        self.left_btn = QPushButton("◀")
        self.right_btn = QPushButton("▶")
        for w in (self.new_btn, self.save_btn, self.load_btn, self.add_clip_btn, self.dup_clip_btn, self.del_clip_btn, self.left_btn, self.right_btn):
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
            "Queue every clip on this timeline as a separate MiniMax H3 generation in timeline order."
        )
        self.generate_selected_btn = QPushButton("Generate Selected")
        self.generate_selected_btn.setMinimumHeight(36)
        self.generate_selected_btn.setToolTip(
            "Regenerate only the selected timeline clip. Continue clips use the finished previous timeline clip as their source."
        )
        self.assemble_timeline_btn = QPushButton("Assemble Video")
        self.assemble_timeline_btn.setMinimumHeight(36)
        self.assemble_timeline_btn.setToolTip(
            "Join the finished timeline clip outputs, in timeline order, into one final MP4."
        )
        self.auto_assemble_check = QCheckBox("Auto assemble when finished")
        self.auto_assemble_check.setChecked(bool(self.project.get("auto_assemble", True)))
        self.auto_assemble_check.setToolTip(
            "After Generate Timeline, automatically assemble the final MP4 when every timeline clip has finished."
        )
        runbar.addWidget(self.generate_timeline_btn)
        runbar.addWidget(self.generate_selected_btn)
        runbar.addWidget(self.assemble_timeline_btn)
        runbar.addWidget(self.auto_assemble_check)
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
        tl.addWidget(self.timeline_scroll, 0)

        project_box = QGroupBox("Project")
        pf = QFormLayout(project_box)
        self.project_name = QLineEdit()
        self.project_file_label = QLabel("Not saved")
        self.project_file_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.final_output_label = QLabel("Not assembled")
        self.final_output_label.setWordWrap(True)
        self.final_output_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        final_actions = QWidget()
        final_actions_l = QHBoxLayout(final_actions)
        final_actions_l.setContentsMargins(0, 0, 0, 0)
        final_actions_l.setSpacing(6)
        self.preview_final_btn = QPushButton("Preview final")
        self.open_final_btn = QPushButton("Open folder")
        final_actions_l.addWidget(self.preview_final_btn)
        final_actions_l.addWidget(self.open_final_btn)
        final_actions_l.addStretch(1)
        pf.addRow("Name", self.project_name)
        pf.addRow("File", self.project_file_label)
        pf.addRow("Final video", self.final_output_label)
        pf.addRow("", final_actions)
        tl.addWidget(project_box)
        tl.addStretch(1)

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
        self.clip_name = QLineEdit()
        self.gen_mode = QComboBox()
        self.gen_mode.addItem("New generation / new chain", "new")
        self.gen_mode.addItem("Continue previous clip", "continue")
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
        cf.addRow("Duration", self.frames_combo)
        cf.addRow("State", self.state_label)
        cf.addRow("Output", self.clip_output_label)
        cf.addRow("", clip_result_actions)
        iv.addWidget(clip_box)

        prompt_box = QGroupBox("Prompt")
        pv = QVBoxLayout(prompt_box)
        self.cut_prompt = QPlainTextEdit()
        self.cut_prompt.setPlaceholderText(
            "Prompt for this H3 generation. You can use the normal Prompt Builder output here, including multiple shots or timestamps."
        )
        self.cut_prompt.setMinimumHeight(240)
        pv.addWidget(self.cut_prompt)
        iv.addWidget(prompt_box)

        settings_box = QGroupBox("MiniMax settings")
        sf = QFormLayout(settings_box)
        self.seed_spin = QSpinBox(); self.seed_spin.setRange(-1, 2147483647)
        self.steps_spin = QSpinBox(); self.steps_spin.setRange(1, 100)
        self.scheduler_combo = QComboBox(); self.scheduler_combo.addItems(["simple", "beta"])
        self.glue_check = QCheckBox("Glue result to source")
        self.audio_memory_check = QCheckBox("Carry audio memory")
        self.latent_check = QCheckBox("Latent continuation")
        self.match_next_check = QCheckBox("Use next video first frame as last frame")
        self.match_next_check.setToolTip(
            "Bridge replacement mode. The selected clip starts from its previous continuation source but is forced to end on the exact first frame of the already-rendered next timeline clip. This lets you replace a middle clip without regenerating the preserved clips after it. Turn it off for a free ending / hard cut."
        )
        self.capture_btn = QPushButton("Capture current Generation-tab settings")
        self.model_label = QLabel("—"); self.model_label.setWordWrap(True)
        self.refs_label = QLabel("—"); self.refs_label.setWordWrap(True)
        self.loras_label = QLabel("—"); self.loras_label.setWordWrap(True)
        sf.addRow("", self.capture_btn)
        sf.addRow("Seed", self.seed_spin)
        sf.addRow("Steps", self.steps_spin)
        sf.addRow("Scheduler", self.scheduler_combo)
        sf.addRow("", self.glue_check)
        sf.addRow("", self.audio_memory_check)
        sf.addRow("", self.latent_check)
        sf.addRow("", self.match_next_check)
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
        self.generate_timeline_btn.clicked.connect(self.generate_timeline)
        self.generate_selected_btn.clicked.connect(self.generate_selected)
        self.assemble_timeline_btn.clicked.connect(self.assemble_timeline)
        self.auto_assemble_check.toggled.connect(self._auto_assemble_changed)
        self.preview_clip_btn.clicked.connect(self.preview_selected_result)
        self.open_clip_btn.clicked.connect(self.open_selected_output)
        self.preview_final_btn.clicked.connect(self.preview_final_result)
        self.open_final_btn.clicked.connect(self.open_final_output)
        self.add_clip_btn.clicked.connect(self.add_clip)
        self.dup_clip_btn.clicked.connect(self.duplicate_clip)
        self.del_clip_btn.clicked.connect(self.delete_clip)
        self.left_btn.clicked.connect(lambda: self.move_clip(-1))
        self.right_btn.clicked.connect(lambda: self.move_clip(1))
        self.zoom_slider.valueChanged.connect(self.canvas.set_zoom)
        self.canvas.clipSelected.connect(self.select_clip)
        self.canvas.clipsReordered.connect(self._reorder_clips)
        self.canvas.clipFramesChanged.connect(self._canvas_frames_changed)
        self.project_name.textEdited.connect(self._project_name_changed)
        self.clip_name.textEdited.connect(self._clip_name_changed)
        self.gen_mode.currentIndexChanged.connect(self._mode_changed)
        self.frames_combo.currentIndexChanged.connect(self._frames_changed)
        self.seed_spin.valueChanged.connect(self._settings_changed)
        self.steps_spin.valueChanged.connect(self._settings_changed)
        self.scheduler_combo.currentTextChanged.connect(self._settings_changed)
        self.glue_check.toggled.connect(self._settings_changed)
        self.audio_memory_check.toggled.connect(self._settings_changed)
        self.latent_check.toggled.connect(self._settings_changed)
        self.match_next_check.toggled.connect(self._match_next_changed)
        self.capture_btn.clicked.connect(self.capture_current_settings)
        self.cut_prompt.textChanged.connect(self._cut_prompt_changed)

    # -------------------------------------------------------------- refreshers
    def _refresh_all(self, *, select_first=False):
        if select_first and self._clips() and not self.selected_clip_id:
            self.selected_clip_id = self._clips()[0]["id"]
        if self.selected_clip_id and not any(c.get("id") == self.selected_clip_id for c in self._clips()):
            self.selected_clip_id = self._clips()[0]["id"] if self._clips() else None
        self.project_name.blockSignals(True)
        self.project_name.setText(str(self.project.get("name") or "MiniMax Timeline"))
        self.project_name.blockSignals(False)
        total = sum(_clip_seconds(c) for c in self._clips())
        self.summary_label.setText(f"{len(self._clips())} clips  •  {total:.2f}s  •  {round(total * FPS)} timeline frames")
        self.generate_timeline_btn.setEnabled(bool(self._clips()))
        ready, reason = self._assembly_ready()
        self.assemble_timeline_btn.setEnabled(bool(self._clips()))
        self.assemble_timeline_btn.setToolTip(
            "Join the finished timeline clip outputs, in timeline order, into one final MP4."
            if ready else reason
        )
        self.auto_assemble_check.blockSignals(True)
        self.auto_assemble_check.setChecked(bool(self.project.get("auto_assemble", True)))
        self.auto_assemble_check.blockSignals(False)
        final_path = str(self.project.get("assembled_output") or "")
        final_exists = bool(final_path and Path(final_path).is_file())
        final_status = str(self.project.get("assembly_status") or "").strip()
        if final_exists:
            self.final_output_label.setText(final_path)
        elif final_status:
            self.final_output_label.setText(final_status)
        else:
            self.final_output_label.setText("Not assembled")
        self.preview_final_btn.setEnabled(final_exists)
        self.open_final_btn.setEnabled(final_exists)
        self.canvas.set_clips(self._clips(), self.selected_clip_id)
        self._load_inspector()

    def _load_inspector(self):
        clip = self._selected_clip()
        self._loading_inspector = True
        try:
            enabled = clip is not None
            for w in (self.clip_name, self.gen_mode, self.frames_combo, self.seed_spin, self.steps_spin,
                      self.scheduler_combo, self.glue_check, self.audio_memory_check, self.latent_check, self.match_next_check,
                      self.capture_btn, self.cut_prompt):
                w.setEnabled(enabled)
            if clip is None:
                self.state_label.setText("No clip selected")
                self.clip_output_label.setText("—")
                self.preview_clip_btn.setEnabled(False); self.open_clip_btn.setEnabled(False)
                self.cut_prompt.clear()
                return
            settings = clip.setdefault("settings", {})
            self.clip_name.setText(str(clip.get("name") or ""))
            idx = self.gen_mode.findData(clip.get("generation_mode") or "new")
            self.gen_mode.setCurrentIndex(max(0, idx))
            fidx = self.frames_combo.findData(int(clip.get("frames") or 243))
            if fidx >= 0: self.frames_combo.setCurrentIndex(fidx)
            self.seed_spin.setValue(int(settings.get("seed", -1)))
            self.steps_spin.setValue(int(settings.get("steps", 15)))
            sched = str(settings.get("scheduler") or "beta")
            if self.scheduler_combo.findText(sched) < 0: self.scheduler_combo.addItem(sched)
            self.scheduler_combo.setCurrentText(sched)
            self.glue_check.setChecked(bool(settings.get("glue_results", False)))
            self.audio_memory_check.setChecked(bool(settings.get("continue_audio_memory", True)))
            self.latent_check.setChecked(bool(settings.get("latent_continuation", False)))
            self.match_next_check.setChecked(bool(clip.get("match_next_first_frame", False)))
            selected_idx = self._selected_index()
            has_next = 0 <= selected_idx < len(self._clips()) - 1
            self.match_next_check.setEnabled(has_next)
            if has_next:
                next_clip = self._clips()[selected_idx + 1]
                next_ready = str(next_clip.get("status") or "") == "finished" and bool(next_clip.get("output")) and not bool(next_clip.get("stale"))
                self.match_next_check.setToolTip(
                    "Bridge replacement mode. Uses the exact first frame of the already-rendered next timeline clip as this clip's last-frame destination."
                    + ("" if next_ready else " The next clip does not currently have a valid finished result, so bridge generation will be blocked until it does.")
                )
            state = str(clip.get("status") or "draft").title()
            if clip.get("stale"): state = "Stale — edited after queue/render"
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
        model = s.get("hybrid_model") if use_hybrid else (s.get("ref2va_model") if int(s.get("mode", 0)) == 2 else s.get("fl2va_model"))
        self.model_label.setText(Path(str(model)).name if model else "Default / auto-resolved")
        refs = list(s.get("ref_images") or []) + list(s.get("ref_videos") or []) + list(s.get("ref_audios") or [])
        if s.get("first"): refs.insert(0, s.get("first"))
        if s.get("last"): refs.append(s.get("last"))
        self.refs_label.setText(f"{len(refs)} source/reference item(s)" if refs else "None captured")
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

    def new_project(self):
        if self._clips() and any(str(s.get("prompt") or "").strip() for c in self._clips() for s in c.get("segments") or []):
            if QMessageBox.question(self, "New timeline", "Clear the current timeline and start a new project?") != QMessageBox.StandardButton.Yes:
                return
        self.project = self._new_project_data()
        self._project_path = None
        self.project_file_label.setText("Not saved")
        self.selected_clip_id = None
        self.selected_cut_index = 0
        self._ensure_initial_clip()
        self._refresh_all(select_first=True)

    def save_project(self):
        if not self._project_path:
            suggested = _safe_project_name(self.project.get("name")).replace(" ", "_") + ".json"
            name, _ = QFileDialog.getSaveFileName(self, "Save MiniMax timeline", suggested, "MiniMax Timeline (*.json);;JSON (*.json)")
            if not name:
                return
            self._project_path = Path(name)
        try:
            self._write_project_json(self._project_path)
        except Exception as exc:
            QMessageBox.critical(self, "Save timeline failed", str(exc))
            return
        self.project_file_label.setText(str(self._project_path))
        # Once the project has a real file, the temporary recovery copy is no
        # longer authoritative. Future autosaves go to the saved JSON.
        try:
            if self._autosave_temp_path.exists():
                self._autosave_temp_path.unlink()
        except Exception:
            pass

    def load_project(self):
        name, _ = QFileDialog.getOpenFileName(self, "Load MiniMax timeline", "", "MiniMax Timeline (*.json);;JSON (*.json)")
        if not name:
            return
        try:
            data = json.loads(Path(name).read_text(encoding="utf-8"))
            if not isinstance(data, dict) or not isinstance(data.get("clips"), list):
                raise ValueError("File does not contain a MiniMax timeline project.")
            self.project = data
            self.project.setdefault("schema_version", TIMELINE_SCHEMA_VERSION)
            self.project.setdefault("project_id", uuid.uuid4().hex)
            self.project.setdefault("name", Path(name).stem)
            self.project.setdefault("assembled_output", "")
            self.project.setdefault("assembly_status", "")
            self.project.setdefault("auto_assemble", True)
            self.project.setdefault("auto_assemble_pending", False)
            for clip in self.project.get("clips") or []:
                clip.setdefault("match_next_first_frame", False)
            for clip in self._clips():
                clip.setdefault("id", uuid.uuid4().hex)
                clip.setdefault("name", "")
                clip.setdefault("generation_mode", "new")
                clip.setdefault("frames", 243)
                clip.setdefault("settings", {})
                clip.setdefault("status", "draft")
                clip.setdefault("stale", False)
                clip.setdefault("queue_job_id", None)
                clip.setdefault("output", "")
                if not clip.get("segments"):
                    clip["segments"] = [{"id": uuid.uuid4().hex, "prompt": "", "weight": 1.0}]
                for seg in clip["segments"]:
                    seg.setdefault("id", uuid.uuid4().hex); seg.setdefault("prompt", ""); seg.setdefault("weight", 1.0)
                if len(clip["segments"]) > 1:
                    merged_prompt = _compiled_prompt(clip)
                    clip["segments"] = [{"id": uuid.uuid4().hex, "prompt": merged_prompt, "weight": 1.0}]
            self._project_path = Path(name)
            self.project_file_label.setText(str(self._project_path))
            self.selected_clip_id = self._clips()[0]["id"] if self._clips() else None
            self.selected_cut_index = 0
            self._refresh_all(select_first=True)
        except Exception as exc:
            QMessageBox.critical(self, "Load timeline failed", str(exc))

    # ------------------------------------------------------------- clip actions
    def add_clip(self):
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
        self.selected_cut_index = 0
        self._renumber_default_names()
        self._refresh_all()

    def duplicate_clip(self):
        idx = self._selected_index()
        if idx < 0: return
        clone = copy.deepcopy(self._clips()[idx])
        clone["id"] = uuid.uuid4().hex
        clone["name"] = str(clone.get("name") or f"Clip {idx + 1}") + " copy"
        clone["queue_job_id"] = None; clone["output"] = ""; clone["status"] = "draft"; clone["stale"] = False
        for seg in clone.get("segments") or []: seg["id"] = uuid.uuid4().hex
        self._clips().insert(idx + 1, clone)
        self._invalidate_assembly()
        self.selected_clip_id = clone["id"]
        self._refresh_all()

    def delete_clip(self):
        idx = self._selected_index()
        if idx < 0: return
        clip = self._clips()[idx]
        has_content = any(str(s.get("prompt") or "").strip() for s in clip.get("segments") or [])
        if has_content and QMessageBox.question(self, "Delete clip", f"{clip.get('name')} contains prompt text. Delete it anyway?") != QMessageBox.StandardButton.Yes:
            return
        self._clips().pop(idx)
        self._invalidate_assembly()
        self._renumber_default_names()
        if self._clips():
            self.selected_clip_id = self._clips()[min(idx, len(self._clips()) - 1)]["id"]
        else:
            self.selected_clip_id = None
        self.selected_cut_index = 0
        self._repair_continuation_chain(mark_stale=True)
        self._refresh_all()

    def move_clip(self, delta):
        idx = self._selected_index()
        target = idx + int(delta)
        if idx < 0 or not (0 <= target < len(self._clips())): return
        self._reorder_clips(idx, target)

    def _reorder_clips(self, old, new):
        clips = self._clips()
        if not (0 <= old < len(clips) and 0 <= new < len(clips)): return
        item = clips.pop(old); clips.insert(new, item)
        self._invalidate_assembly()
        self._repair_continuation_chain(mark_stale=True)
        self._refresh_all()

    def _repair_continuation_chain(self, mark_stale=False):
        for i, clip in enumerate(self._clips()):
            if i == 0 and clip.get("generation_mode") == "continue":
                clip["generation_mode"] = "new"
                if mark_stale: clip["stale"] = True
            elif mark_stale and clip.get("generation_mode") == "continue" and clip.get("queue_job_id"):
                clip["stale"] = True

    def select_clip(self, clip_id):
        self.selected_clip_id = str(clip_id)
        self.selected_cut_index = 0
        self.canvas.selected_id = self.selected_clip_id
        self.canvas.update()
        self._load_inspector()

    # ---------------------------------------------------------- inspector slots
    def _project_name_changed(self, text):
        self.project["name"] = text

    def _clip_name_changed(self, text):
        if self._loading_inspector: return
        clip = self._selected_clip()
        if not clip: return
        clip["name"] = text
        self.canvas.update()

    def _mode_changed(self):
        if self._loading_inspector: return
        idx = self._selected_index(); clip = self._selected_clip()
        if not clip: return
        mode = self.gen_mode.currentData()
        if idx == 0 and mode == "continue":
            self._loading_inspector = True
            self.gen_mode.setCurrentIndex(self.gen_mode.findData("new"))
            self._loading_inspector = False
            QMessageBox.information(self, "Continuation", "The first timeline clip must start a chain. Add or select a source generation before using Continue Previous Clip.")
            return
        clip["generation_mode"] = mode
        self._touch_clip(idx)
        self._refresh_all()

    def _frames_changed(self):
        if self._loading_inspector: return
        clip = self._selected_clip(); idx = self._selected_index()
        if not clip: return
        frames = int(self.frames_combo.currentData() or clip.get("frames") or 243)
        if frames != int(clip.get("frames") or 0):
            clip["frames"] = frames
            clip.setdefault("settings", {})["frames"] = frames
            clip["settings"]["experimental_long_duration"] = frames > 719
            self._touch_clip(idx)
            self._refresh_all()

    def _canvas_frames_changed(self, clip_id, frames):
        for i, clip in enumerate(self._clips()):
            if str(clip.get("id")) == str(clip_id):
                clip["frames"] = int(frames)
                clip.setdefault("settings", {})["frames"] = int(frames)
                clip["settings"]["experimental_long_duration"] = int(frames) > 719
                self._touch_clip(i)
                break
        self._refresh_all()

    def _settings_changed(self, *args):
        if self._loading_inspector: return
        clip = self._selected_clip(); idx = self._selected_index()
        if not clip: return
        s = clip.setdefault("settings", {})
        s["seed"] = self.seed_spin.value(); s["steps"] = self.steps_spin.value(); s["scheduler"] = self.scheduler_combo.currentText()
        s["glue_results"] = self.glue_check.isChecked(); s["continue_audio_memory"] = self.audio_memory_check.isChecked(); s["latent_continuation"] = self.latent_check.isChecked()
        self._touch_clip(idx)
        self._refresh_all()

    def _match_next_changed(self, checked):
        if self._loading_inspector:
            return
        clip = self._selected_clip(); idx = self._selected_index()
        if not clip:
            return
        if idx < 0 or idx >= len(self._clips()) - 1:
            if checked:
                self._loading_inspector = True
                self.match_next_check.setChecked(False)
                self._loading_inspector = False
            clip["match_next_first_frame"] = False
            return
        clip["match_next_first_frame"] = bool(checked)
        self._touch_clip(idx, propagate=not bool(checked))
        self._refresh_all()

    def capture_current_settings(self):
        clip = self._selected_clip(); idx = self._selected_index()
        if not clip:
            return

        # Capture technical Generation-tab settings only. The timeline prompt is
        # authored independently and must never be replaced by whatever prompt
        # happens to be present on the Generation tab.
        current_prompt = _compiled_prompt(clip)
        current_segments = copy.deepcopy(clip.get("segments") or [])
        settings = self._capture_settings()
        settings.pop("prompt", None)
        clip["settings"] = settings

        frames = int(settings.get("frames") or clip.get("frames") or 243)
        if self.frame_values:
            frames = min(self.frame_values, key=lambda v: abs(v - frames))
        clip["frames"] = frames

        # Preserve the timeline prompt verbatim. Keep the existing segment id so
        # saved project state remains stable even though the UI exposes one prompt.
        if current_segments:
            clip["segments"] = current_segments
        else:
            clip["segments"] = [{
                "id": uuid.uuid4().hex,
                "prompt": current_prompt,
                "weight": 1.0,
            }]

        self._touch_clip(idx)
        self._refresh_all()

    # -------------------------------------------------------------- prompt edit
    def _cut_prompt_changed(self):
        if self._loading_inspector:
            return
        clip = self._selected_clip(); idx = self._selected_index()
        if not clip:
            return
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

    # --------------------------------------------------------------- results
    def _assembly_ready(self):
        clips = self._clips()
        if not clips:
            return False, "Timeline has no clips."
        for i, clip in enumerate(clips):
            name = str(clip.get("name") or f"Clip {i + 1}")
            if clip.get("stale"):
                return False, f"{name} is stale and must be regenerated first."
            if str(clip.get("status") or "") != "finished":
                return False, f"{name} has not finished generating."
            output = str(clip.get("output") or "")
            if not output or not Path(output).is_file():
                return False, f"{name} has no usable output file."
            if i > 0 and clip.get("generation_mode") == "continue" and bool((clip.get("settings") or {}).get("glue_results", False)):
                return False, (
                    f"{name} has 'Glue result to source' enabled. Timeline assembly expects each block "
                    "to contain only its own generated section; disable Glue and regenerate that continuation clip."
                )
        return True, ""

    def assemble_timeline(self):
        ready, reason = self._assembly_ready()
        if not ready:
            QMessageBox.information(self, "Timeline assembly", reason)
            return False
        if not callable(self.assemble_timeline_callback):
            QMessageBox.warning(self, "Timeline assembly", "Timeline assembly is not connected to the standalone GUI.")
            return False
        self.project["assembly_status"] = "Assembling…"
        self.project["assembled_output"] = ""
        self._refresh_all()
        result = self.assemble_timeline_callback(copy.deepcopy(self.project))
        if not result and self.project.get("assembly_status") == "Assembling…":
            self.project["assembly_status"] = "Assembly did not start."
            self._refresh_all()
        return bool(result)

    def mark_assembly_started(self, output):
        self.project["assembled_output"] = str(output or "")
        self.project["assembly_status"] = "Assembling…"
        self._refresh_all()

    def mark_assembly_finished(self, output):
        self.project["assembled_output"] = str(output or "")
        self.project["assembly_status"] = "Finished"
        self._refresh_all()

    def mark_assembly_failed(self, message):
        self.project["assembled_output"] = ""
        self.project["assembly_status"] = "Assembly failed: " + str(message or "Unknown error")
        self._refresh_all()

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
            if i == 0 and clip.get("generation_mode") == "continue":
                return False, "Clip 1 cannot continue a previous timeline clip."
            prompt = _compiled_prompt(clip)
            if not prompt:
                return False, f"{clip.get('name') or f'Clip {i + 1}'} has no prompt."
            if int(clip.get("frames") or 0) not in self.frame_values:
                return False, f"{clip.get('name') or f'Clip {i + 1}'} has an invalid H3 frame count."
            if bool(clip.get("match_next_first_frame", False)):
                return False, (
                    f"{clip.get('name') or f'Clip {i + 1}'} has 'Use next video first frame as last frame' enabled. "
                    "That mode is for replacing one middle clip while preserving the already-rendered next clip. Select that clip and use Generate Selected."
                )
        return True, ""

    def generation_specs(self):
        specs = []
        clips = self._clips()
        for i, clip in enumerate(clips):
            spec = copy.deepcopy(clip)
            spec["timeline_index"] = i
            spec["compiled_prompt"] = _compiled_prompt(clip)
            if i > 0:
                prev = clips[i - 1]
                spec["timeline_previous_output"] = str(prev.get("output") or "")
                spec["timeline_previous_job_id"] = str(prev.get("queue_job_id") or "")
                spec["timeline_previous_status"] = str(prev.get("status") or "")
                spec["timeline_previous_stale"] = bool(prev.get("stale"))
            if i + 1 < len(clips):
                nxt = clips[i + 1]
                spec["timeline_next_output"] = str(nxt.get("output") or "")
                spec["timeline_next_status"] = str(nxt.get("status") or "")
                spec["timeline_next_stale"] = bool(nxt.get("stale"))
            specs.append(spec)
        return specs

    def generate_selected(self):
        clip = self._selected_clip()
        idx = self._selected_index()
        if clip is None or idx < 0:
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
        if str(clip.get("generation_mode") or "new") == "continue":
            if idx == 0:
                QMessageBox.warning(self, "Timeline", "The first clip cannot continue a previous timeline clip.")
                return False
            prev = self._clips()[idx - 1]
            prev_output = Path(str(prev.get("output") or ""))
            if str(prev.get("status") or "") != "finished" or bool(prev.get("stale")) or not prev_output.is_file():
                QMessageBox.warning(self, "Timeline bridge", "Generate Selected needs a valid finished previous clip for Continue Previous Clip.")
                return False
        if bool(clip.get("match_next_first_frame", False)):
            if idx + 1 >= len(self._clips()):
                QMessageBox.warning(self, "Timeline bridge", "There is no next clip whose first frame can be used.")
                return False
            nxt = self._clips()[idx + 1]
            next_output = Path(str(nxt.get("output") or ""))
            if str(nxt.get("status") or "") != "finished" or bool(nxt.get("stale")) or not next_output.is_file():
                QMessageBox.warning(self, "Timeline bridge", "The next clip must already have a valid finished result before its first frame can anchor this replacement.")
                return False
        if not callable(self.queue_timeline_callback):
            QMessageBox.warning(self, "Timeline", "The timeline is not connected to the MiniMax queue.")
            return False
        result = self.queue_timeline_callback([spec])
        if result:
            self._invalidate_assembly("Selected clip regenerated — assemble again when ready.")
            self._refresh_all()
        return bool(result)

    def generate_timeline(self):
        ok, error = self.validate_timeline()
        if not ok:
            QMessageBox.warning(self, "Timeline not ready", error)
            return False
        if not callable(self.queue_timeline_callback):
            QMessageBox.warning(self, "Timeline", "The timeline is not connected to the MiniMax queue.")
            return False
        result = self.queue_timeline_callback(self.generation_specs())
        if result:
            self.project["assembled_output"] = ""
            self.project["assembly_status"] = "Waiting for timeline clips to finish…" if self.auto_assemble_check.isChecked() else ""
            self.project["auto_assemble"] = self.auto_assemble_check.isChecked()
            self.project["auto_assemble_pending"] = self.auto_assemble_check.isChecked()
            self._refresh_all()
        return bool(result)

    def mark_queued(self, clip_id, job_id, output=""):
        for clip in self._clips():
            if str(clip.get("id")) == str(clip_id):
                clip["queue_job_id"] = str(job_id)
                clip["status"] = "pending"
                clip["stale"] = False
                clip["output"] = str(output or "")
                break
        self._refresh_all()

    def sync_queue_jobs(self, jobs):
        by_id = {str(j.get("id")): j for j in (jobs or []) if j.get("id")}
        changed = False
        for clip in self._clips():
            job_id = clip.get("queue_job_id")
            if not job_id or str(job_id) not in by_id:
                continue
            job = by_id[str(job_id)]
            state = str(job.get("state") or "pending")
            if clip.get("status") != state:
                clip["status"] = state; changed = True
            output = str(job.get("output") or "")
            if output and clip.get("output") != output:
                clip["output"] = output; changed = True
        if changed:
            self._refresh_all()
        if self.project.get("auto_assemble_pending"):
            ready, _reason = self._assembly_ready()
            if ready:
                self.project["auto_assemble_pending"] = False
                self.assemble_timeline()
