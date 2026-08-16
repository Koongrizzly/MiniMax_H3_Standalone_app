from __future__ import annotations

import json
import os
import random
import re
import subprocess
import sys
import time
import traceback
import html
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QObject, QThread, QTimer, Qt, Signal
from PySide6.QtGui import QDesktopServices, QFont
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QFileDialog, QFormLayout, QGroupBox,
    QHBoxLayout, QLabel, QLineEdit, QMainWindow, QMessageBox, QPushButton,
    QScrollArea, QSpinBox, QDoubleSpinBox, QTabWidget, QTextEdit, QVBoxLayout,
    QWidget, QPlainTextEdit, QProgressBar, QSizePolicy
)
from PySide6.QtCore import QUrl

try:
    import psutil
except Exception:
    psutil = None

ROOT = Path(__file__).resolve().parents[1]
SETTINGS_FILE = ROOT / "presets" / "setsave" / "minimax_m3.json"
DEFAULT_MODEL = ROOT / "models" / "minimax_m3"
DEFAULT_OUTPUT = ROOT / "output" / "audio" / "minimax_M3"
LOG_DIR = ROOT / "logs"


def _hud_color(value, yellow_at=None, orange_at=None, red_at=None):
    try:
        v = float(value)
    except Exception:
        return None
    if red_at is not None and v >= red_at:
        return "#d70022"
    if orange_at is not None and v >= orange_at:
        return "#d98a00"
    if yellow_at is not None and v >= yellow_at:
        return "#e0c600"
    return None


def _hud_span(text, color=None):
    safe = html.escape(str(text))
    if color:
        return f"<span style='color:{color};font-weight:600'>{safe}</span>"
    return safe


def _hud_rate(bytes_per_sec):
    v = float(max(0.0, bytes_per_sec))
    if v >= 1024 ** 2:
        return f"{v/(1024**2):.1f} MB/s", v/(1024**2)
    return f"{v/1024:.0f} KB/s", v/(1024**2)


class SystemHud(QLabel):
    """Always-visible MiniMax Music 3 system/job monitor, based on the H3 GUI HUD."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("systemHud")
        self.setTextFormat(Qt.TextFormat.RichText)
        self.setAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignHCenter)
        self.setMinimumHeight(36)
        self.setMinimumWidth(0)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.setStyleSheet(
            "QLabel#systemHud { background:#0d1319; border:1px solid #263545; "
            "border-radius:7px; padding:6px 10px; font-size:12pt; }"
        )
        self.setToolTip(
            "Live Music 3 job phase/elapsed time, GPU VRAM/load/temperature, DDR RAM, CPU load, "
            "network traffic above 100 KB/s, and local date/time."
        )
        self._last_net = None
        self._last_net_t = time.monotonic()
        if psutil is not None:
            try:
                psutil.cpu_percent(interval=None)
                self._last_net = psutil.net_io_counters()
            except Exception:
                pass
        self.timer = QTimer(self)
        self.timer.setInterval(1500)
        self.timer.timeout.connect(self.refresh)
        self.timer.start()
        self.refresh()

    def _gpu(self):
        try:
            out = subprocess.check_output([
                "nvidia-smi",
                "--query-gpu=memory.used,memory.total,utilization.gpu,temperature.gpu",
                "--format=csv,noheader,nounits",
            ], stderr=subprocess.DEVNULL, text=True, timeout=0.8).strip().splitlines()[0]
            vals = [x.strip() for x in out.split(',')]
            return float(vals[0]) / 1024.0, float(vals[1]) / 1024.0, int(float(vals[2])), int(float(vals[3]))
        except Exception:
            return None

    def refresh(self):
        parts = []
        window = self.window()
        phase = str(getattr(window, "hud_phase", "Idle") or "Idle")
        started = getattr(window, "hud_started_at", None)
        if phase != "Idle" and started:
            elapsed = max(0, int(time.monotonic() - float(started)))
            elapsed_txt = f"{elapsed//60:02d}:{elapsed%60:02d}"
            parts.append("JOB " + _hud_span(f"{phase} · {elapsed_txt}", "#82e7ff"))
        else:
            parts.append("JOB " + _hud_span("Idle", "#82e7ff"))

        gpu = self._gpu()
        if gpu:
            used, total, util, temp = gpu
            fill = (used / total * 100.0) if total else 0.0
            parts.append("GPU : " + _hud_span(f"{used:.1f}/{total:.0f}", _hud_color(fill, 85, 90, 95)))
            parts.append(_hud_span(f"{util}%", _hud_color(util, None, 85, 95)))
            parts.append(_hud_span(f"{temp}°C", _hud_color(temp, 60, 65, 70)))
        else:
            parts.append("GPU : N/A")

        if psutil is not None:
            try:
                vm = psutil.virtual_memory()
                used = (vm.total - vm.available) / (1024 ** 3)
                total = vm.total / (1024 ** 3)
                parts.append(f"DDR {used:.1f}/{total:.0f} {vm.percent:.0f}%")
            except Exception:
                parts.append("DDR N/A")
            try:
                cpu = float(psutil.cpu_percent(interval=None))
                parts.append("CPU " + _hud_span(f"{cpu:.0f}%", _hud_color(cpu, None, 85, 95)))
            except Exception:
                parts.append("CPU N/A")
            try:
                now_t = time.monotonic()
                cur = psutil.net_io_counters()
                if self._last_net is not None:
                    dt = max(0.1, now_t - self._last_net_t)
                    dl = max(0, cur.bytes_recv - self._last_net.bytes_recv) / dt
                    ul = max(0, cur.bytes_sent - self._last_net.bytes_sent) / dt
                    if dl > 100 * 1024:
                        txt, mbs = _hud_rate(dl)
                        parts.append("DL " + _hud_span(txt, _hud_color(mbs, 10, 50, 95)))
                    if ul > 100 * 1024:
                        txt, mbs = _hud_rate(ul)
                        parts.append("UL " + _hud_span(txt, _hud_color(mbs, 10, 50, 95)))
                self._last_net = cur
                self._last_net_t = now_t
            except Exception:
                pass
        else:
            parts.extend(["DDR N/A", "CPU N/A"])

        parts.append(datetime.now().strftime("%a. %d %b %H:%M"))
        self.setText("&nbsp;&nbsp;".join(parts))


class NoWheelSpinBox(QSpinBox):
    def wheelEvent(self, event):
        event.ignore()


class NoWheelDoubleSpinBox(QDoubleSpinBox):
    def wheelEvent(self, event):
        event.ignore()


class NoWheelComboBox(QComboBox):
    def wheelEvent(self, event):
        event.ignore()


class GenerateWorker(QObject):
    log = Signal(str)
    finished = Signal(str)
    failed = Signal(str)

    def __init__(self, model_dir: Path, prompt: str, lyrics: str, duration: float,
                 seed: int, output: Path, vram_mode: str, ar_top_k: int = 0, flow_steps: int = 0,
                 flow_guidance: float = 0.0, ar_weight_mode: str = "bf16"):
        super().__init__()
        self.model_dir = model_dir
        self.prompt = prompt
        self.lyrics = lyrics
        self.duration = duration
        self.seed = seed
        self.output = output
        self.vram_mode = vram_mode
        self.ar_top_k = int(ar_top_k or 0)
        self.flow_steps = int(flow_steps or 0)
        self.flow_guidance = float(flow_guidance or 0.0)
        self.ar_weight_mode = str(ar_weight_mode or "bf16")

    def run(self):
        try:
            from minimax_m3_backend import MiniMaxM3Engine, GenerationRequest
            engine = MiniMaxM3Engine(self.model_dir, self.log.emit)
            req = GenerationRequest(
                prompt=self.prompt,
                lyrics=self.lyrics,
                duration=self.duration,
                seed=self.seed,
                output_path=self.output,
                vram_mode=self.vram_mode,
                ar_top_k=self.ar_top_k,
                flow_steps=self.flow_steps,
                flow_guidance=self.flow_guidance,
                ar_weight_mode=self.ar_weight_mode,
            )
            path = engine.generate(req)
            self.finished.emit(str(path))
        except Exception:
            self.failed.emit(traceback.format_exc())


class DownloadWorker(QObject):
    log = Signal(str)
    finished = Signal(str)
    failed = Signal(str)

    def __init__(self, destination: Path):
        super().__init__()
        self.destination = destination

    def run(self):
        try:
            from model_downloader import download_model, validate_model
            path = download_model(self.destination, self.log.emit)
            ok, missing = validate_model(path)
            if not ok:
                raise RuntimeError("Download finished but validation failed:\n" + "\n".join(missing))
            self.finished.emit(str(path))
        except Exception:
            self.failed.emit(traceback.format_exc())


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("MiniMax Music 3 Standalone")
        self.resize(1100, 820)
        self._thread = None
        self._worker = None
        self.hud_phase = "Idle"
        self.hud_started_at = None
        self.settings = self.load_settings()
        self.log_file = None
        self.download_anim_timer = QTimer(self)
        self.download_anim_timer.setInterval(350)
        self.download_anim_timer.timeout.connect(self._animate_download_status)
        self._download_anim_step = 0

        self.save_timer = QTimer(self)
        self.save_timer.setSingleShot(True)
        self.save_timer.setInterval(1200)
        self.save_timer.timeout.connect(self.save_settings)

        root = QWidget()
        outer = QVBoxLayout(root)
        outer.setContentsMargins(10, 10, 10, 10)
        outer.setSpacing(8)

        self.system_hud = SystemHud(self)
        outer.addWidget(self.system_hud, 0)

        self.tabs = QTabWidget()
        self.tabs.setUsesScrollButtons(False)
        self.tabs.setDocumentMode(True)
        outer.addWidget(self.tabs, 1)
        self.tabs.addTab(self.build_generation_tab(), "Generation")
        self.tabs.addTab(self.build_settings_tab(), "Settings")
        self.setCentralWidget(root)
        self._apply_style()
        self.statusBar().showMessage("Ready")
        self.apply_settings_to_ui()

    def _apply_style(self):
        # Keep the Music 3 app visually consistent with the MiniMax H3 standalone GUI.
        # The HUD is designed for a dark application surface; without the global
        # palette its normal text inherits Windows' black foreground and becomes
        # almost invisible on the dark HUD background.
        self.setStyleSheet("""
        QMainWindow, QWidget { background:#11151b; color:#e8eef6; font-size:10pt; }
        QTabWidget::pane { border:1px solid #263545; border-radius:7px; }
        QTabBar::tab { background:#171f28; color:#dce8f3; border:1px solid #2e4051; padding:8px 18px; min-width:110px; }
        QTabBar::tab:selected { background:#1d3040; color:#82e7ff; }
        QGroupBox { border:1px solid #2a3948; border-radius:8px; margin-top:10px; padding:10px; font-weight:600; }
        QGroupBox::title { subcontrol-origin:margin; left:10px; padding:0 5px; color:#75d8ff; }
        QLineEdit, QPlainTextEdit, QTextEdit, QComboBox, QSpinBox, QDoubleSpinBox {
            background:#0b0f14; color:#e8eef6; border:1px solid #334556; border-radius:5px; padding:5px;
            selection-background-color:#176c89; selection-color:#ffffff;
        }
        QComboBox QAbstractItemView { background:#0b0f14; color:#e8eef6; selection-background-color:#176c89; }
        QPushButton { background:#1b2732; color:#e8eef6; border:1px solid #3c5267; border-radius:5px; padding:6px 11px; }
        QPushButton:hover { background:#243544; }
        QPushButton:pressed { background:#15222c; }
        QPushButton:disabled { color:#66717a; background:#15191e; }
        QCheckBox { color:#e8eef6; spacing:7px; }
        QLabel#systemHud { color:#e8eef6; background:#0d1319; border:1px solid #263545; border-radius:7px; padding:6px 10px; font-size:15pt; }
        QProgressBar { background:#0b0f14; color:#e8eef6; border:1px solid #334556; border-radius:5px; text-align:center; min-height:18px; }
        QProgressBar::chunk { background:#176c89; border-radius:4px; }
        QScrollArea { border:0; background:#11151b; }
        QScrollBar:vertical { background:#0c1116; width:14px; margin:0; }
        QScrollBar::handle:vertical { background:#395166; min-height:30px; border-radius:6px; }
        QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height:0px; }
        QStatusBar { background:#0d1319; color:#a8bac9; border-top:1px solid #263545; }
        QToolTip { background:#17212b; color:#e8eef6; border:1px solid #3c5267; padding:4px; }
        """)

    def build_generation_tab(self):
        # Generation has enough controls to require its own vertical scroll area.
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn)
        page = QWidget()
        scroll.setWidget(page)
        layout = QVBoxLayout(page)

        title = QLabel("MiniMax Music 3")
        f = QFont()
        f.setPointSize(18)
        f.setBold(True)
        title.setFont(f)
        layout.addWidget(title)

        style_box = QGroupBox("Music style / MiniMax caption builder")
        style_form = QFormLayout(style_box)

        self.prompt_mode = NoWheelComboBox()
        self.prompt_mode.addItem("Structured controls + additional description", "structured")
        self.prompt_mode.addItem("Raw description only", "raw")
        self.prompt_mode.currentIndexChanged.connect(self._sync_prompt_mode)
        self.prompt_mode.currentIndexChanged.connect(self.schedule_save)
        style_form.addRow("Prompt mode", self.prompt_mode)

        self.genre_combo = NoWheelComboBox()
        self.genre_combo.setEditable(True)
        self.genre_combo.addItems([
            "Deep House", "House", "Tech House", "Progressive House", "Reggae", "Dub", "Techno",
            "Trance", "Drum & Bass", "Disco", "Funk", "Rock", "Pop", "Hip-Hop", "Ambient",
            "Cinematic", "Classical", "Jazz", "Blues", "Country"
        ])
        self.genre_combo.currentTextChanged.connect(self.schedule_save)
        style_form.addRow("Genre", self.genre_combo)

        self.subgenre_edit = QLineEdit()
        self.subgenre_edit.setPlaceholderText("optional subgenre / scene, e.g. beach club deep house")
        self.subgenre_edit.editingFinished.connect(self.schedule_save)
        style_form.addRow("Subgenre / scene", self.subgenre_edit)

        bpm_row = QWidget(); bpm_l = QHBoxLayout(bpm_row); bpm_l.setContentsMargins(0, 0, 0, 0)
        self.bpm_spin = NoWheelSpinBox()
        self.bpm_spin.setRange(0, 260)
        self.bpm_spin.setSpecialValueText("Auto")
        self.bpm_spin.setValue(0)
        self.bpm_spin.valueChanged.connect(self.schedule_save)
        bpm_l.addWidget(self.bpm_spin)
        bpm_l.addWidget(QLabel("BPM"))
        bpm_l.addStretch(1)
        style_form.addRow("Tempo", bpm_row)

        key_row = QWidget(); key_l = QHBoxLayout(key_row); key_l.setContentsMargins(0, 0, 0, 0)
        self.key_combo = NoWheelComboBox()
        self.key_combo.addItems(["Auto", "C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"])
        self.key_combo.currentIndexChanged.connect(self.schedule_save)
        self.scale_combo = NoWheelComboBox()
        self.scale_combo.addItems(["Auto", "Major", "Minor", "Dorian", "Mixolydian", "Phrygian", "Lydian"])
        self.scale_combo.currentIndexChanged.connect(self.schedule_save)
        key_l.addWidget(self.key_combo)
        key_l.addWidget(self.scale_combo)
        key_l.addStretch(1)
        style_form.addRow("Key / scale", key_row)

        self.mood_edit = QLineEdit()
        self.mood_edit.setPlaceholderText("energy and emotional direction, e.g. warm, driving, sunny, confident")
        self.mood_edit.editingFinished.connect(self.schedule_save)
        style_form.addRow("Mood / energy", self.mood_edit)

        self.vocal_mode = NoWheelComboBox()
        self.vocal_mode.addItem("Vocal track", "vocal")
        self.vocal_mode.addItem("Instrumental", "instrumental")
        self.vocal_mode.currentIndexChanged.connect(self._sync_vocal_mode)
        self.vocal_mode.currentIndexChanged.connect(self.schedule_save)
        style_form.addRow("Track type", self.vocal_mode)

        self.vocal_style_edit = QLineEdit()
        self.vocal_style_edit.setPlaceholderText("voice/timbre/delivery, e.g. soulful female vocal, restrained and intimate")
        self.vocal_style_edit.editingFinished.connect(self.schedule_save)
        style_form.addRow("Vocal style", self.vocal_style_edit)

        self.instruments_edit = QLineEdit()
        self.instruments_edit.setPlaceholderText("main instruments, drums, bass, synths, guitars, percussion...")
        self.instruments_edit.editingFinished.connect(self.schedule_save)
        style_form.addRow("Instruments", self.instruments_edit)

        self.arrangement_edit = QLineEdit()
        self.arrangement_edit.setPlaceholderText("song structure / progression, e.g. DJ-friendly intro, build, hook, breakdown, return")
        self.arrangement_edit.editingFinished.connect(self.schedule_save)
        style_form.addRow("Arrangement", self.arrangement_edit)

        self.production_edit = QLineEdit()
        self.production_edit.setPlaceholderText("mix/production character, stereo space, reverb, punch, analog/digital character...")
        self.production_edit.editingFinished.connect(self.schedule_save)
        style_form.addRow("Production", self.production_edit)

        self.strict_style = QCheckBox("Strict style emphasis")
        self.strict_style.setToolTip(
            "Reinforces the selected genre and BPM inside the MiniMax structured caption. This is text conditioning only; "
            "it does not invent a CFG parameter or change the model pipeline."
        )
        self.strict_style.toggled.connect(self.schedule_save)
        style_form.addRow("Adherence", self.strict_style)
        layout.addWidget(style_box)
        self.structured_style_box = style_box

        desc_box = QGroupBox("Additional music description / raw prompt")
        desc_l = QVBoxLayout(desc_box)
        self.prompt_edit = QPlainTextEdit()
        self.prompt_edit.setPlaceholderText(
            "Additional creative direction folded into the official MiniMax structured caption, or the complete prompt when Raw description only is selected."
        )
        self.prompt_edit.setMinimumHeight(130)
        self.prompt_edit.textChanged.connect(self.schedule_save)
        desc_l.addWidget(self.prompt_edit)
        self.effective_prompt_preview = QLabel("")
        self.effective_prompt_preview.setWordWrap(True)
        self.effective_prompt_preview.setStyleSheet("color:#93a8ba;")
        self.effective_prompt_preview.setToolTip("The complete text that will be sent to MiniMax is also written to the run log.")
        desc_l.addWidget(self.effective_prompt_preview)
        layout.addWidget(desc_box)

        lyrics_box = QGroupBox("Lyrics")
        lyrics_l = QVBoxLayout(lyrics_box)
        lyric_hint = QLabel(
            "For vocal tracks, put section tags on their own line. Example: [Verse] on one line, then the lyric text below it. "
            "Instrumental mode uses MiniMax's no-lyrics marker internally; no vocals are requested."
        )
        lyric_hint.setWordWrap(True)
        lyrics_l.addWidget(lyric_hint)
        tag_row = QHBoxLayout()
        for tag in ("[Intro]", "[Verse]", "[Pre-Chorus]", "[Chorus]", "[Bridge]", "[Instrumental]", "[Solo]", "[Outro]"):
            b = QPushButton(tag)
            b.clicked.connect(lambda _checked=False, t=tag: self._insert_lyric_tag(t))
            tag_row.addWidget(b)
        tag_row.addStretch(1)
        lyrics_l.addLayout(tag_row)
        self.lyrics_edit = QPlainTextEdit()
        self.lyrics_edit.setPlaceholderText("[Verse]\nYour lyric line here\n[Chorus]\nYour chorus here")
        self.lyrics_edit.setMinimumHeight(220)
        self.lyrics_edit.textChanged.connect(self.schedule_save)
        lyrics_l.addWidget(self.lyrics_edit)
        layout.addWidget(lyrics_box)
        self.lyrics_box = lyrics_box

        generation_box = QGroupBox("Generation")
        gen_form = QFormLayout(generation_box)
        dur_row = QWidget(); row = QHBoxLayout(dur_row); row.setContentsMargins(0, 0, 0, 0)
        self.duration_spin = NoWheelDoubleSpinBox()
        self.duration_spin.setRange(1.0, 360.0)
        self.duration_spin.setDecimals(1)
        self.duration_spin.setSingleStep(5.0)
        self.duration_spin.setToolTip(
            "Duration is passed to MiniMax Music 3 as its audio-duration request. Actual track length can vary: "
            "it also depends on the amount of lyrics/words and the model can stop or adjust duration internally."
        )
        self.duration_spin.valueChanged.connect(self.schedule_save)
        row.addWidget(self.duration_spin)
        row.addWidget(QLabel("seconds"))
        row.addSpacing(20)
        row.addWidget(QLabel("Seed (-1 = random)"))
        self.seed_spin = NoWheelSpinBox()
        self.seed_spin.setRange(-1, 2147483647)
        self.seed_spin.valueChanged.connect(self.schedule_save)
        row.addWidget(self.seed_spin)
        row.addStretch(1)
        gen_form.addRow("Duration / seed", dur_row)
        self.duration_behavior_note = QLabel(
            "Actual duration also depends on the amount of words in the lyrics and can be changed internally by the model."
        )
        self.duration_behavior_note.setWordWrap(True)
        self.duration_behavior_note.setStyleSheet("color: #9aa5b1;")
        gen_form.addRow("", self.duration_behavior_note)

        native_row_w = QWidget(); native_row = QHBoxLayout(native_row_w); native_row.setContentsMargins(0, 0, 0, 0)
        self.ar_top_k_spin = NoWheelSpinBox()
        self.ar_top_k_spin.setRange(0, 512)
        self.ar_top_k_spin.setSpecialValueText("Auto (native)")
        self.ar_top_k_spin.setValue(0)
        self.ar_top_k_spin.setToolTip(
            "Native MiniMax Music 3 autoregressive Top-K. Auto preserves the pinned Diffusers value. "
            "Lower values restrict token choices and can make sampling more predictable; higher values allow more variation. "
            "This directly wraps MiniMax's existing _sample_top_k helper; it does not add a ComfyUI sampler."
        )
        self.ar_top_k_spin.valueChanged.connect(self.schedule_save)
        native_row.addWidget(self.ar_top_k_spin)
        native_row.addWidget(QLabel("0 = use native value"))
        native_row.addStretch(1)
        gen_form.addRow("AR Top-K", native_row_w)

        flow_row_w = QWidget(); flow_row = QHBoxLayout(flow_row_w); flow_row.setContentsMargins(0, 0, 0, 0)
        self.flow_steps_spin = NoWheelSpinBox()
        self.flow_steps_spin.setRange(0, 100)
        self.flow_steps_spin.setSpecialValueText("Auto (native)")
        self.flow_steps_spin.setValue(0)
        self.flow_steps_spin.setToolTip(
            "Native MiniMax Music 3 flow-matching denoising steps. Auto preserves the exact step count requested by the pinned Diffusers block. "
            "A manual value only changes num_inference_steps passed to the existing FlowMatchEulerDiscreteScheduler."
        )
        self.flow_steps_spin.valueChanged.connect(self.schedule_save)
        flow_row.addWidget(QLabel("Steps")); flow_row.addWidget(self.flow_steps_spin)
        flow_row.addSpacing(18)
        self.flow_guidance_spin = NoWheelDoubleSpinBox()
        self.flow_guidance_spin.setRange(0.0, 20.0)
        self.flow_guidance_spin.setDecimals(2)
        self.flow_guidance_spin.setSingleStep(0.1)
        self.flow_guidance_spin.setSpecialValueText("Auto (native)")
        self.flow_guidance_spin.setValue(0.0)
        self.flow_guidance_spin.setToolTip(
            "Native classifier-free guidance scale used by MiniMax Music 3's flow-matching stage. Auto preserves the pinned pipeline guider. "
            "Manual values recreate the same Diffusers ClassifierFreeGuidance component with only guidance_scale changed."
        )
        self.flow_guidance_spin.valueChanged.connect(self.schedule_save)
        flow_row.addWidget(QLabel("CFG")); flow_row.addWidget(self.flow_guidance_spin)
        flow_row.addWidget(QLabel("0 = native"))
        flow_row.addStretch(1)
        gen_form.addRow("Flow matching", flow_row_w)

        long_row_w = QWidget(); long_row = QHBoxLayout(long_row_w); long_row.setContentsMargins(0, 0, 0, 0)
        self.experimental_long_duration = QCheckBox("Experimental long duration")
        self.experimental_long_duration.setToolTip(
            "Unlock duration requests beyond the documented 9,000 acoustic-frame / 6-minute limit. "
            "This is experimental: MiniMax may stop early, reject the request, run out of memory, or behave unpredictably."
        )
        self.experimental_long_duration.toggled.connect(self._sync_long_duration_mode)
        self.experimental_long_duration.toggled.connect(self.schedule_save)
        long_row.addWidget(self.experimental_long_duration)
        self.duration_limit_note = QLabel("Normal limit: 6:00 = 9,000 acoustic frames at 25 fps")
        self.duration_limit_note.setStyleSheet("color: #9aa5b1;")
        long_row.addWidget(self.duration_limit_note)
        long_row.addStretch(1)
        gen_form.addRow("Long duration", long_row_w)
        layout.addWidget(generation_box)

        btns = QHBoxLayout()
        self.generate_btn = QPushButton("Generate Music")
        self.generate_btn.clicked.connect(self.start_generation)
        btns.addWidget(self.generate_btn)
        self.open_output_btn = QPushButton("Open Output Folder")
        self.open_output_btn.clicked.connect(self.open_output_folder)
        btns.addWidget(self.open_output_btn)
        btns.addStretch(1)
        layout.addLayout(btns)
        layout.addStretch(1)
        return scroll

    def _insert_lyric_tag(self, tag):
        text = self.lyrics_edit.toPlainText()
        prefix = "" if not text or text.endswith("\n") else "\n"
        self.lyrics_edit.insertPlainText(prefix + str(tag) + "\n")
        self.lyrics_edit.setFocus()

    def _sync_vocal_mode(self, *args):
        instrumental = self.vocal_mode.currentData() == "instrumental"
        self.vocal_style_edit.setEnabled(not instrumental)
        self.lyrics_box.setEnabled(not instrumental)
        if instrumental:
            self.lyrics_box.setToolTip("Instrumental mode uses MiniMax's no-lyrics marker internally; the Lyrics box is ignored.")
        else:
            self.lyrics_box.setToolTip("")
        self._refresh_effective_prompt_preview()

    def _sync_prompt_mode(self, *args):
        structured = self.prompt_mode.currentData() == "structured"
        # Keep the group visible so users can see their saved values when switching modes,
        # but clearly disable them when raw mode is selected.
        for w in (self.genre_combo, self.subgenre_edit, self.bpm_spin, self.key_combo, self.scale_combo,
                  self.mood_edit, self.vocal_mode, self.vocal_style_edit, self.instruments_edit,
                  self.arrangement_edit, self.production_edit, self.strict_style):
            w.setEnabled(structured)
        self._refresh_effective_prompt_preview()

    def _build_effective_prompt(self):
        raw = self.prompt_edit.toPlainText().strip()
        if self.prompt_mode.currentData() == "raw":
            return raw

        genre = self.genre_combo.currentText().strip()
        subgenre = self.subgenre_edit.text().strip()
        bpm = int(self.bpm_spin.value())
        key = self.key_combo.currentText().strip()
        scale = self.scale_combo.currentText().strip()
        mood = self.mood_edit.text().strip()
        instrumental = self.vocal_mode.currentData() == "instrumental"
        vocal_style = self.vocal_style_edit.text().strip()
        instruments = self.instruments_edit.text().strip()
        arrangement = self.arrangement_edit.text().strip()
        production = self.production_edit.text().strip()

        # MiniMax's own music-caption-rewriter skill asks for exactly three
        # top-level sections: Global Metadata, Vocal Details and Arrangement.
        # Keep all values grounded in the user's controls; do not invent BPM,
        # key, vocal gender, instruments or production techniques.
        global_bits = []
        if genre:
            identity = genre
            if subgenre:
                identity += f" with {subgenre} influences"
            global_bits.append(f"The track is {identity}.")
        elif subgenre:
            global_bits.append(f"The musical direction is {subgenre}.")
        if bpm > 0:
            global_bits.append(f"Use an explicit tempo of {bpm} BPM.")
        if key != "Auto":
            key_text = key + (f" {scale}" if scale != "Auto" else "")
            global_bits.append(f"The requested tonal center is {key_text}.")
        elif scale != "Auto":
            global_bits.append(f"Use a {scale} modal or scale character without inventing a specific key.")
        if mood:
            global_bits.append(f"The mood and energy should be {mood}.")
        if production:
            global_bits.append(f"Overall sonic and production profile: {production}.")
        if raw:
            global_bits.append(f"Additional creative direction: {raw}")
        if self.strict_style.isChecked() and genre:
            strict = f"Keep the musical identity clearly rooted in {genre} throughout"
            if bpm > 0:
                strict += f" and preserve the {bpm} BPM pulse"
            strict += ", avoiding drift into unrelated genres."
            global_bits.append(strict)
        if not global_bits:
            global_bits.append("Preserve the musical direction specified by the user without adding unsupported stylistic details.")

        if instrumental:
            vocal_bits = ["The piece is instrumental and should contain no sung vocals."]
            if instruments:
                vocal_bits.append("The lead melodic role should emerge from the specified instrumental palette rather than a voice.")
            else:
                vocal_bits.append("Keep the lead role instrumental without inventing a vocalist or vocal timbre.")
        else:
            vocal_bits = ["This is a vocal track."]
            if vocal_style:
                vocal_bits.append(f"Lead vocal character and delivery: {vocal_style}.")
            else:
                vocal_bits.append("The exact vocal timbre, register and delivery are unspecified; do not invent a fixed gender or register from the controls alone.")

        arrangement_bits = []
        if instruments:
            arrangement_bits.append(f"Core instrumentation: {instruments}.")
        if arrangement:
            arrangement_bits.append(f"Requested structural and arrangement direction: {arrangement}.")

        # Section tags are structural directives in MiniMax's official guide.
        # Preserve their order, but never copy or summarize lyric text.
        lyric_text = self.lyrics_edit.toPlainText() if not instrumental else ""
        tags = []
        for match in re.finditer(r"^\s*\[([^\]\r\n]+)\]\s*$", lyric_text, flags=re.MULTILINE):
            tag = match.group(1).strip()
            if tag and tag.lower() not in [t.lower() for t in tags]:
                tags.append(tag)

        if tags:
            arrangement_bits.append("Follow the lyric-section timeline in this order: " + " → ".join(tags) + ".")
            for i, tag in enumerate(tags):
                low = tag.lower()
                if "intro" in low:
                    detail = "establish the core palette and groove with controlled density, leaving room for the main body to enter"
                elif "pre" in low and "chorus" in low:
                    detail = "increase tension and forward motion while preparing a clear transition into the chorus"
                elif "chorus" in low:
                    detail = "deliver the strongest recurring hook and fullest appropriate energy while preserving the established genre"
                elif "verse" in low:
                    detail = "support the lead with a stable groove and enough space for phrasing, changing texture rather than abandoning the core identity"
                elif "bridge" in low:
                    detail = "create a coherent contrast in texture or intensity before reconnecting with the main material"
                elif "solo" in low:
                    detail = "feature an instrumental lead while the rhythm section maintains continuity"
                elif "instrumental" in low:
                    detail = "shift focus to the instrumental palette and let the arrangement develop without adding vocal content"
                elif "outro" in low:
                    detail = "resolve the energy and remove or simplify elements toward a musically deliberate ending"
                else:
                    detail = "make a clear, musically coherent local change in density, instrumentation or energy while preserving continuity"
                arrangement_bits.append(f"{tag}: {detail}.")
        elif not arrangement:
            if instrumental:
                arrangement_bits.append(
                    "Use a coherent instrumental timeline: establish the main palette, develop the groove and melodic material, "
                    "introduce a contrasting or lower-density passage, return to the strongest material, then resolve with a deliberate ending."
                )
            else:
                arrangement_bits.append(
                    "Use a coherent song timeline with an opening, developing body sections, a clearly stronger recurring hook section, "
                    "a contrasting passage where appropriate, and a deliberate ending."
                )

        if production:
            arrangement_bits.append("Apply the requested production character consistently as sections enter, intensify, thin out and resolve.")
        arrangement_bits.append("Keep instrument entrances, exits and transitions continuous and musically plausible rather than presenting a static equipment list.")

        return (
            "### Global Metadata\n"
            + " ".join(global_bits).strip()
            + "\n### Vocal Details\n"
            + " ".join(vocal_bits).strip()
            + "\n### Arrangement\n"
            + " ".join(arrangement_bits).strip()
        )

    def _structured_prompt_conflicts(self):
        """Return clear contradictions between structured controls and free text.

        This intentionally checks only explicit, high-confidence conflicts. It does
        not try to interpret the whole prompt or reject negative phrases such as
        "no techno" later in a description.
        """
        if self.prompt_mode.currentData() != "structured":
            return []
        raw = self.prompt_edit.toPlainText().strip()
        if not raw:
            return []
        problems = []
        selected_genre = self.genre_combo.currentText().strip()
        first_sentence = re.split(r"[.\n]", raw, maxsplit=1)[0].lower()
        known = [
            "deep house", "tech house", "progressive house", "heavy metal", "metal",
            "house", "reggae", "dub", "techno", "trance", "drum & bass", "drum and bass",
            "disco", "funk", "rock", "pop", "hip-hop", "hip hop", "ambient", "cinematic",
            "classical", "jazz", "blues", "country",
        ]
        asserted = None
        # Prefer longer genre names before their shorter parents (e.g. Deep House before House).
        for genre in sorted(known, key=len, reverse=True):
            if re.search(r"\b" + re.escape(genre) + r"\b", first_sentence):
                asserted = genre
                break
        if asserted and selected_genre:
            norm_selected = selected_genre.lower().replace("drum & bass", "drum and bass")
            norm_asserted = asserted.replace("drum & bass", "drum and bass")
            # Treat generic Metal as compatible with Heavy Metal, otherwise require the same genre phrase.
            compatible = (norm_asserted == norm_selected) or (
                {norm_asserted, norm_selected} <= {"metal", "heavy metal"}
            )
            if not compatible:
                problems.append(
                    f"Genre control is '{selected_genre}', but the description starts by asking for '{asserted}'."
                )

        selected_bpm = int(self.bpm_spin.value())
        bpm_values = [int(x) for x in re.findall(r"(?<!\d)(\d{2,3})\s*BPM\b", raw, flags=re.IGNORECASE)]
        if selected_bpm > 0 and bpm_values and any(v != selected_bpm for v in bpm_values):
            values = ", ".join(str(v) for v in sorted(set(bpm_values)))
            problems.append(f"Tempo control is {selected_bpm} BPM, but the description explicitly mentions {values} BPM.")

        instrumental_words = bool(re.search(r"\binstrumental(?:\s+track|\s+heavy metal|\s+music)?\b", raw, flags=re.IGNORECASE))
        vocal_mode = self.vocal_mode.currentData()
        if instrumental_words and vocal_mode == "vocal":
            problems.append("Track type is Vocal, but the description explicitly asks for instrumental music.")
        return problems

    def _refresh_effective_prompt_preview(self, *args):
        if not hasattr(self, "effective_prompt_preview"):
            return
        effective = self._build_effective_prompt()
        if not effective:
            self.effective_prompt_preview.setText("Effective prompt: (empty)")
            return
        one_line = " ".join(effective.split())
        if len(one_line) > 260:
            one_line = one_line[:257] + "..."
        self.effective_prompt_preview.setText("Effective prompt: " + one_line)

    def build_settings_tab(self):
        outer = QWidget()
        outer_layout = QVBoxLayout(outer)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        outer_layout.addWidget(scroll)
        content = QWidget()
        scroll.setWidget(content)
        layout = QVBoxLayout(content)

        folders = QGroupBox("Folders")
        form = QFormLayout(folders)
        self.model_path_edit = QLineEdit()
        self.model_path_edit.editingFinished.connect(self.schedule_save)
        model_row = QWidget(); model_l = QHBoxLayout(model_row); model_l.setContentsMargins(0,0,0,0)
        model_l.addWidget(self.model_path_edit)
        b = QPushButton("Browse"); b.clicked.connect(self.browse_model); model_l.addWidget(b)
        form.addRow("Model folder", model_row)

        self.output_path_edit = QLineEdit()
        self.output_path_edit.editingFinished.connect(self.schedule_save)
        out_row = QWidget(); out_l = QHBoxLayout(out_row); out_l.setContentsMargins(0,0,0,0)
        out_l.addWidget(self.output_path_edit)
        b2 = QPushButton("Browse"); b2.clicked.connect(self.browse_output); out_l.addWidget(b2)
        form.addRow("Output folder", out_row)
        layout.addWidget(folders)

        perf = QGroupBox("VRAM / Performance")
        pform = QFormLayout(perf)
        self.vram_mode = NoWheelComboBox()
        self.vram_mode.addItem("Full BF16 GPU", "full")
        self.vram_mode.addItem("Automatic CPU offload", "offload")
        self.vram_mode.addItem("Low VRAM", "streaming")
        self.vram_mode.setToolTip(
            "Full keeps the model on GPU. Automatic uses balanced internal group offloading to leave more VRAM "
            "headroom for long generations. Low VRAM uses smaller groups for a lower memory footprint."
        )
        self.vram_mode.currentIndexChanged.connect(self.schedule_save)
        pform.addRow("VRAM mode", self.vram_mode)

        self.ar_weight_mode = NoWheelComboBox()
        self.ar_weight_mode.addItem("BF16", "bf16")
        self.ar_weight_mode.addItem("INT8 weight-only (experimental)", "int8")
        self.ar_weight_mode.setToolTip(
            "Controls only the autoregressive Music 3 language/depth model Linear weights. "
            "INT8 uses PyTorch torchao weight-only quantization to reduce static AR VRAM. "
            "The flow-matching transformer, audio decoder, embeddings and KV cache stay in their native precision."
        )
        self.ar_weight_mode.currentIndexChanged.connect(self.schedule_save)
        pform.addRow("AR weights", self.ar_weight_mode)
        layout.addWidget(perf)

        model_tools = QGroupBox("Model / Tests")
        mlay = QVBoxLayout(model_tools)
        self.download_btn = QPushButton("Download needed model files")
        self.download_btn.clicked.connect(self.start_download)
        mlay.addWidget(self.download_btn)
        self.download_status = QLabel("Model download idle")
        mlay.addWidget(self.download_status)
        self.download_progress = QProgressBar()
        self.download_progress.setRange(0, 100)
        self.download_progress.setValue(0)
        self.download_progress.setTextVisible(True)
        self.download_progress.setFormat("Idle")
        mlay.addWidget(self.download_progress)
        self.validate_btn = QPushButton("Validate Local Model")
        self.validate_btn.clicked.connect(self.validate_local_model)
        mlay.addWidget(self.validate_btn)
        self.cuda_btn = QPushButton("Run CUDA / Runtime Test")
        self.cuda_btn.clicked.connect(self.cuda_test)
        mlay.addWidget(self.cuda_btn)
        hint = QLabel(
            "Uses accelerated Hugging Face downloads and downloads only the model files this app needs. "
            "Existing files are kept and missing or incomplete files are repaired."
        )
        hint.setWordWrap(True)
        mlay.addWidget(hint)
        layout.addWidget(model_tools)

        advanced = QGroupBox("Advanced")
        alay = QFormLayout(advanced)
        self.system_hud_toggle = QCheckBox("System HUD")
        self.system_hud_toggle.setChecked(True)
        self.system_hud_toggle.setToolTip(
            "Show the compact live HUD above the tabs. It displays the current Music 3 operation and elapsed time, "
            "GPU VRAM/load/temperature, DDR RAM, CPU load, network DL/UL above 100 KB/s, and local date/time. Default: On."
        )
        self.system_hud_toggle.toggled.connect(self._set_system_hud_visible)
        alay.addRow("Monitor", self.system_hud_toggle)
        self.extended_logs = QCheckBox("Extended console/file logging")
        self.extended_logs.stateChanged.connect(self.schedule_save)
        alay.addRow("Logging", self.extended_logs)
        layout.addWidget(advanced)

        logs = QGroupBox("Session Log")
        l = QVBoxLayout(logs)
        self.log_view = QTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMinimumHeight(220)
        l.addWidget(self.log_view)
        layout.addWidget(logs)
        layout.addStretch(1)
        return outer

    def defaults(self):
        return {
            "prompt": "",
            "lyrics": "",
            "prompt_mode": "structured",
            "genre": "Deep House",
            "subgenre": "",
            "bpm": 0,
            "key": "Auto",
            "scale": "Auto",
            "mood": "",
            "vocal_mode": "vocal",
            "vocal_style": "",
            "instruments": "",
            "arrangement": "",
            "production": "",
            "strict_style": False,
            "duration": 60.0,
            "ar_top_k": 0,
            "flow_steps": 0,
            "flow_guidance": 0.0,
            "experimental_long_duration": False,
            "seed": -1,
            "model_path": str(DEFAULT_MODEL),
            "output_path": str(DEFAULT_OUTPUT),
            "vram_mode": "offload",
            "ar_weight_mode": "bf16",
            "extended_logs": False,
            "system_hud": True,
        }

    def load_settings(self):
        data = self.defaults()
        try:
            if SETTINGS_FILE.exists():
                loaded = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    # Preserve pre-v1.13 behavior for existing users: an old saved free-form
                    # description must not suddenly be mixed with new structured defaults.
                    if "prompt_mode" not in loaded and str(loaded.get("prompt", "")).strip():
                        loaded["prompt_mode"] = "raw"
                    data.update(loaded)
        except Exception:
            pass
        return data

    def apply_settings_to_ui(self):
        s = self.settings
        self.prompt_edit.setPlainText(s.get("prompt", ""))
        self.lyrics_edit.setPlainText(s.get("lyrics", ""))
        idx = self.prompt_mode.findData(s.get("prompt_mode", "structured")); self.prompt_mode.setCurrentIndex(max(0, idx))
        self.genre_combo.setCurrentText(str(s.get("genre", "Deep House")))
        self.subgenre_edit.setText(str(s.get("subgenre", "")))
        self.bpm_spin.setValue(int(s.get("bpm", 0)))
        self.key_combo.setCurrentText(str(s.get("key", "Auto")))
        self.scale_combo.setCurrentText(str(s.get("scale", "Auto")))
        self.mood_edit.setText(str(s.get("mood", "")))
        idx = self.vocal_mode.findData(s.get("vocal_mode", "vocal")); self.vocal_mode.setCurrentIndex(max(0, idx))
        self.vocal_style_edit.setText(str(s.get("vocal_style", "")))
        self.instruments_edit.setText(str(s.get("instruments", "")))
        self.arrangement_edit.setText(str(s.get("arrangement", "")))
        self.production_edit.setText(str(s.get("production", "")))
        self.strict_style.setChecked(bool(s.get("strict_style", False)))
        self._sync_prompt_mode()
        self._sync_vocal_mode()
        self._refresh_effective_prompt_preview()
        self.experimental_long_duration.setChecked(bool(s.get("experimental_long_duration", False)))
        self._sync_long_duration_mode(self.experimental_long_duration.isChecked(), save=False)
        self.duration_spin.setValue(float(s.get("duration", 60.0)))
        self.ar_top_k_spin.setValue(int(s.get("ar_top_k", 0)))
        self.flow_steps_spin.setValue(int(s.get("flow_steps", 0)))
        self.flow_guidance_spin.setValue(float(s.get("flow_guidance", 0.0)))
        self.seed_spin.setValue(int(s.get("seed", -1)))
        self.model_path_edit.setText(s.get("model_path", str(DEFAULT_MODEL)))
        self.output_path_edit.setText(s.get("output_path", str(DEFAULT_OUTPUT)))
        idx = self.vram_mode.findData(s.get("vram_mode", "offload"))
        self.vram_mode.setCurrentIndex(max(0, idx))
        idx = self.ar_weight_mode.findData(s.get("ar_weight_mode", "bf16"))
        self.ar_weight_mode.setCurrentIndex(max(0, idx))
        self.extended_logs.setChecked(bool(s.get("extended_logs", False)))
        self.system_hud_toggle.setChecked(bool(s.get("system_hud", True)))
        self._set_system_hud_visible(self.system_hud_toggle.isChecked(), save=False)


    def _sync_long_duration_mode(self, enabled, save=True):
        enabled = bool(enabled)
        current = float(self.duration_spin.value())
        if enabled:
            # Deliberately generous research ceiling. This does not claim MiniMax supports
            # 15 minutes natively; it only lets the user test beyond the documented 6-minute cap.
            self.duration_spin.setMaximum(900.0)
            self.duration_spin.setToolTip(
                "Experimental mode: requests up to 900 seconds (15 minutes) are exposed for testing. "
                "The documented native limit is 360 seconds / 9,000 acoustic frames, so values above 360 seconds are unsupported research values."
            )
            self.duration_limit_note.setText("Experimental: up to 15:00 exposed for testing; documented limit remains 6:00 / 9,000 frames")
        else:
            self.duration_spin.setMaximum(360.0)
            if current > 360.0:
                self.duration_spin.setValue(360.0)
            self.duration_spin.setToolTip(
                "Duration is passed to MiniMax Music 3 as its audio-duration request. Actual track length can vary: "
                "it also depends on the amount of lyrics/words and the model can stop or adjust duration internally."
            )
            self.duration_limit_note.setText("Normal limit: 6:00 = 9,000 acoustic frames at 25 fps")
        if save and hasattr(self, "save_timer"):
            self.schedule_save()

    def _set_system_hud_visible(self, enabled, save=True):
        enabled = bool(enabled)
        if hasattr(self, "system_hud"):
            self.system_hud.setVisible(enabled)
            if enabled:
                self.system_hud.refresh()
                if not self.system_hud.timer.isActive():
                    self.system_hud.timer.start()
            else:
                self.system_hud.timer.stop()
        if save and hasattr(self, "save_timer"):
            self.schedule_save()

    def _set_hud_job(self, phase, restart=False):
        phase = str(phase or "Idle")
        if phase == "Idle":
            self.hud_phase = "Idle"
            self.hud_started_at = None
        else:
            if restart or self.hud_started_at is None:
                self.hud_started_at = time.monotonic()
            self.hud_phase = phase
        if hasattr(self, "system_hud") and self.system_hud.isVisible():
            self.system_hud.refresh()

    def _update_hud_from_log(self, text):
        low = str(text or "").lower()
        if "starting model download" in low or "repository:" in low:
            self._set_hud_job("Downloading model")
        elif "fetching" in low or "downloading" in low or "reconstruction" in low:
            if self.hud_phase.lower().startswith("downloading"):
                self._set_hud_job("Downloading model")
        elif "reading local modular pipeline" in low:
            self._set_hud_job("Reading model index")
        elif "loading local bf16 model components" in low or "loading components" in low:
            self._set_hud_job("Loading components")
        elif "moving pipeline to cuda" in low:
            self._set_hud_job("Moving model to GPU")
        elif "requested maximum duration" in low or "generating audio" in low:
            self._set_hud_job("Generating audio")
        elif "encoding 16-bit pcm wav" in low:
            self._set_hud_job("Encoding WAV")

    def schedule_save(self, *args):
        if hasattr(self, "effective_prompt_preview"):
            try:
                self._refresh_effective_prompt_preview()
            except Exception:
                pass
        self.save_timer.start()

    def save_settings(self):
        SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "prompt": self.prompt_edit.toPlainText(),
            "lyrics": self.lyrics_edit.toPlainText(),
            "prompt_mode": self.prompt_mode.currentData(),
            "genre": self.genre_combo.currentText().strip(),
            "subgenre": self.subgenre_edit.text().strip(),
            "bpm": self.bpm_spin.value(),
            "key": self.key_combo.currentText(),
            "scale": self.scale_combo.currentText(),
            "mood": self.mood_edit.text().strip(),
            "vocal_mode": self.vocal_mode.currentData(),
            "vocal_style": self.vocal_style_edit.text().strip(),
            "instruments": self.instruments_edit.text().strip(),
            "arrangement": self.arrangement_edit.text().strip(),
            "production": self.production_edit.text().strip(),
            "strict_style": self.strict_style.isChecked(),
            "duration": self.duration_spin.value(),
            "ar_top_k": self.ar_top_k_spin.value(),
            "flow_steps": self.flow_steps_spin.value(),
            "flow_guidance": self.flow_guidance_spin.value(),
            "experimental_long_duration": self.experimental_long_duration.isChecked(),
            "seed": self.seed_spin.value(),
            "model_path": self.model_path_edit.text().strip(),
            "output_path": self.output_path_edit.text().strip(),
            "vram_mode": self.vram_mode.currentData(),
            "ar_weight_mode": self.ar_weight_mode.currentData(),
            "extended_logs": self.extended_logs.isChecked(),
            "system_hud": self.system_hud_toggle.isChecked(),
        }
        tmp = SETTINGS_FILE.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, SETTINGS_FILE)

    def log(self, text: str):
        self._update_hud_from_log(text)
        stamp = datetime.now().strftime("%H:%M:%S")
        line = f"[{stamp}] {text.rstrip()}"
        self.log_view.append(line)
        if self.log_file:
            with self.log_file.open("a", encoding="utf-8") as f:
                f.write(line + "\n")

    def begin_log_file(self, prefix="minimax_m3"):
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        self.log_file = LOG_DIR / f"{prefix}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
        self.log(f"Log file: {self.log_file}")

    def browse_model(self):
        p = QFileDialog.getExistingDirectory(self, "Select MiniMax Music 3 model folder", self.model_path_edit.text())
        if p:
            self.model_path_edit.setText(p); self.schedule_save()

    def browse_output(self):
        p = QFileDialog.getExistingDirectory(self, "Select output folder", self.output_path_edit.text())
        if p:
            self.output_path_edit.setText(p); self.schedule_save()

    def open_output_folder(self):
        p = Path(self.output_path_edit.text().strip() or DEFAULT_OUTPUT)
        p.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(p)))

    def validate_local_model(self):
        try:
            from model_downloader import validate_model
            ok, missing = validate_model(Path(self.model_path_edit.text().strip()))
            if ok:
                QMessageBox.information(self, "Model validation", "Required Diffusers model components are present.")
            else:
                QMessageBox.warning(self, "Model validation", "Missing required files:\n\n" + "\n".join(missing))
        except Exception as e:
            QMessageBox.critical(self, "Validation error", str(e))

    def cuda_test(self):
        try:
            import torch, diffusers, transformers
            text = [
                f"Torch: {torch.__version__}",
                f"Diffusers: {diffusers.__version__}",
                f"Transformers: {transformers.__version__}",
                f"CUDA available: {torch.cuda.is_available()}",
            ]
            try:
                import torchao
                text.append(f"TorchAO: {getattr(torchao, '__version__', 'installed')}")
            except Exception:
                text.append("TorchAO: not installed (AR INT8 unavailable)")
            if torch.cuda.is_available():
                text += [
                    f"GPU: {torch.cuda.get_device_name(0)}",
                    f"VRAM: {torch.cuda.get_device_properties(0).total_memory/(1024**3):.2f} GiB",
                ]
            QMessageBox.information(self, "Runtime test", "\n".join(text))
        except Exception:
            QMessageBox.critical(self, "Runtime test", traceback.format_exc())

    def _animate_download_status(self):
        self._download_anim_step = (self._download_anim_step + 1) % 4
        dots = "." * self._download_anim_step
        self.download_status.setText(f"Downloading needed model files{dots}")

    def start_download(self):
        if self._thread is not None:
            return
        destination = Path(self.model_path_edit.text().strip() or DEFAULT_MODEL)
        destination.mkdir(parents=True, exist_ok=True)
        self.begin_log_file("minimax_m3_download")
        self._set_hud_job("Downloading model", restart=True)
        self.download_btn.setEnabled(False)
        self.download_progress.setRange(0, 0)
        self.download_progress.setFormat("Downloading...")
        self.download_status.setText("Downloading needed model files")
        self._download_anim_step = 0
        self.download_anim_timer.start()
        self.statusBar().showMessage("Downloading needed model files...")
        self.log("Starting model download / repair.")
        self._thread = QThread(self)
        self._worker = DownloadWorker(destination)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.log.connect(self.log)
        self._worker.finished.connect(self.download_done)
        self._worker.failed.connect(self.worker_failed)
        self._worker.finished.connect(self._thread.quit)
        self._worker.failed.connect(self._thread.quit)
        self._thread.finished.connect(self.cleanup_thread)
        self._thread.start()

    def download_done(self, path):
        self.download_anim_timer.stop()
        self.download_progress.setRange(0, 100)
        self.download_progress.setValue(100)
        self.download_progress.setFormat("Complete")
        self.download_status.setText("Model files ready")
        self.log(f"Validated model: {path}")
        self.statusBar().showMessage("Model download complete")
        self._set_hud_job("Idle")
        QMessageBox.information(self, "MiniMax Music 3", "Model download and validation completed.")

    def start_generation(self):
        if self._thread is not None:
            return
        conflicts = self._structured_prompt_conflicts()
        if conflicts:
            QMessageBox.warning(
                self,
                "Conflicting music instructions",
                "Structured controls and the additional description contradict each other:\n\n"
                + "\n".join(f"- {item}" for item in conflicts)
                + "\n\nUpdate the structured controls, or switch Prompt mode to Raw description only."
            )
            return
        prompt = self._build_effective_prompt().strip()
        instrumental = self.vocal_mode.currentData() == "instrumental" if self.prompt_mode.currentData() == "structured" else False
        lyrics = "无歌词" if instrumental else self.lyrics_edit.toPlainText().strip()
        if not prompt:
            QMessageBox.warning(self, "Missing music description", "Enter a raw description or fill in the structured music controls first.")
            return
        if not instrumental and not lyrics:
            QMessageBox.warning(self, "Missing lyrics", "Enter lyrics/section tags, or select Instrumental in structured mode.")
            return
        from model_downloader import validate_model
        model_dir = Path(self.model_path_edit.text().strip() or DEFAULT_MODEL)
        ok, missing = validate_model(model_dir)
        if not ok:
            QMessageBox.warning(self, "Model incomplete", "Download/repair the model from Settings first.\n\n" + "\n".join(missing[:8]))
            return
        seed = self.seed_spin.value()
        if seed < 0:
            seed = random.randint(0, 2147483647)
        out_dir = Path(self.output_path_edit.text().strip() or DEFAULT_OUTPUT)
        out_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output = out_dir / f"minimax_m3_{stamp}_seed_{seed}.wav"
        self._set_hud_job("Preparing generation", restart=True)
        self.begin_log_file("minimax_m3")
        self.log(f"Seed: {seed}")
        self.generate_btn.setEnabled(False)
        self.statusBar().showMessage("Generating music...")
        self._thread = QThread(self)
        self._worker = GenerateWorker(
            model_dir, prompt, lyrics, self.duration_spin.value(), seed,
            output, self.vram_mode.currentData(), self.ar_top_k_spin.value(),
            self.flow_steps_spin.value(), self.flow_guidance_spin.value(),
            self.ar_weight_mode.currentData()
        )
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.log.connect(self.log)
        self._worker.finished.connect(self.generation_done)
        self._worker.failed.connect(self.worker_failed)
        self._worker.finished.connect(self._thread.quit)
        self._worker.failed.connect(self._thread.quit)
        self._thread.finished.connect(self.cleanup_thread)
        self._thread.start()

    def generation_done(self, path):
        self.log(f"Generation complete: {path}")
        self.statusBar().showMessage("Generation complete")
        self._set_hud_job("Idle")
        QMessageBox.information(self, "Generation complete", path)

    def worker_failed(self, trace):
        self.download_anim_timer.stop()
        if hasattr(self, "download_progress"):
            self.download_progress.setRange(0, 100)
            self.download_progress.setValue(0)
            self.download_progress.setFormat("Failed")
            self.download_status.setText("Download / operation failed")
        self.log(trace)
        self.statusBar().showMessage("Operation failed")
        self._set_hud_job("Idle")
        QMessageBox.critical(self, "MiniMax Music 3 error", trace[-5000:])

    def cleanup_thread(self):
        if self._thread:
            self._thread.deleteLater()
        self._thread = None
        self._worker = None
        self.generate_btn.setEnabled(True)
        self.download_btn.setEnabled(True)

    def closeEvent(self, event):
        if self._thread is not None:
            QMessageBox.warning(self, "Busy", "A generation or model download is still running.")
            event.ignore()
            return
        self.save_timer.stop()
        self.save_settings()
        event.accept()


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("MiniMax Music 3 Standalone")
    w = MainWindow()
    w.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
