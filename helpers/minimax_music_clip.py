from __future__ import annotations

"""MiniMax H3 Music Clip Creator.

A MiniMax-native music-video helper for FrameVision / standalone PySide6 use.

Design rules:
- Ref2VA is the generation path because it can condition on the song audio slice.
- The master song timeline is authoritative; MiniMax frame counts are generation constraints.
- MiniMax native frame counts are selected from the 17-frame grid (124 + n*17).
- Generated clips may include a small head/tail margin and are trimmed during assembly.
- Lyric songs prefer Whisper phrase boundaries; instrumental songs use evenly distributed,
  beat-aware cuts without treating tiny timing mismatches as fatal.
- A visual change may be directed *inside* one MiniMax generation when a lyric/section
  boundary does not fit a useful physical clip boundary.
- The original full song is muxed back at final assembly; generated clip audio is not used
  as the final soundtrack.

This first helper intentionally does not import the old LTX Music Clip Creator. It keeps a
small project JSON and calls the existing helpers/generate_ref.py backend.
"""

import json
import concurrent.futures
import math
import os
import re
import random
import shutil
import struct
import subprocess
import threading
import sys
import tempfile
import urllib.request
import zipfile
import time
import wave
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

try:
    from PySide6.QtCore import QThread, Qt, Signal, QUrl, QTimer
    from PySide6.QtGui import QDesktopServices, QPixmap
    from PySide6.QtWidgets import (
        QApplication,
        QCheckBox,
        QComboBox,
        QFileDialog,
        QFormLayout,
        QGroupBox,
        QHBoxLayout,
        QHeaderView,
        QLabel,
        QLineEdit,
        QMainWindow,
        QMessageBox,
        QPlainTextEdit,
        QProgressBar,
        QPushButton,
        QSlider,
        QSpinBox,
        QDoubleSpinBox,
        QTabWidget,
        QTableWidget,
        QTableWidgetItem,
        QVBoxLayout,
        QWidget,
        QAbstractItemView,
    )
except Exception as exc:  # pragma: no cover - useful error when launched from wrong env
    raise RuntimeError("MiniMax Music Clip Creator requires PySide6.") from exc


def _detect_project_root() -> Path:
    here = Path(__file__).resolve().parent
    candidates = [here, here.parent, here.parent.parent]
    for candidate in candidates:
        if (candidate / "environments").is_dir() and (candidate / "helpers").is_dir():
            return candidate
    return here.parent


ROOT = _detect_project_root()
HELPERS_DIR = ROOT / "helpers"
OUTPUT_ROOT = ROOT / "output" / "minimax_music_clips"
SETTINGS_PATH = ROOT / "presets" / "minimax_music_clip_settings.json"
AUTOSAVE_PATH = ROOT / "presets" / "setsave" / "minimax_music_clip.json"
WHISPER_DIR = ROOT / "presets" / "bin" / "whisper"
WHISPER_MODEL = WHISPER_DIR / "ggml-small.bin"
WHISPER_RUNTIME_ZIP = WHISPER_DIR / "_whisper_runtime.zip"
WHISPER_RUNTIME_URL = "https://github.com/ggml-org/whisper.cpp/releases/download/v1.9.1/whisper-bin-x64.zip"
WHISPER_MODEL_URL = "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-small.bin?download=true"
MINIMAX_ENV = ROOT / "environments" / ".minimax_h3_int4"
MINIMAX_PY = MINIMAX_ENV / "python.exe"
if not MINIMAX_PY.is_file():
    MINIMAX_PY = MINIMAX_ENV / "Scripts" / "python.exe"
GENERATE_REF = HELPERS_DIR / "generate_ref.py"

NORMAL_FRAME_MAX = 719
MUSIC_FRAME_MIN = 124
MUSIC_FRAME_DEFAULT_MAX = 396
MUSIC_FRAME_GRID = tuple(range(MUSIC_FRAME_MIN, MUSIC_FRAME_DEFAULT_MAX + 1, 17))
FPS = 24.0

RESOLUTION_PRESETS: Dict[str, Dict[str, Tuple[int, int]]] = {
    "576 × 320": {"16:9": (576, 320), "9:16": (320, 576), "1:1": (320, 320)},
    "736 × 384": {"16:9": (736, 384), "9:16": (384, 736), "1:1": (384, 384)},
    "832 × 480": {"16:9": (832, 480), "9:16": (480, 832), "1:1": (480, 480)},
    "960 × 544": {"16:9": (960, 544), "9:16": (544, 960), "1:1": (544, 544)},
    "1280 × 720": {"16:9": (1280, 704), "9:16": (704, 1280), "1:1": (704, 704)},
    "1344 × 768": {"16:9": (1344, 768), "9:16": (768, 1344), "1:1": (768, 768)},
    "1920 × 1088": {"16:9": (1920, 1088), "9:16": (1088, 1920), "1:1": (1088, 1088)},
}

REFERENCE_TYPES = ("Character", "Background / Location", "Object / Prop", "Style / Mood", "Picture / Composition anchor", "Other")
REFERENCE_TYPE_ALIASES = {
    "Location": "Background / Location",
    "Object": "Object / Prop",
    "Style": "Style / Mood",
}

def _normalise_reference_kind(kind: str) -> str:
    k = (kind or "").strip()
    k = REFERENCE_TYPE_ALIASES.get(k, k)
    return k if k in REFERENCE_TYPES else "Other"


def _safe_stem(value: str) -> str:
    stem = Path(value or "music_video").stem
    stem = re.sub(r"[^A-Za-z0-9._-]+", "_", stem).strip("._-")
    return stem[:80] or "music_video"


def _fmt_time(seconds: float) -> str:
    seconds = max(0.0, float(seconds or 0.0))
    minutes = int(seconds // 60)
    sec = seconds - minutes * 60
    return f"{minutes:02d}:{sec:05.2f}"


def _existing_executable(candidates: Iterable[Path | str]) -> str:
    for candidate in candidates:
        text = str(candidate)
        if not text:
            continue
        p = Path(text)
        if p.is_file():
            return str(p)
        found = shutil.which(text)
        if found:
            return found
    return ""


def ffmpeg_path() -> str:
    return _existing_executable(
        [
            ROOT / "presets" / "bin" / "ffmpeg.exe",
            ROOT / "presets" / "bin" / "ffmpeg",
            "ffmpeg.exe",
            "ffmpeg",
        ]
    )


def ffprobe_path() -> str:
    return _existing_executable(
        [
            ROOT / "presets" / "bin" / "ffprobe.exe",
            ROOT / "presets" / "bin" / "ffprobe",
            "ffprobe.exe",
            "ffprobe",
        ]
    )


def probe_duration(path: str) -> float:
    probe = ffprobe_path()
    if not probe:
        return 0.0
    cp = subprocess.run(
        [probe, "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", path],
        capture_output=True,
        text=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    try:
        return max(0.0, float((cp.stdout or "").strip()))
    except Exception:
        return 0.0


def minimax_frame_grid(max_frames: int) -> List[int]:
    max_frames = max(MUSIC_FRAME_MIN, min(NORMAL_FRAME_MAX, int(max_frames)))
    values = list(range(MUSIC_FRAME_MIN, max_frames + 1, 17))
    # 480 is supported by the existing MiniMax GUI/backend even though it is off-grid.
    if MUSIC_FRAME_MIN <= 480 <= max_frames:
        values.append(480)
    return sorted(set(values))


def nearest_generation_frames(required_seconds: float, max_frames: int, prefer_longer: bool = True) -> int:
    """Choose a valid MiniMax frame count for a required amount of source time.

    Prefer a duration that is at least as long as requested. If the request exceeds
    the user's max, return the maximum and let the planner split the edit interval.
    """
    grid = minimax_frame_grid(max_frames)
    if not grid:
        return MUSIC_FRAME_MIN
    wanted = max(0.0, float(required_seconds)) * FPS
    if prefer_longer:
        for frames in grid:
            if frames + 1e-9 >= wanted:
                return frames
    return min(grid, key=lambda f: abs(float(f) - wanted))


def _clean_minimax_env() -> Dict[str, str]:
    env = os.environ.copy()
    for key in (
        "PYTHONPATH",
        "PYTHONHOME",
        "VIRTUAL_ENV",
        "CONDA_DEFAULT_ENV",
        "CONDA_PROMPT_MODIFIER",
        "_CE_CONDA",
        "_CE_M",
    ):
        env.pop(key, None)
    if MINIMAX_PY.is_file():
        prefix = MINIMAX_PY.parents[1]
        env["CONDA_PREFIX"] = str(prefix)
        preferred = [prefix, prefix / "Scripts", prefix / "Library" / "bin"]
        old_parts = [p for p in env.get("PATH", "").split(os.pathsep) if p]
        cleaned: List[str] = []
        for part in old_parts:
            low = os.path.normcase(os.path.abspath(part))
            if os.path.normcase(str(ROOT / "environments")) in low and os.path.normcase(str(prefix)) not in low:
                continue
            cleaned.append(part)
        env["PATH"] = os.pathsep.join([str(p) for p in preferred] + cleaned)
    env["PYTHONNOUSERSITE"] = "1"
    return env


@dataclass
class Beat:
    time: float
    strength: float
    kind: str


@dataclass
class Section:
    start: float
    end: float
    kind: str


@dataclass
class AnalysisResult:
    duration: float = 0.0
    beats: List[Beat] = field(default_factory=list)
    sections: List[Section] = field(default_factory=list)


@dataclass
class LyricSegment:
    start: float
    end: float
    text: str


@dataclass
class ReferenceAsset:
    name: str
    kind: str
    path: str
    description: str = ""
    enabled: bool = True


@dataclass
class MusicShot:
    index: int
    edit_start: float
    edit_end: float
    frames: int
    generation_start: float
    generation_end: float
    trim_in: float
    lyrics: str = ""
    section: str = ""
    prompt: str = ""
    reference_names: List[str] = field(default_factory=list)
    internal_cuts: List[float] = field(default_factory=list)  # song-absolute seconds
    output_path: str = ""
    status: str = "Planned"
    seed: int = -1

    @property
    def edit_duration(self) -> float:
        return max(0.0, self.edit_end - self.edit_start)

    @property
    def generation_duration(self) -> float:
        return self.frames / FPS


@dataclass
class MusicProject:
    version: int = 1
    audio_path: str = ""
    output_dir: str = ""
    title: str = ""
    main_idea: str = ""
    style_theme: str = ""
    characters_subjects: str = ""
    locations_world: str = ""
    camera_choreography: str = ""
    resolution: str = "832 × 480"
    aspect: str = "16:9"
    max_frames: int = MUSIC_FRAME_DEFAULT_MAX
    head_padding: float = 0.35
    tail_padding: float = 0.45
    phrase_snap_tolerance: float = 1.25
    beat_sensitivity: int = 10
    whisper_timing_enabled: bool = True
    job_seed: int = -1
    steps: int = 15
    cfg: float = 1.0
    shift: float = 12.0
    audio_shift: float = 3.0
    ref_image_size: str = "match"
    sage_attention: bool = False
    spectrum: bool = False
    vram_manager_enabled: bool = True
    vram_auto_bypass: bool = True
    # Keep the MiniMax GUI fresh-install VRAM defaults here so Music Clip Creator
    # launches Ref2VA with the same protection policy instead of only passing the
    # abbreviated --vram-manager-auto flag.
    vram_residency_engine: str = "static"
    vram_runtime_free_gb: float = 0.50
    vram_text_headroom_gb: float = 1.0
    vram_diffusion_headroom_gb: float = 1.0
    vram_offload_chunk_mb: int = 512
    vram_max_resident_weights_gb: float = 0.0
    vram_block_check_interval: int = 1
    vram_async_streams: int = 2
    vram_video_vae_reserve_gb: float = 2.0
    vram_audio_vae_reserve_gb: float = 1.0
    vram_residency_fill: bool = False
    vram_residency_target_free_gb: float = 0.50
    vram_residency_warmup_blocks: int = 2
    vram_residency_refill_interval: int = 1
    turbo_lora_path: str = ""
    turbo_lora_strength: float = 1.0
    references: List[ReferenceAsset] = field(default_factory=list)
    lyrics: List[LyricSegment] = field(default_factory=list)
    analysis: AnalysisResult = field(default_factory=AnalysisResult)
    shots: List[MusicShot] = field(default_factory=list)


# ------------------------- lightweight music analysis -------------------------


def analyze_music(audio_path: str, sensitivity: int = 10) -> AnalysisResult:
    """Port of the useful lightweight RMS beat/energy analysis from Music Clip Creator."""
    ffmpeg = ffmpeg_path()
    if not ffmpeg:
        raise RuntimeError("FFmpeg was not found in presets/bin or PATH.")
    tmpdir = Path(tempfile.mkdtemp(prefix="fv_minimax_music_an_"))
    wav_path = tmpdir / "mono.wav"
    try:
        cp = subprocess.run(
            [ffmpeg, "-y", "-i", audio_path, "-vn", "-ac", "1", "-ar", "44100", "-acodec", "pcm_s16le", str(wav_path)],
            capture_output=True,
            text=True,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if cp.returncode != 0 or not wav_path.is_file():
            raise RuntimeError("Failed to convert audio for analysis:\n" + (cp.stderr or cp.stdout or ""))

        with wave.open(str(wav_path), "rb") as wf:
            fr = wf.getframerate()
            total_frames = wf.getnframes()
            duration = total_frames / float(fr) if fr else 0.0
            win_size = max(1, int(fr * 0.05))
            values: List[float] = []
            times: List[float] = []
            read = 0
            while read < total_frames:
                n = min(win_size, total_frames - read)
                raw = wf.readframes(n)
                read += n
                if not raw:
                    break
                count = len(raw) // 2
                if not count:
                    rms = 0.0
                else:
                    samples = struct.unpack("<" + "h" * count, raw)
                    acc = sum((s / 32768.0) ** 2 for s in samples)
                    rms = math.sqrt(acc / count)
                values.append(rms)
                times.append(read / float(fr))

        if not values:
            raise RuntimeError("Audio analysis produced no samples.")
        max_v = max(values) or 1.0
        norm = [v / max_v for v in values]
        mean = sum(norm) / len(norm)
        std = math.sqrt(sum((v - mean) ** 2 for v in norm) / len(norm))
        sens = max(0.0, min(20.0, float(sensitivity)))
        if sens <= 10.0:
            frac = (10.0 - sens) / 10.0
            scale = 1.0 + (frac ** 1.75) * 2.60
        else:
            frac = (sens - 10.0) / 10.0
            scale = 1.0 - (frac ** 1.20) * 0.42
        scale = max(0.55, min(3.60, scale))
        beat_thr = mean + std * 0.7 * scale
        major_thr = mean + std * 1.4 * scale

        beats: List[Beat] = []
        last_peak = -9999
        min_dist = max(1, int(0.15 / 0.05))
        for i in range(1, len(norm) - 1):
            v = norm[i]
            if v < beat_thr or not (v >= norm[i - 1] and v >= norm[i + 1]):
                continue
            if i - last_peak < min_dist:
                if beats and v > beats[-1].strength:
                    beats[-1] = Beat(times[i], v, "major" if v >= major_thr else "minor")
                    last_peak = i
                continue
            beats.append(Beat(times[i], v, "major" if v >= major_thr else "minor"))
            last_peak = i
        beats.sort(key=lambda b: b.time)

        win1 = max(1, int(1.0 / 0.05))
        e_vals: List[float] = []
        e_times: List[float] = []
        for i in range(0, len(norm), win1):
            chunk = norm[i : i + win1]
            if chunk:
                e_vals.append(sum(chunk) / len(chunk))
                e_times.append(i * 0.05)
        if not e_vals:
            e_vals, e_times = [mean], [0.0]
        e_mean = sum(e_vals) / len(e_vals)
        e_std = math.sqrt(sum((v - e_mean) ** 2 for v in e_vals) / len(e_vals))
        low_thr, high_thr = e_mean - 0.4 * e_std, e_mean + 0.4 * e_std
        raw_sections: List[Section] = []
        cur_kind: Optional[str] = None
        cur_start = 0.0
        for t, value in zip(e_times, e_vals):
            kind = "intro_or_break" if value < low_thr else ("chorus_or_drop" if value > high_thr else "verse_or_mid")
            if cur_kind is None:
                cur_kind, cur_start = kind, t
            elif kind != cur_kind:
                raw_sections.append(Section(cur_start, t, cur_kind))
                cur_kind, cur_start = kind, t
        if cur_kind is not None:
            raw_sections.append(Section(cur_start, duration, cur_kind))
        labelled: List[Section] = []
        chorus_seen = False
        for idx, section in enumerate(raw_sections):
            if idx == 0:
                kind = "intro"
            elif section.kind == "chorus_or_drop":
                kind = "chorus" if not chorus_seen else "drop"
                chorus_seen = True
            elif section.kind == "intro_or_break":
                kind = "break"
            else:
                kind = "verse"
            labelled.append(Section(section.start, section.end, kind))
        if labelled:
            labelled[-1] = Section(labelled[-1].start, labelled[-1].end, "outro")
        return AnalysisResult(duration=duration, beats=beats, sections=labelled)
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


# ------------------------------ shot planning -------------------------------


def _section_at(t: float, sections: Sequence[Section]) -> str:
    for section in sections:
        if section.start - 1e-6 <= t < section.end + 1e-6:
            return section.kind
    return "unknown"


def _lyrics_overlapping(start: float, end: float, lyrics: Sequence[LyricSegment]) -> str:
    parts: List[str] = []
    for seg in lyrics:
        if max(start, seg.start) < min(end, seg.end):
            text = re.sub(r"\s+", " ", seg.text or "").strip()
            if text:
                parts.append(text)
    return " ".join(parts).strip()


def _nearest_beat(target: float, beats: Sequence[Beat], tolerance: float = 0.75) -> float:
    candidates = [b.time for b in beats if abs(b.time - target) <= tolerance]
    if not candidates:
        return target
    return min(candidates, key=lambda x: abs(x - target))


def _candidate_lyric_boundaries(lyrics: Sequence[LyricSegment], duration: float) -> List[float]:
    """Return lyric *line ends* as the primary physical edit anchors.

    Whisper segment starts are useful for prompt context, but a line ending is the
    natural place to hand the song to the next generated clip.  Using starts and
    ends equally was one reason the old planner could cut through phrases.
    """
    vals = {max(0.0, duration)}
    for seg in lyrics:
        if 0.0 < seg.end < duration:
            vals.add(round(seg.end, 3))
    return sorted(vals)


def _candidate_lyric_starts(lyrics: Sequence[LyricSegment], duration: float) -> List[float]:
    vals = set()
    for seg in lyrics:
        if 0.0 < seg.start < duration:
            vals.add(round(seg.start, 3))
    return sorted(vals)


def _valid_edit_lengths(cursor: float, project: MusicProject, max_frames: int) -> List[Tuple[float, int]]:
    """Map valid H3 frame counts to the edit length they can safely cover here."""
    generation_start = max(0.0, cursor - project.head_padding)
    trim_in = cursor - generation_start
    out: List[Tuple[float, int]] = []
    for frames in minimax_frame_grid(max_frames):
        edit_len = frames / FPS - trim_in - project.tail_padding
        if edit_len > 1.20:
            out.append((edit_len, frames))
    return out


def _choose_next_lyric_boundary(
    cursor: float,
    hard_limit: float,
    lyric_ends: Sequence[float],
    lyric_starts: Sequence[float],
    section_bounds: Sequence[float],
    beats: Sequence[Beat],
    project: MusicProject,
    max_frames: int,
) -> float:
    """Choose a physical cut with lyric line endings as the timing skeleton.

    Priority is deliberately different from the old duration-packing planner:
      1. lyric line end that is already close to a valid MiniMax frame duration
      2. other lyric line ends that can be covered by a valid duration and trimmed
      3. section boundaries / lyric starts
      4. beats only as a small tie-breaker

    The song edit boundary remains authoritative; MiniMax may generate a little past
    it and final assembly trims the excess.
    """
    min_end = cursor + 1.25
    valid_lengths = _valid_edit_lengths(cursor, project, max_frames)
    if not valid_lengths:
        return hard_limit

    def frame_fit_error(end: float) -> Tuple[float, int]:
        wanted = end - cursor
        # Prefer a frame count that covers the boundary; trimming is allowed.
        covering = [(length - wanted, frames) for length, frames in valid_lengths if length + 1e-6 >= wanted]
        if not covering:
            return (999.0, max_frames)
        return min(covering, key=lambda x: x[0])

    def beat_distance(t: float) -> float:
        if not beats:
            return 9.0
        return min(abs(b.time - t) for b in beats)

    lyric_candidates = [v for v in lyric_ends if min_end < v <= hard_limit + 1e-6]
    if lyric_candidates:
        scored = []
        for value in lyric_candidates:
            excess, frames = frame_fit_error(value)
            if excess >= 998.0:
                continue
            # Near-grid lyric endings are the jackpot.  Otherwise still prefer a
            # lyric ending and trim the generation overrun rather than crossing it.
            near_grid = 0 if excess <= 0.35 else 1
            # Prefer useful clip lengths over tiny fragments, then smaller trim.
            short_penalty = max(0.0, 4.0 - (value - cursor))
            scored.append((near_grid, short_penalty, excess, beat_distance(value), -value, value, frames))
        if scored:
            return min(scored)[5]

    # No usable lyric ending before the current H3 limit. Use meaningful structural
    # boundaries before falling back to pure duration packing.
    structural = [v for v in section_bounds if min_end < v <= hard_limit + 1e-6]
    structural += [v for v in lyric_starts if min_end < v <= hard_limit + 1e-6]
    if structural:
        candidates = []
        for value in sorted(set(structural)):
            excess, _frames = frame_fit_error(value)
            if excess < 998.0:
                candidates.append((excess, beat_distance(value), -value, value))
        if candidates:
            return min(candidates)[3]

    return hard_limit


def build_shot_plan(project: MusicProject) -> List[MusicShot]:
    duration = float(project.analysis.duration or probe_duration(project.audio_path))
    if duration <= 0.05:
        raise RuntimeError("Could not determine song duration.")

    max_frames = max(MUSIC_FRAME_MIN, int(project.max_frames))
    max_gen_seconds = max_frames / FPS
    usable_edit_max = max(1.5, max_gen_seconds - project.head_padding - project.tail_padding)
    target_edit = usable_edit_max * 0.90
    lyrics = list(project.lyrics)
    use_lyric_timing = bool(project.whisper_timing_enabled and lyrics)
    lyric_bounds = _candidate_lyric_boundaries(lyrics, duration)
    lyric_starts = _candidate_lyric_starts(lyrics, duration)
    section_bounds = sorted({round(s.start, 3) for s in project.analysis.sections} | {round(s.end, 3) for s in project.analysis.sections})

    edit_ranges: List[Tuple[float, float]] = []
    cursor = 0.0
    while cursor < duration - 0.04:
        hard_limit = min(duration, cursor + usable_edit_max)
        target = min(duration, cursor + target_edit)
        if use_lyric_timing:
            end = _choose_next_lyric_boundary(
                cursor, hard_limit, lyric_bounds, lyric_starts, section_bounds,
                project.analysis.beats, project, max_frames
            )
        else:
            # Instrumental/default mode: keep duration packing as the fallback and
            # only nudge the proposed cut to a nearby beat.
            end = _nearest_beat(target, project.analysis.beats, tolerance=0.85)
            if end <= cursor + 1.25 or end > hard_limit + 0.05:
                end = hard_limit
        if duration - end < 1.0:
            end = duration
        if end <= cursor + 0.20:
            end = min(duration, cursor + usable_edit_max)
        edit_ranges.append((round(cursor, 3), round(end, 3)))
        cursor = end

    # Avoid a tiny final physical clip: fold it into the previous edit interval when
    # the previous generation can still cover it. Otherwise keep it; assembly is best effort.
    if len(edit_ranges) >= 2 and edit_ranges[-1][1] - edit_ranges[-1][0] < 1.25:
        prev = edit_ranges[-2]
        merged = (prev[0], edit_ranges[-1][1])
        needed = merged[1] - merged[0] + project.head_padding + project.tail_padding
        if needed <= max_gen_seconds + 0.05:
            edit_ranges[-2:] = [merged]

    shots: List[MusicShot] = []
    all_visual_boundaries = sorted(set(lyric_bounds + lyric_starts + section_bounds))
    for idx, (edit_start, edit_end) in enumerate(edit_ranges, start=1):
        generation_start = max(0.0, edit_start - project.head_padding)
        trim_in = edit_start - generation_start
        required = trim_in + (edit_end - edit_start) + project.tail_padding
        frames = nearest_generation_frames(required, max_frames, prefer_longer=True)
        generation_duration = frames / FPS
        generation_end = min(duration, generation_start + generation_duration)
        # If the requested range is too close to the song end, the audio slice may be
        # shorter than the MiniMax generation. This is allowed; final trim remains authoritative.
        internal = [
            b for b in all_visual_boundaries
            if edit_start + 0.30 < b < edit_end - 0.30
        ]
        lyric_text = _lyrics_overlapping(edit_start, edit_end, lyrics)
        section = _section_at((edit_start + edit_end) * 0.5, project.analysis.sections)
        shots.append(
            MusicShot(
                index=idx,
                edit_start=edit_start,
                edit_end=edit_end,
                frames=frames,
                generation_start=round(generation_start, 3),
                generation_end=round(generation_end, 3),
                trim_in=round(trim_in, 3),
                lyrics=lyric_text,
                section=section,
                internal_cuts=internal,
            )
        )
    return shots


def _selected_refs_for_shot(project: MusicProject, shot: MusicShot) -> List[ReferenceAsset]:
    by_name = {r.name: r for r in project.references if r.enabled and Path(r.path).is_file()}
    return [by_name[name] for name in shot.reference_names if name in by_name][:9]


def _h3_ref_subject_definitions(project: MusicProject, refs: Sequence[ReferenceAsset]) -> List[str]:
    lines: List[str] = []
    subject_no = 0
    for picture_no, ref in enumerate(refs, 1):
        name = (ref.name or f"reference {picture_no}").strip()
        detail = re.sub(r"\s+", " ", (ref.description or "").strip())
        kind = _normalise_reference_kind(ref.kind)
        if kind == "Picture / Composition anchor":
            text = f"<Picture {picture_no}> is the composition/shot-planning reference named {name}. Use its framing, spatial arrangement and visual layout as a concrete shot anchor."
        else:
            subject_no += 1
            if kind == "Character":
                purpose = "reusable character/person"
            elif kind == "Background / Location":
                purpose = "reusable background/location environment"
            elif kind == "Object / Prop":
                purpose = "reusable object/prop"
            elif kind == "Style / Mood":
                purpose = "reusable visual style/mood reference"
            else:
                purpose = "reusable referenced subject"
            text = f"<Subject {subject_no}> is {name}, the {purpose} defined by <Picture {picture_no}>."
        if detail:
            text += f" Purpose/details: {detail}."
        lines.append(text)
    if project.characters_subjects.strip():
        lines.append(
            "Project reference-purpose details: "
            + re.sub(r"\s+", " ", project.characters_subjects.strip())
            + ". Apply these named roles, outfit/prop rules, environment rules and continuity details to the matching references above."
        )
    lines.append(
        "<Audio 1> is the supplied song segment for this shot. It is the authoritative music/performance source for vocals, lyrics, rhythm, beat, timing and continuity."
    )
    return lines


def _h3_reference_labels(refs: Sequence[ReferenceAsset]) -> Dict[str, List[Tuple[int, int, ReferenceAsset]]]:
    """Return H3 labels grouped by semantic role.

    Tuple format is ``(subject_no, picture_no, ref)``. Composition anchors have a
    subject number of 0 because they remain standalone <Picture N> references.
    """
    groups: Dict[str, List[Tuple[int, int, ReferenceAsset]]] = {
        "characters": [],
        "backgrounds": [],
        "objects": [],
        "styles": [],
        "others": [],
        "pictures": [],
    }
    subject_no = 0
    for picture_no, ref in enumerate(refs, 1):
        kind = _normalise_reference_kind(ref.kind)
        if kind == "Picture / Composition anchor":
            groups["pictures"].append((0, picture_no, ref))
            continue
        subject_no += 1
        item = (subject_no, picture_no, ref)
        if kind == "Character":
            groups["characters"].append(item)
        elif kind == "Background / Location":
            groups["backgrounds"].append(item)
        elif kind == "Object / Prop":
            groups["objects"].append(item)
        elif kind == "Style / Mood":
            groups["styles"].append(item)
        else:
            groups["others"].append(item)
    return groups


def build_h3_reference_prompt(
    project: MusicProject,
    shot: MusicShot,
    selected_refs: Sequence[ReferenceAsset],
) -> str:
    """Build a MiniMax-H3 full-reference prompt using the official Ref2VA layout.

    Reference roles stay semantically separate: characters are performers, locations
    are environments, props are objects, style refs guide appearance, and composition
    pictures anchor framing. This prevents a background reference from being treated as
    another visible person/performer.
    """
    refs = list(selected_refs)[:9]

    # 1) subject_definitions
    sections: List[str] = ["subject_definitions:"]
    sections.extend(_h3_ref_subject_definitions(project, refs))

    # Keep role groups separate throughout the prompt. A background may legitimately be
    # a reusable <Subject N> in H3, but it must never be grouped with character performers.
    groups = _h3_reference_labels(refs)
    characters = groups["characters"]
    backgrounds = groups["backgrounds"]
    objects = groups["objects"]
    styles = groups["styles"]
    others = groups["others"]
    picture_anchors = groups["pictures"]

    def labels(items: Sequence[Tuple[int, int, ReferenceAsset]]) -> str:
        return ", ".join(f"<Subject {subject_no}>" for subject_no, _picture_no, _ref in items if subject_no)

    # 2) summary
    story = re.sub(r"\s+", " ", project.main_idea.strip()) if project.main_idea.strip() else "the current music-video story"
    summary_bits = [f"[reference generation + audio reuse] Create a music-video shot for {story}."]
    if characters:
        summary_bits.append(f"Use {labels(characters)} as the visible character/performer reference{'s' if len(characters) != 1 else ''}.")
    if backgrounds:
        summary_bits.append(f"Use {labels(backgrounds)} only as the scene environment/background reference{'s' if len(backgrounds) != 1 else ''}, not as additional people or performers.")
    if objects:
        summary_bits.append(f"Use {labels(objects)} only as referenced objects/props when they belong in the scene.")
    if styles:
        summary_bits.append(f"Use {labels(styles)} only for visual style/mood guidance.")
    if others:
        summary_bits.append(f"Use {labels(others)} according to their explicitly defined purposes.")
    if picture_anchors:
        picture_summary = ", ".join(f"<Picture {picture_no}>" for _subject_no, picture_no, _ref in picture_anchors)
        summary_bits.append(f"Use {picture_summary} as concrete composition/shot-planning anchors rather than as character identities.")
    summary_bits.append("Reuse <Audio 1> as the continuous authoritative song/performance source for this shot.")
    sections.append("summary:")
    sections.append(" ".join(summary_bits))

    # 3) retention_analysis
    sections.append("retention_analysis:")
    for group_name in ("characters", "backgrounds", "objects", "styles", "others"):
        for i, _picture_no, ref in groups[group_name]:
            name = (ref.name or f"Subject {i}").strip()
            detail = re.sub(r"\s+", " ", (ref.description or "").strip())
            kind = _normalise_reference_kind(ref.kind)
            if kind == "Background / Location":
                rule = f"<Subject {i}>: fully_preserved - keep {name} strictly as the referenced environment/background, preserving its recognizable room design, lighting, layout and named recurring features; it is not a person or performer."
            elif kind == "Object / Prop":
                rule = f"<Subject {i}>: fully_preserved - keep {name} strictly as the referenced object/prop with its recognizable appearance and intended purpose; it is not an additional person or performer."
            elif kind == "Style / Mood":
                rule = f"<Subject {i}>: weak_reference - use {name} only for the intended visual style/mood without turning it into a physical character or object."
            elif kind == "Character":
                rule = f"<Subject {i}>: fully_preserved - keep {name}'s referenced identity, appearance and defined character role consistent."
            else:
                rule = f"<Subject {i}>: fully_preserved - keep {name}'s explicitly defined reference role consistent."
            if detail:
                rule += f" Preserve these reference details: {detail}."
            sections.append(rule)
    for _subject_no, picture_no, ref in picture_anchors:
        name = (ref.name or f"Picture {picture_no}").strip()
        detail = re.sub(r"\s+", " ", (ref.description or "").strip())
        rule = f"<Picture {picture_no}>: partially_preserved - use {name} as the requested framing/composition anchor while allowing scene motion and shot development."
        if detail:
            rule += f" Follow these anchor details: {detail}."
        sections.append(rule)
    if project.characters_subjects.strip() and refs:
        sections.append(
            "Named subject rules: fully_preserved - "
            + re.sub(r"\s+", " ", project.characters_subjects.strip())
            + "."
        )
    sections.append(
        "<Audio 1>: fully_copy - keep the supplied song segment continuous as the target performance track; preserve its existing vocals, lyrics, instrumental passages, rhythm and timing instead of inventing replacement vocals or music."
    )

    # 4) detailed_description
    sections.append("detailed_description:")
    style = re.sub(r"\s+", " ", project.style_theme.strip()) if project.style_theme.strip() else "cinematic music-video"
    location = re.sub(r"\s+", " ", project.locations_world.strip())
    if location:
        # Avoid awkward output such as "set in in the basement" when the user already
        # typed a leading "in".
        location_text = re.sub(r"^(?:set\s+in\s+|in\s+)", "", location, flags=re.IGNORECASE).strip()
        sections.append(f"The target video uses a {style} style and takes place in {location_text}.")
    else:
        sections.append(f"The target video uses a {style} style.")

    shot1: List[str] = ["[Shot 1]"]
    if characters:
        if len(characters) == 1:
            shot1.append(f"The visible character is {labels(characters)}; preserve this character's identity and role.")
        else:
            shot1.append(f"The visible characters are {labels(characters)}; preserve their identities and roles.")
    if backgrounds:
        shot1.append(f"Place the character action inside {labels(backgrounds)} as the referenced background/location; preserve the environment but do not create a second person from it.")
    if objects:
        shot1.append(f"Use {labels(objects)} only as props/objects where relevant to the action.")
    if styles:
        shot1.append(f"Apply {labels(styles)} only as visual style/mood guidance.")
    if picture_anchors:
        first_pictures = ", ".join(f"<Picture {picture_no}>" for _subject_no, picture_no, _ref in picture_anchors)
        shot1.append(f"Use {first_pictures} for the specified composition/framing purpose.")
    if project.main_idea.strip():
        shot1.append(re.sub(r"\s+", " ", project.main_idea.strip()) + ".")
    shot1.append(f"This is the {shot.section or 'current'} section of the music video.")
    if project.camera_choreography.strip():
        shot1.append("Camera movement: " + re.sub(r"\s+", " ", project.camera_choreography.strip()) + ".")
    shot1.append("<Audio 1> begins immediately and remains continuous and synchronized with the visible performance.")

    if shot.lyrics.strip():
        lyric = re.sub(r"\s+", " ", shot.lyrics.strip())
        if len(characters) == 1:
            performer = f"<Subject {characters[0][0]}>"
            shot1.append(
                f"When <Audio 1> reaches the lyric phrase <d>[Original language] {lyric}</d>, {performer} is the singer/performer and lip-syncs/sings it in time with the supplied audio."
            )
        elif len(characters) > 1:
            shot1.append(
                f"When <Audio 1> reaches the lyric phrase <d>[Original language] {lyric}</d>, only the character whose user-defined purpose identifies them as the singer/lead vocalist performs the lyric; keep every other referenced character in their defined role."
            )
        else:
            shot1.append(
                f"When <Audio 1> reaches the lyric phrase <d>[Original language] {lyric}</d>, synchronize the visible performance and mouth movement to the supplied audio."
            )
    else:
        shot1.append("This section contains no supplied lyric phrase; keep the instrumental/vocal-free part of <Audio 1> intact and do not introduce a new vocal performance.")
    sections.append(" ".join(shot1))

    for cut_no, absolute in enumerate(shot.internal_cuts[:4], start=2):
        rel = max(0.0, absolute - shot.generation_start)
        mm = int(rel // 60)
        ss = rel - mm * 60
        timestamp = f"{mm:02d}:{ss:06.3f}"
        sections.append(
            f"[Shot {cut_no}] At {timestamp}, the camera cuts to a complementary new angle or visual beat within the same established scene. "
            "Keep the same character identities, performer roles, props and background/location roles. <Audio 1> continues seamlessly across the cut with unchanged musical timing and vocal continuity."
        )

    sections.append("Keep all visible character identities and all reference-purpose details stable across shots. Background/location references remain environments, and prop references remain objects. Start useful action immediately. No visible subtitles or captions.")

    # 5/6) audio sections. The master/reference song is the only music layer we want.
    sections.append("overall_soundscape:")
    sections.append(
        "Natural physical ambience and incidental scene sounds may be subtle and secondary. Do not let them replace, interrupt, or obscure the synchronized <Audio 1> performance."
    )
    sections.append("non_diegetic_music:")
    sections.append("<Audio 1> is directly reused as the complete music track for this shot. Do not add a separate score or replacement music.")

    return "\n".join(x.strip() for x in sections if x and x.strip())

def build_default_prompt(project: MusicProject, shot: MusicShot) -> str:
    return build_h3_reference_prompt(project, shot, _selected_refs_for_shot(project, shot))


def build_generation_prompt(project: MusicProject, shot: MusicShot, selected_refs: List[ReferenceAsset]) -> str:
    """Always launch Ref2VA with the H3-native full-reference prompt structure.

    Legacy LTX-style saved prompts are intentionally not forwarded to MiniMax anymore;
    the current project brief, reference-purpose details, shot timing and lyrics are the
    source of truth and are converted here at generation time.
    """
    return build_h3_reference_prompt(project, shot, selected_refs)


def auto_assign_references(project: MusicProject, shot: MusicShot) -> List[str]:
    """Conservative first-pass router: refs named in prompt/brief win; character refs otherwise stay available.

    Users can edit the assignment in the shot table. This avoids sending all nine references
    blindly while still giving the director useful defaults.
    """
    enabled = [r for r in project.references if r.enabled and Path(r.path).is_file()]
    blob = " ".join((shot.prompt, shot.lyrics, project.characters_subjects, project.main_idea)).lower()
    chosen: List[str] = []
    for ref in enabled:
        if ref.name and ref.name.lower() in blob:
            chosen.append(ref.name)
    # Background/location and style refs are normally project-wide context, so include
    # them even when the user did not repeat their names in every shot prompt.
    for ref in enabled:
        kind = _normalise_reference_kind(ref.kind)
        if kind in ("Background / Location", "Style / Mood") and ref.name not in chosen:
            chosen.append(ref.name)
    if not any(_normalise_reference_kind(r.kind) == "Character" and r.name in chosen for r in enabled):
        chars = [r.name for r in enabled if _normalise_reference_kind(r.kind) == "Character"]
        chosen.extend(x for x in chars[:2] if x not in chosen)
    # Objects/props and picture/composition anchors remain opt-in by name/purpose so an
    # unrelated prop or storyboard image is not injected into every shot automatically.
    return chosen[:9]


# ----------------------------- worker threads ------------------------------


class FunctionWorker(QThread):
    progress = Signal(str)
    succeeded = Signal(object)
    failed = Signal(str)

    def __init__(self, fn, *args, **kwargs):
        super().__init__()
        self.fn = fn
        self.args = args
        self.kwargs = kwargs

    def run(self) -> None:
        try:
            result = self.fn(self.progress.emit, *self.args, **self.kwargs)
            self.succeeded.emit(result)
        except Exception as exc:
            self.failed.emit(str(exc))


def _analysis_task(progress, audio_path: str, sensitivity: int) -> AnalysisResult:
    progress("Analyzing music energy and beat candidates...")
    result = analyze_music(audio_path, sensitivity)
    progress(f"Analysis complete: {len(result.beats)} beat candidates, {len(result.sections)} sections.")
    return result


def _whisper_cli_path() -> Optional[Path]:
    direct = WHISPER_DIR / "whisper-cli.exe"
    if direct.is_file():
        return direct
    if WHISPER_DIR.is_dir():
        for candidate in WHISPER_DIR.rglob("whisper-cli.exe"):
            if candidate.is_file():
                return candidate
    return None


def _whisper_cpp_ready() -> bool:
    return _whisper_cli_path() is not None and WHISPER_MODEL.is_file() and WHISPER_MODEL.stat().st_size > 100_000_000


def _download_with_resume(url: str, destination: Path, label: str, progress) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    part = destination.with_suffix(destination.suffix + ".part")
    for attempt in range(1, 4):
        try:
            existing = part.stat().st_size if part.is_file() else 0
            headers = {"User-Agent": "MiniMax-H3-Music-Clip-Creator/1.0"}
            if existing > 0:
                headers["Range"] = f"bytes={existing}-"
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=45) as response:
                code = getattr(response, "status", 200)
                append = existing > 0 and code == 206
                if not append and existing:
                    existing = 0
                total_header = response.headers.get("Content-Length")
                total = (int(total_header) + existing) if total_header and append else (int(total_header) if total_header else 0)
                mode = "ab" if append else "wb"
                done = existing
                last_report = -1
                with open(part, mode) as f:
                    while True:
                        chunk = response.read(4 * 1024 * 1024)
                        if not chunk:
                            break
                        f.write(chunk)
                        done += len(chunk)
                        if total:
                            pct = int(done * 100 / total)
                            if pct >= last_report + 5:
                                last_report = pct
                                progress(f"Downloading {label}: {pct}% ({done / (1024**2):.0f}/{total / (1024**2):.0f} MiB)")
                        elif done // (64 * 1024 * 1024) != (done - len(chunk)) // (64 * 1024 * 1024):
                            progress(f"Downloading {label}: {done / (1024**2):.0f} MiB")
            if not part.is_file() or part.stat().st_size < 1024:
                raise RuntimeError(f"Downloaded {label} is empty.")
            os.replace(str(part), str(destination))
            return destination
        except Exception as exc:
            if attempt >= 3:
                raise RuntimeError(f"Could not download {label}: {exc}") from exc
            progress(f"{label} download retry {attempt}/3...")
            time.sleep(1.0 * attempt)
    return destination


def _install_whisper_runtime(runtime_zip: Path) -> None:
    extract_dir = WHISPER_DIR / "_runtime_extract"
    if extract_dir.exists():
        shutil.rmtree(extract_dir, ignore_errors=True)
    extract_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(runtime_zip, "r") as zf:
        zf.extractall(extract_dir)
    exe = next((x for x in extract_dir.rglob("whisper-cli.exe") if x.is_file()), None)
    if exe is None:
        raise RuntimeError("Downloaded whisper.cpp runtime does not contain whisper-cli.exe")
    # Current Windows releases dynamically link the DLLs beside whisper-cli.exe.
    # Flatten that runtime directory into presets/bin/whisper so the folder is portable.
    for item in exe.parent.iterdir():
        if item.is_file():
            shutil.copy2(item, WHISPER_DIR / item.name)
    shutil.rmtree(extract_dir, ignore_errors=True)
    runtime_zip.unlink(missing_ok=True)


def _download_whisper_cpp_task(progress) -> str:
    WHISPER_DIR.mkdir(parents=True, exist_ok=True)
    if _whisper_cpp_ready():
        return str(WHISPER_DIR)
    progress("Downloading standalone multilingual Whisper.cpp Small model and runtime...")
    # Runtime and model are independent, so download them simultaneously. The .part
    # files are retained on interruption and HTTP Range is used on the next attempt.
    jobs = []
    if _whisper_cli_path() is None:
        jobs.append((WHISPER_RUNTIME_URL, WHISPER_RUNTIME_ZIP, "Whisper.cpp runtime"))
    if not WHISPER_MODEL.is_file() or WHISPER_MODEL.stat().st_size < 100_000_000:
        jobs.append((WHISPER_MODEL_URL, WHISPER_MODEL, "Whisper Small multilingual model"))
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, len(jobs))) as pool:
        futures = [pool.submit(_download_with_resume, url, dest, label, progress) for url, dest, label in jobs]
        for future in futures:
            future.result()
    if WHISPER_RUNTIME_ZIP.is_file():
        progress("Installing Whisper.cpp runtime...")
        _install_whisper_runtime(WHISPER_RUNTIME_ZIP)
    if not _whisper_cpp_ready():
        raise RuntimeError("Whisper.cpp installation validation failed. whisper-cli.exe or ggml-small.bin is missing.")
    progress("Whisper.cpp Small multilingual is ready.")
    return str(WHISPER_DIR)


def _parse_whisper_json_segments(data: Dict[str, Any]) -> List[LyricSegment]:
    lyrics: List[LyricSegment] = []
    for seg in data.get("transcription", []) if isinstance(data, dict) else []:
        if not isinstance(seg, dict):
            continue
        text = re.sub(r"\s+", " ", str(seg.get("text") or "")).strip()
        offsets = seg.get("offsets") if isinstance(seg.get("offsets"), dict) else {}
        try:
            start = float(offsets.get("from", 0)) / 1000.0
            end = float(offsets.get("to", 0)) / 1000.0
        except Exception:
            continue
        if text and end > start:
            lyrics.append(LyricSegment(start, end, text))
    return lyrics


# Whisper Small is intentionally used to keep the portable standalone install light.
# Its transcription is good enough for lyric-aware planning, but its raw segment cuts
# can occur in the middle of a sentence/lyric phrase.  The planner only needs *safe*
# phrase ends, so clean the segmentation before exposing it as MusicProject.lyrics.
_WHISPER_CONTINUATION_WORDS = {
    "a", "an", "and", "as", "at", "because", "but", "by", "for", "from", "if",
    "in", "into", "is", "it", "of", "on", "or", "so", "than", "that", "the",
    "then", "through", "to", "when", "where", "while", "with", "without", "yet",
    "i", "i'm", "i've", "i'll", "you", "you're", "we", "we're", "they", "they're",
    "my", "your", "our", "their", "this", "these", "those", "one", "two", "every",
}


def _whisper_phrase_cleanup(raw: Sequence[LyricSegment]) -> List[LyricSegment]:
    """Merge obviously broken Whisper.cpp Small segments into safer lyric phrases.

    This deliberately errs toward a slightly longer phrase: for music-video planning a
    late cut is preferable to a cut halfway through a sung phrase.  Strong punctuation
    or a real timestamp gap remains a boundary.  Very long merged phrases are capped so
    the director still receives useful edit anchors.
    """
    if not raw:
        return []

    result: List[LyricSegment] = []
    cur_start = float(raw[0].start)
    cur_end = float(raw[0].end)
    cur_text = re.sub(r"\s+", " ", raw[0].text or "").strip()

    def last_word(text: str) -> str:
        words = re.findall(r"[A-Za-zÀ-ÖØ-öø-ÿ0-9']+", text.lower())
        return words[-1] if words else ""

    def strong_end(text: str) -> bool:
        return bool(re.search(r"[.!?][\"')\]]*$", text.strip()))

    for nxt in raw[1:]:
        nxt_text = re.sub(r"\s+", " ", nxt.text or "").strip()
        if not nxt_text:
            continue
        gap = max(0.0, float(nxt.start) - cur_end)
        merged_duration = float(nxt.end) - cur_start
        current_duration = cur_end - cur_start
        tail = last_word(cur_text)

        # A noticeable pause or strong sentence punctuation is a trustworthy phrase end.
        boundary_is_safe = gap >= 0.38 or strong_end(cur_text)

        # Small's most damaging cuts are contiguous fragments such as "down the" /
        # "page ...".  Always join those.  Also join short contiguous fragments because
        # Whisper's segment boundary is not a lyric boundary in that case.
        clearly_continues = (
            gap <= 0.20 and (
                tail in _WHISPER_CONTINUATION_WORDS
                or current_duration < 3.0
                or len(cur_text.split()) < 7
            )
        )

        # When punctuation is absent (common on sung material), allow contiguous chunks
        # to grow to a normal lyric-phrase size, but do not merge half a verse forever.
        phrase_needs_more_context = (
            gap <= 0.12
            and not boundary_is_safe
            and current_duration < 5.2
            and merged_duration <= 8.5
        )

        should_merge = clearly_continues or phrase_needs_more_context
        if should_merge and merged_duration <= 9.5:
            cur_text = (cur_text + " " + nxt_text).strip()
            cur_end = float(nxt.end)
            continue

        result.append(LyricSegment(round(cur_start, 3), round(cur_end, 3), cur_text))
        cur_start = float(nxt.start)
        cur_end = float(nxt.end)
        cur_text = nxt_text

    if cur_text:
        result.append(LyricSegment(round(cur_start, 3), round(cur_end, 3), cur_text))
    return result


def _whisper_task(progress, audio_path: str) -> List[LyricSegment]:
    cli = _whisper_cli_path()
    if cli is None or not WHISPER_MODEL.is_file():
        raise RuntimeError("Standalone Whisper.cpp is not installed. Use 'Download Whisper.cpp Small' on the Song Analysis tab first.")
    ffmpeg = ffmpeg_path()
    if not ffmpeg:
        raise RuntimeError("FFmpeg was not found; it is required to prepare audio for Whisper.cpp.")
    progress("Preparing 16 kHz mono audio for Whisper.cpp...")
    payload_dir = OUTPUT_ROOT / "_temp"
    payload_dir.mkdir(parents=True, exist_ok=True)
    stamp = int(time.time() * 1000)
    wav_path = payload_dir / f"whisper_input_{stamp}.wav"
    result_prefix = payload_dir / f"whisper_result_{stamp}"
    result_json = result_prefix.with_suffix(".json")
    try:
        cp = subprocess.run(
            [ffmpeg, "-y", "-i", audio_path, "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(wav_path)],
            capture_output=True, text=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if cp.returncode != 0 or not wav_path.is_file():
            raise RuntimeError("Could not prepare audio for Whisper.cpp:\n" + (cp.stderr or cp.stdout or ""))
        threads = max(2, min(12, (os.cpu_count() or 8) // 2))
        progress(f"Transcribing with standalone Whisper.cpp Small multilingual ({threads} CPU threads, language auto-detect)...")
        cmd = [
            str(cli), "-m", str(WHISPER_MODEL), "-f", str(wav_path),
            "-l", "auto", "-t", str(threads), "-oj", "-of", str(result_prefix), "-np",
        ]
        cp = subprocess.run(
            cmd, capture_output=True, text=True,
            cwd=str(cli.parent), creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if cp.returncode != 0:
            raise RuntimeError("Whisper.cpp transcription failed:\n" + (cp.stderr or cp.stdout or ""))
        if not result_json.is_file():
            raise RuntimeError("Whisper.cpp completed but did not create its JSON result.")
        data = json.loads(result_json.read_text(encoding="utf-8-sig"))
        raw_lyrics = _parse_whisper_json_segments(data)
        lyrics = _whisper_phrase_cleanup(raw_lyrics)
        language = ""
        try:
            language = str(data.get("result", {}).get("language") or "")
        except Exception:
            pass
        suffix = f" ({language})" if language else ""
        if len(lyrics) != len(raw_lyrics):
            progress(f"Whisper.cpp complete: {len(raw_lyrics)} raw segments -> {len(lyrics)} cleaned lyric phrases{suffix}.")
        else:
            progress(f"Whisper.cpp complete: {len(lyrics)} cleaned lyric phrases{suffix}.")
        return lyrics
    finally:
        for cleanup in (wav_path, result_json):
            try:
                cleanup.unlink(missing_ok=True)
            except Exception:
                pass

def _extract_audio_slice(audio: str, out_path: Path, start: float, duration: float) -> None:
    ffmpeg = ffmpeg_path()
    if not ffmpeg:
        raise RuntimeError("FFmpeg was not found.")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        ffmpeg, "-y", "-ss", f"{max(0.0, start):.6f}", "-i", audio,
        "-t", f"{max(0.10, duration):.6f}", "-vn", "-ac", "2", "-ar", "32000",
        "-c:a", "pcm_s16le", str(out_path),
    ]
    cp = subprocess.run(cmd, capture_output=True, text=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if cp.returncode != 0 or not out_path.is_file():
        raise RuntimeError("Could not create Ref2VA audio slice:\n" + (cp.stderr or cp.stdout or ""))


_GENERATION_CANCEL = threading.Event()
_ACTIVE_GENERATION_PROCESS = None
_ACTIVE_GENERATION_LOCK = threading.Lock()


def _terminate_generation_process_tree() -> None:
    global _ACTIVE_GENERATION_PROCESS
    with _ACTIVE_GENERATION_LOCK:
        proc = _ACTIVE_GENERATION_PROCESS
    if proc is None or proc.poll() is not None:
        return
    try:
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        else:
            proc.terminate()
    except Exception:
        try:
            proc.terminate()
        except Exception:
            pass


def _generation_task(progress, project: MusicProject, shot_indices: List[int]) -> List[Dict[str, Any]]:
    global _ACTIVE_GENERATION_PROCESS
    _GENERATION_CANCEL.clear()
    if not MINIMAX_PY.is_file():
        raise RuntimeError(f"MiniMax environment Python not found. Checked: {MINIMAX_ENV / 'python.exe'} and {MINIMAX_ENV / 'Scripts' / 'python.exe'}")
    if not GENERATE_REF.is_file():
        raise RuntimeError("MiniMax Ref2VA launcher not found: helpers/generate_ref.py")
    if not Path(project.audio_path).is_file():
        raise RuntimeError("The project song file is missing.")

    out_dir = Path(project.output_dir or OUTPUT_ROOT / _safe_stem(project.audio_path)).resolve()
    raw_dir = out_dir / "raw_clips"
    audio_dir = out_dir / "audio_chunks"
    logs_dir = ROOT / "logs"
    raw_dir.mkdir(parents=True, exist_ok=True)
    audio_dir.mkdir(parents=True, exist_ok=True)
    logs_dir.mkdir(parents=True, exist_ok=True)
    width, height = RESOLUTION_PRESETS[project.resolution][project.aspect]
    refs_by_name = {r.name: r for r in project.references if r.enabled and Path(r.path).is_file()}
    results: List[Dict[str, Any]] = []
    targets = [s for s in project.shots if s.index in shot_indices]
    for pos, shot in enumerate(targets, start=1):
        if _GENERATION_CANCEL.is_set():
            progress("Generation stopped. Completed clips were kept.")
            break
        progress(f"Generating shot {shot.index} ({pos}/{len(targets)})...")
        audio_chunk = audio_dir / f"shot_{shot.index:03d}.wav"
        _extract_audio_slice(project.audio_path, audio_chunk, shot.generation_start, shot.frames / FPS)
        out_path = raw_dir / f"shot_{shot.index:03d}.mp4"
        selected_names = [n for n in shot.reference_names if n in refs_by_name]
        if not selected_names:
            # Older/restored projects can have an empty shot assignment even though valid
            # project references exist. Re-run the same conservative router at generation
            # time so a selected project character image is never silently dropped.
            selected_names = [n for n in auto_assign_references(project, shot) if n in refs_by_name]
        if not selected_names and refs_by_name:
            # Last-resort safety: Ref2VA music shots should not silently ignore all images.
            selected_names = [next(iter(refs_by_name))]
        selected = [refs_by_name[n] for n in selected_names[:9]]
        generation_prompt = build_generation_prompt(project, shot, selected)
        cmd = [
            str(MINIMAX_PY), "-u", str(GENERATE_REF),
            "--prompt", generation_prompt,
            "--width", str(width), "--height", str(height),
            "--frames", str(shot.frames), "--steps", str(project.steps),
            "--cfg", str(project.cfg), "--seed", str(shot.seed),
            "--shift", str(project.shift), "--audio-shift", str(project.audio_shift),
            "--ref-image-size", project.ref_image_size,
            "--ref-audio", str(audio_chunk),
            "--output", str(out_path),
        ]
        if project.vram_manager_enabled:
            cmd += ["--vram-manager-auto" if project.vram_auto_bypass else "--vram-manager"]
            cmd += [
                "--vram-residency-engine", str(project.vram_residency_engine or "static"),
                "--vram-runtime-free-gb", str(float(project.vram_runtime_free_gb)),
                "--vram-text-headroom-gb", str(float(project.vram_text_headroom_gb)),
                "--vram-diffusion-headroom-gb", str(float(project.vram_diffusion_headroom_gb)),
                "--vram-offload-chunk-mb", str(int(project.vram_offload_chunk_mb)),
                "--vram-max-resident-weights-gb", str(float(project.vram_max_resident_weights_gb)),
                "--vram-block-check-interval", str(int(project.vram_block_check_interval)),
                "--vram-async-streams", str(int(project.vram_async_streams)),
                "--vram-video-vae-reserve-gb", str(float(project.vram_video_vae_reserve_gb)),
                "--vram-audio-vae-reserve-gb", str(float(project.vram_audio_vae_reserve_gb)),
                "--vram-residency-target-free-gb", str(float(project.vram_residency_target_free_gb)),
                "--vram-residency-warmup-blocks", str(int(project.vram_residency_warmup_blocks)),
                "--vram-residency-refill-interval", str(int(project.vram_residency_refill_interval)),
            ]
            cmd += ["--vram-residency-fill" if project.vram_residency_fill else "--no-vram-residency-fill"]
        if project.sage_attention:
            cmd += ["--sage-attention"]
        if project.spectrum:
            cmd += ["--spectrum"]
        turbo_lora = str(project.turbo_lora_path or "").strip()
        if turbo_lora:
            turbo_path = Path(turbo_lora)
            if not turbo_path.is_file():
                raise RuntimeError(f"Turbo LoRA not found: {turbo_lora}")
            cmd += ["--lora", str(turbo_path.resolve()), "--lora-strength", str(float(project.turbo_lora_strength))]
        for ref in selected:
            cmd += ["--ref-image", ref.path]
        # Ref2VA only requires at least one reference. The song chunk already fills that requirement.
        stamp = time.strftime("%Y%m%d_%H%M%S")
        log_path = logs_dir / f"minimax_music_clip_{stamp}_shot{shot.index:03d}.log"
        cp = subprocess.Popen(
            cmd,
            cwd=str(ROOT),
            # Match the already-working MiniMax GUI launch behavior: use the
            # dedicated MiniMax Python executable, but inherit the normal process
            # environment unchanged. Do not sanitize PYTHONPATH/user-site/PATH here;
            # the standalone MiniMax GUI does not do that and its Ref2VA runtime is
            # already proven on this install.
            env=os.environ.copy(),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        with _ACTIVE_GENERATION_LOCK:
            _ACTIVE_GENERATION_PROCESS = cp
        tail: List[str] = []
        assert cp.stdout is not None
        with log_path.open("w", encoding="utf-8", errors="replace") as log:
            log.write("MiniMax Music Clip Creator - Ref2VA shot log\n")
            log.write(f"Shot: {shot.index}\n")
            log.write(f"MiniMax Python: {MINIMAX_PY}\n")
            log.write(f"Working directory: {ROOT}\n")
            log.write(f"Audio chunk: {audio_chunk}\n")
            log.write(f"Output: {out_path}\n")
            log.write("Command: " + subprocess.list2cmdline(cmd) + "\n\n")
            log.flush()
            for line in cp.stdout:
                if _GENERATION_CANCEL.is_set() and cp.poll() is None:
                    _terminate_generation_process_tree()
                log.write(line)
                log.flush()
                text = line.rstrip()
                if text:
                    tail.append(text)
                    tail = tail[-40:]
                    if "step" in text.lower() or "saved" in text.lower() or "error" in text.lower():
                        progress(f"Shot {shot.index}: {text}")
        rc = cp.wait()
        with _ACTIVE_GENERATION_LOCK:
            if _ACTIVE_GENERATION_PROCESS is cp:
                _ACTIVE_GENERATION_PROCESS = None
        if _GENERATION_CANCEL.is_set():
            if out_path.is_file():
                results.append({"index": shot.index, "ok": True, "output_path": str(out_path), "log_path": str(log_path)})
                progress(f"__MINIMAX_SHOT_DONE__|{shot.index}|{out_path}")
            else:
                results.append({"index": shot.index, "ok": False, "cancelled": True, "message": "Cancelled", "log_path": str(log_path)})
            progress("Generation stopped. Completed clips were kept.")
            break
        if rc != 0 or not out_path.is_file():
            results.append({
                "index": shot.index,
                "ok": False,
                "message": ("\n".join(tail) or f"Exit code {rc}") + f"\n\nLog: {log_path}",
                "log_path": str(log_path),
            })
            progress(f"__MINIMAX_SHOT_FAILED__|{shot.index}|{log_path}")
            continue
        results.append({"index": shot.index, "ok": True, "output_path": str(out_path), "log_path": str(log_path)})
        progress(f"__MINIMAX_SHOT_DONE__|{shot.index}|{out_path}")
    return results


def _assembly_task(progress, project: MusicProject) -> str:
    ffmpeg = ffmpeg_path()
    if not ffmpeg:
        raise RuntimeError("FFmpeg was not found.")
    if not project.shots:
        raise RuntimeError("No shot plan exists.")
    missing = [s.index for s in project.shots if not s.output_path or not Path(s.output_path).is_file()]
    if missing:
        raise RuntimeError("Missing generated clips for shots: " + ", ".join(map(str, missing)))

    out_dir = Path(project.output_dir or OUTPUT_ROOT / _safe_stem(project.audio_path)).resolve()
    temp_dir = out_dir / "_assembly"
    temp_dir.mkdir(parents=True, exist_ok=True)
    trimmed: List[Path] = []
    for i, shot in enumerate(project.shots, start=1):
        progress(f"Trimming shot {shot.index} ({i}/{len(project.shots)})...")
        target = temp_dir / f"trim_{shot.index:03d}.mp4"
        # tpad makes tiny source-duration mismatches non-fatal; trim then enforces the edit slot.
        vf = (
            f"tpad=stop_mode=clone:stop_duration=1.0,"
            f"trim=start={shot.trim_in:.6f}:duration={shot.edit_duration:.6f},"
            "setpts=PTS-STARTPTS"
        )
        cmd = [
            ffmpeg, "-y", "-i", shot.output_path, "-an", "-vf", vf,
            "-c:v", "libx264", "-crf", "18", "-pix_fmt", "yuv420p", str(target),
        ]
        cp = subprocess.run(cmd, capture_output=True, text=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if cp.returncode != 0 or not target.is_file():
            raise RuntimeError(f"Could not trim shot {shot.index}:\n" + (cp.stderr or cp.stdout or ""))
        trimmed.append(target)

    concat_file = temp_dir / "concat.txt"
    concat_file.write_text("\n".join("file '" + str(p).replace("'", "'\\''") + "'" for p in trimmed), encoding="utf-8")
    video_only = temp_dir / "video_only.mp4"
    progress("Concatenating trimmed shots...")
    cp = subprocess.run(
        [ffmpeg, "-y", "-f", "concat", "-safe", "0", "-i", str(concat_file), "-c", "copy", str(video_only)],
        capture_output=True,
        text=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if cp.returncode != 0:
        # Codec-copy concat can fail with odd source headers. Re-encode fallback.
        cp = subprocess.run(
            [ffmpeg, "-y", "-f", "concat", "-safe", "0", "-i", str(concat_file), "-an", "-c:v", "libx264", "-crf", "18", "-pix_fmt", "yuv420p", str(video_only)],
            capture_output=True,
            text=True,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    if cp.returncode != 0 or not video_only.is_file():
        raise RuntimeError("Could not concatenate clips:\n" + (cp.stderr or cp.stdout or ""))

    final = out_dir / f"{_safe_stem(project.title or project.audio_path)}_minimax_music_video.mp4"
    song_duration = project.analysis.duration or probe_duration(project.audio_path)
    progress("Muxing the original master song...")
    cmd = [
        ffmpeg, "-y", "-i", str(video_only), "-i", project.audio_path,
        "-map", "0:v:0", "-map", "1:a:0", "-c:v", "copy", "-c:a", "aac", "-b:a", "256k",
    ]
    if song_duration > 0:
        cmd += ["-t", f"{song_duration:.6f}"]
    cmd += [str(final)]
    cp = subprocess.run(cmd, capture_output=True, text=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if cp.returncode != 0 or not final.is_file():
        raise RuntimeError("Final mux failed:\n" + (cp.stderr or cp.stdout or ""))
    progress(f"Saved final music video: {final}")
    return str(final)


# ---------------------------------- GUI ------------------------------------


class MiniMaxMusicClipWidget(QWidget):
    """Embeddable MiniMax Music Clip Creator widget."""

    def __init__(self, parent: Optional[QWidget] = None, queue_adapter=None):
        super().__init__(parent)
        self.queue_adapter = queue_adapter
        self.project = MusicProject(output_dir=str(OUTPUT_ROOT))
        self.project_path = ""
        self.worker: Optional[FunctionWorker] = None
        self._autosave_last_text = ""
        self._build_ui()
        # Restore the complete working session first. The older small settings file
        # remains only as a fallback for installs that do not have a session save yet.
        if not self._load_autosave():
            self._load_settings()
        self._sync_ui_from_project()
        self._start_autosave()

    # ---- UI construction ----
    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(8, 8, 8, 8)
        outer.setSpacing(6)
        self.tabs = QTabWidget(self)
        outer.addWidget(self.tabs, 1)

        self.page_project = QWidget(self)
        self.page_refs = QWidget(self)
        self.page_analysis = QWidget(self)
        self.page_director = QWidget(self)
        self.page_generate = QWidget(self)
        self.page_settings = QWidget(self)
        for page, title in (
            (self.page_project, "Project"),
            (self.page_refs, "References"),
            (self.page_analysis, "Song Analysis"),
            (self.page_director, "Director / Shot List"),
            (self.page_generate, "Generate & Review"),
            (self.page_settings, "Settings"),
        ):
            self.tabs.addTab(page, title)

        self._build_project_tab()
        self._build_refs_tab()
        self._build_analysis_tab()
        self._build_director_tab()
        self._build_generate_tab()
        self._build_settings_tab()

        footer = QHBoxLayout()
        self.progress = QProgressBar(self)
        self.progress.setRange(0, 1)
        self.progress.setValue(0)
        self.status = QLabel("Ready.", self)
        self.status.setTextInteractionFlags(Qt.TextSelectableByMouse)
        footer.addWidget(self.progress, 1)
        footer.addWidget(self.status, 2)
        outer.addLayout(footer)

    def _build_project_tab(self) -> None:
        lay = QVBoxLayout(self.page_project)
        audio_box = QGroupBox("Song", self.page_project)
        form = QFormLayout(audio_box)
        row = QHBoxLayout()
        self.edit_audio = QLineEdit(audio_box)
        self.btn_audio = QPushButton("Browse...", audio_box)
        row.addWidget(self.edit_audio, 1); row.addWidget(self.btn_audio)
        form.addRow("Master song:", row)
        self.label_duration = QLabel("Duration: not analyzed", audio_box)
        form.addRow("", self.label_duration)
        row2 = QHBoxLayout()
        self.edit_output = QLineEdit(audio_box)
        self.btn_output = QPushButton("Browse...", audio_box)
        row2.addWidget(self.edit_output, 1); row2.addWidget(self.btn_output)
        form.addRow("Project output:", row2)
        self.edit_title = QLineEdit(audio_box)
        form.addRow("Project title:", self.edit_title)
        lay.addWidget(audio_box)

        brief = QGroupBox("Creative brief", self.page_project)
        bf = QFormLayout(brief)
        self.edit_idea = QPlainTextEdit(brief); self.edit_idea.setMaximumHeight(90)
        self.edit_style = QLineEdit(brief)
        self.edit_subjects = QPlainTextEdit(brief); self.edit_subjects.setMaximumHeight(90)
        self.edit_world = QLineEdit(brief)
        self.edit_camera = QLineEdit(brief)
        self.edit_subjects.setToolTip("Optional continuity details for named reference images. Example: Erica is the blue-haired woman in her reference, keeps that outfit, and is always the drummer when the drum kit is present. Leave blank when MiniMax may invent role, outfit or props.")
        bf.addRow("Main idea / story:", self.edit_idea)
        bf.addRow("Style / theme:", self.edit_style)
        bf.addRow("Ref image purpose details:", self.edit_subjects)
        bf.addRow("Locations / world:", self.edit_world)
        bf.addRow("Camera choreography:", self.edit_camera)
        lay.addWidget(brief)

        actions = QHBoxLayout()
        self.btn_new = QPushButton("New project", self.page_project)
        self.btn_open = QPushButton("Open project...", self.page_project)
        self.btn_save = QPushButton("Save project", self.page_project)
        self.btn_save_as = QPushButton("Save project as...", self.page_project)
        actions.addWidget(self.btn_new); actions.addWidget(self.btn_open); actions.addWidget(self.btn_save); actions.addWidget(self.btn_save_as); actions.addStretch(1)
        lay.addStretch(1)
        lay.addLayout(actions)

        self.btn_audio.clicked.connect(self._browse_audio)
        self.btn_output.clicked.connect(self._browse_output)
        self.btn_new.clicked.connect(self._new_project)
        self.btn_open.clicked.connect(self._open_project)
        self.btn_save.clicked.connect(self._save_project)
        self.btn_save_as.clicked.connect(lambda: self._save_project(force_as=True))

    def _build_refs_tab(self) -> None:
        lay = QVBoxLayout(self.page_refs)
        info = QLabel(
            "Project references are sent directly to MiniMax Ref2VA. Choose what each image is for: Character, Background / Location, Object / Prop, Style / Mood, or Picture / Composition anchor. "
            "MiniMax then receives the correct <Subject N> or <Picture N> relationship automatically. Use Image purpose details only for consistency rules the model cannot infer by itself. "
            "Each shot can use a different subset; up to 9 images can be passed to one Ref2VA job.",
            self.page_refs,
        )
        info.setWordWrap(True); lay.addWidget(info)
        self.refs_table = QTableWidget(0, 6, self.page_refs)
        self.refs_table.setHorizontalHeaderLabels(["Use", "Preview", "Name", "Reference role", "Path", "Image description / purpose"])
        self.refs_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.refs_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.refs_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.refs_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeToContents)
        self.refs_table.horizontalHeader().setSectionResizeMode(4, QHeaderView.Stretch)
        self.refs_table.horizontalHeader().setSectionResizeMode(5, QHeaderView.Stretch)
        self.refs_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        lay.addWidget(self.refs_table, 1)
        row = QHBoxLayout()
        self.btn_add_ref = QPushButton("Add reference image...", self.page_refs)
        self.btn_remove_ref = QPushButton("Remove selected", self.page_refs)
        row.addWidget(self.btn_add_ref); row.addWidget(self.btn_remove_ref); row.addStretch(1)
        lay.addLayout(row)
        self.btn_add_ref.clicked.connect(self._add_reference)
        self.btn_remove_ref.clicked.connect(self._remove_reference)

    def _build_analysis_tab(self) -> None:
        lay = QVBoxLayout(self.page_analysis)
        box = QGroupBox("Analysis", self.page_analysis)
        form = QFormLayout(box)
        self.spin_sensitivity = QSpinBox(box); self.spin_sensitivity.setRange(0, 20); self.spin_sensitivity.setValue(10)
        self.spin_sensitivity.setToolTip("Beat detector sensitivity. 0 = strict/fewer peaks, 10 = normal, 20 = loose/more peaks.")
        form.addRow("Beat sensitivity:", self.spin_sensitivity)
        self.check_whisper_timing = QCheckBox("Use Whisper lyrics for shot timing", box)
        self.check_whisper_timing.setChecked(True)
        self.check_whisper_timing.setToolTip(
            "On: Whisper phrase endings are the preferred shot boundaries when they can be covered by a valid MiniMax H3 frame count. "
            "Beat/energy analysis becomes supporting information. Off: use the normal beat/energy duration planner. If no lyrics are loaded, normal planning is used automatically."
        )
        form.addRow("Lyric-aware planning:", self.check_whisper_timing)
        lay.addWidget(box)
        row = QHBoxLayout()
        self.btn_analyze = QPushButton("Analyze track", self.page_analysis)
        self.btn_whisper = QPushButton("Transcribe lyrics with Whisper.cpp", self.page_analysis)
        self.btn_download_whisper = QPushButton("Download Whisper.cpp Small", self.page_analysis)
        self.btn_download_whisper.setToolTip(
            "Downloads the standalone multilingual Whisper.cpp Small model (~488 MiB) plus the small Windows runtime into presets/bin/whisper. "
            "It is independent of FrameVision's old Whisper environment and can be reused when this creator is added to the standalone MiniMax app."
        )
        self.btn_clear_lyrics = QPushButton("Treat as instrumental", self.page_analysis)
        row.addWidget(self.btn_analyze); row.addWidget(self.btn_whisper); row.addWidget(self.btn_download_whisper); row.addWidget(self.btn_clear_lyrics); row.addStretch(1)
        self.analysis_summary = QLabel("No analysis yet.", self.page_analysis); self.analysis_summary.setWordWrap(True); lay.addWidget(self.analysis_summary)
        self.lyrics_table = QTableWidget(0, 3, self.page_analysis)
        self.lyrics_table.setHorizontalHeaderLabels(["Start", "End", "Whisper phrase"])
        self.lyrics_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.lyrics_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.lyrics_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        lay.addWidget(self.lyrics_table, 1)
        lay.addLayout(row)
        self.btn_analyze.clicked.connect(self._start_analysis)
        self.btn_whisper.clicked.connect(self._start_whisper)
        self.btn_download_whisper.clicked.connect(self._download_whisper_cpp)
        self.btn_clear_lyrics.clicked.connect(self._clear_lyrics)
        self._update_whisper_buttons()

    def _build_director_tab(self) -> None:
        lay = QVBoxLayout(self.page_director)
        top = QHBoxLayout()
        self.btn_plan = QPushButton("Create / rebuild shot plan", self.page_director)
        self.btn_refresh_prompts = QPushButton("Rebuild H3 Ref2VA prompts", self.page_director)
        top.addWidget(self.btn_plan); top.addWidget(self.btn_refresh_prompts); top.addStretch(1)
        note = QLabel(
            "Prompts are built in MiniMax-H3 full-reference format: subject definitions, reference retention, shot-by-shot description and explicit audio roles. "
            "Lyric/section boundaries that fall inside a generated clip become timestamped H3 shots while the supplied audio continues across the cut.",
            self.page_director,
        ); note.setWordWrap(True); lay.addWidget(note)
        self.shot_table = QTableWidget(0, 10, self.page_director)
        self.shot_table.setHorizontalHeaderLabels([
            "#", "Song range", "Edit", "MiniMax frames", "Gen", "Section", "Lyrics", "Internal cuts", "References", "Prompt"
        ])
        for col in (0, 2, 3, 4): self.shot_table.horizontalHeader().setSectionResizeMode(col, QHeaderView.ResizeToContents)
        for col in (1, 5, 6, 7, 8, 9): self.shot_table.horizontalHeader().setSectionResizeMode(col, QHeaderView.Stretch)
        self.shot_table.setWordWrap(True)
        self.shot_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.shot_table.verticalHeader().setDefaultSectionSize(72)
        self.shot_table.verticalHeader().setMinimumSectionSize(56)
        lay.addWidget(self.shot_table, 1)

        prompt_box = QGroupBox("Selected shot H3 prompt", self.page_director)
        prompt_lay = QVBoxLayout(prompt_box)
        self.shot_prompt_editor = QPlainTextEdit(prompt_box)
        self.shot_prompt_editor.setPlaceholderText("Select a shot above to view or edit its complete MiniMax-H3 Ref2VA prompt.")
        self.shot_prompt_editor.setMinimumHeight(150)
        self.shot_prompt_editor.setMaximumHeight(240)
        self.shot_prompt_editor.setLineWrapMode(QPlainTextEdit.WidgetWidth)
        prompt_lay.addWidget(self.shot_prompt_editor)
        lay.addWidget(prompt_box)

        lay.addLayout(top)
        self.btn_plan.clicked.connect(self._create_plan)
        self.btn_refresh_prompts.clicked.connect(self._rebuild_prompts)
        self.shot_table.itemChanged.connect(self._shot_table_item_changed)
        self.shot_table.itemSelectionChanged.connect(self._show_selected_shot_prompt)
        self.shot_prompt_editor.textChanged.connect(self._selected_shot_prompt_changed)

    def _build_generate_tab(self) -> None:
        lay = QVBoxLayout(self.page_generate)
        row = QHBoxLayout()
        self.btn_generate_selected = QPushButton("Generate selected shot", self.page_generate)
        self.btn_generate_all = QPushButton("Generate all missing shots", self.page_generate)
        self.btn_stop_generation = QPushButton("Stop generation", self.page_generate)
        self.btn_stop_generation.setEnabled(False)
        self.btn_stop_generation.setToolTip("Stops the current direct MiniMax clip. When embedded in the standalone app, use the main Queue tab to cancel/requeue jobs.")
        if callable(getattr(self, "queue_adapter", None)):
            self.btn_stop_generation.setVisible(False)
        self.btn_assemble = QPushButton("Assemble final music video", self.page_generate)
        self.btn_open_output = QPushButton("Open output folder", self.page_generate)
        row.addWidget(self.btn_generate_selected); row.addWidget(self.btn_generate_all); row.addWidget(self.btn_stop_generation); row.addWidget(self.btn_assemble); row.addWidget(self.btn_open_output); row.addStretch(1)
        self.label_job_seed = QLabel("Job seed: auto (first generation locks one random seed for the whole job). Edit a shot's Seed value below to override it for retries.", self.page_generate)
        self.label_job_seed.setWordWrap(True)
        lay.addWidget(self.label_job_seed)
        self.review_table = QTableWidget(0, 6, self.page_generate)
        self.review_table.setHorizontalHeaderLabels(["#", "Status", "Frames", "Edit range", "Output", "Seed"])
        self.review_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.review_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.review_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.review_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeToContents)
        self.review_table.horizontalHeader().setSectionResizeMode(4, QHeaderView.Stretch)
        self.review_table.horizontalHeader().setSectionResizeMode(5, QHeaderView.ResizeToContents)
        self.review_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        lay.addWidget(self.review_table, 1)

        preview_box = QGroupBox("Selected clip preview", self.page_generate)
        preview_lay = QVBoxLayout(preview_box)
        self.review_preview = QLabel("Select a generated shot to preview it.", preview_box)
        self.review_preview.setAlignment(Qt.AlignCenter)
        self.review_preview.setMinimumHeight(180)
        self.review_preview.setMaximumHeight(280)
        self.review_preview.setStyleSheet("QLabel { border: 1px solid palette(mid); background: palette(base); }")
        preview_lay.addWidget(self.review_preview, 1)
        preview_actions = QHBoxLayout()
        self.btn_play_clip = QPushButton("Play selected clip", preview_box)
        self.btn_open_clip_folder = QPushButton("Open clip folder", preview_box)
        preview_actions.addWidget(self.btn_play_clip); preview_actions.addWidget(self.btn_open_clip_folder); preview_actions.addStretch(1)
        preview_lay.addLayout(preview_actions)
        lay.addWidget(preview_box)
        lay.addLayout(row)

        self.btn_generate_selected.clicked.connect(self._generate_selected)
        self.btn_generate_all.clicked.connect(self._generate_all)
        self.btn_stop_generation.clicked.connect(self._stop_generation)
        self.btn_assemble.clicked.connect(self._assemble)
        self.btn_open_output.clicked.connect(self._open_output_folder)
        self.btn_play_clip.clicked.connect(self._play_selected_clip)
        self.btn_open_clip_folder.clicked.connect(self._open_selected_clip_folder)
        self.review_table.itemSelectionChanged.connect(self._show_selected_clip_preview)
        self.review_table.itemChanged.connect(self._review_table_item_changed)
        self.review_table.cellDoubleClicked.connect(lambda _r, _c: self._play_selected_clip())

    def _build_settings_tab(self) -> None:
        lay = QVBoxLayout(self.page_settings)
        gen = QGroupBox("MiniMax generation", self.page_settings)
        form = QFormLayout(gen)
        self.combo_resolution = QComboBox(gen); self.combo_resolution.addItems(list(RESOLUTION_PRESETS.keys())); self.combo_resolution.setCurrentText("832 × 480")
        self.combo_aspect = QComboBox(gen); self.combo_aspect.addItems(["16:9", "9:16", "1:1"])
        form.addRow("Resolution:", self.combo_resolution); form.addRow("Aspect ratio:", self.combo_aspect)

        max_row = QHBoxLayout()
        self.slider_frames = QSlider(Qt.Horizontal, gen); self.slider_frames.setRange(0, len(MUSIC_FRAME_GRID) - 1); self.slider_frames.setValue(len(MUSIC_FRAME_GRID) - 1)
        self.label_frames = QLabel(gen)
        max_row.addWidget(self.slider_frames, 1); max_row.addWidget(self.label_frames)
        form.addRow("Maximum generated shot length:", max_row)
        self.slider_frames.setToolTip(
            "Sets the maximum MiniMax Ref2VA generation length used by the planner. The director may use shorter valid frame counts. "
            "Generated clips can include extra material at the beginning/end and are trimmed during final assembly."
        )
        self.spin_head = QDoubleSpinBox(gen); self.spin_head.setRange(0.0, 2.0); self.spin_head.setSingleStep(0.05); self.spin_head.setValue(0.35); self.spin_head.setSuffix(" s")
        self.spin_tail = QDoubleSpinBox(gen); self.spin_tail.setRange(0.0, 2.0); self.spin_tail.setSingleStep(0.05); self.spin_tail.setValue(0.45); self.spin_tail.setSuffix(" s")
        self.spin_snap = QDoubleSpinBox(gen); self.spin_snap.setRange(0.0, 3.0); self.spin_snap.setSingleStep(0.05); self.spin_snap.setValue(1.25); self.spin_snap.setSuffix(" s")
        form.addRow("Extra context before edit:", self.spin_head)
        form.addRow("Extra context after edit:", self.spin_tail)
        form.addRow("Phrase-boundary snap tolerance:", self.spin_snap)
        self.spin_steps = QSpinBox(gen); self.spin_steps.setRange(1, 100); self.spin_steps.setValue(15)
        self.spin_cfg = QDoubleSpinBox(gen); self.spin_cfg.setRange(0.0, 20.0); self.spin_cfg.setValue(1.0); self.spin_cfg.setDecimals(2)
        self.spin_shift = QDoubleSpinBox(gen); self.spin_shift.setRange(0.0, 30.0); self.spin_shift.setValue(12.0); self.spin_shift.setDecimals(2)
        self.spin_audio_shift = QDoubleSpinBox(gen); self.spin_audio_shift.setRange(0.0, 20.0); self.spin_audio_shift.setValue(3.0); self.spin_audio_shift.setDecimals(2)
        form.addRow("Steps:", self.spin_steps); form.addRow("CFG:", self.spin_cfg); form.addRow("Shift:", self.spin_shift); form.addRow("Audio shift:", self.spin_audio_shift)
        self.combo_ref_size = QComboBox(gen); self.combo_ref_size.addItems(["match", "max"]); form.addRow("Reference image size:", self.combo_ref_size)
        lora_row = QHBoxLayout()
        self.edit_turbo_lora = QLineEdit(gen)
        self.edit_turbo_lora.setPlaceholderText("Optional speed / Turbo LoRA (.safetensors)")
        self.btn_turbo_lora = QPushButton("Browse...", gen)
        self.spin_turbo_lora = QDoubleSpinBox(gen); self.spin_turbo_lora.setRange(-4.0, 4.0); self.spin_turbo_lora.setSingleStep(0.05); self.spin_turbo_lora.setDecimals(2); self.spin_turbo_lora.setValue(1.0)
        self.spin_turbo_lora.setToolTip("Strength for the selected MiniMax LoRA. 1.0 = normal strength.")
        lora_row.addWidget(self.edit_turbo_lora, 1); lora_row.addWidget(self.btn_turbo_lora); lora_row.addWidget(QLabel("Strength:", gen)); lora_row.addWidget(self.spin_turbo_lora)
        form.addRow("Turbo / speed LoRA:", lora_row)
        self.btn_turbo_lora.clicked.connect(self._browse_turbo_lora)
        self.check_vram_manager = QCheckBox("Enable VRAM Manager protection", gen); self.check_vram_manager.setChecked(True)
        self.check_vram_manager.setToolTip("Master switch. Off = never use VRAM Manager. On = use the setting below to choose automatic bypass or always-on protection.")
        self.check_vram_auto_bypass = QCheckBox("Automatic bypass when job fits", gen); self.check_vram_auto_bypass.setChecked(True)
        self.check_vram_auto_bypass.setToolTip("On = MiniMax decides per stage/job whether native loading is safe. Off = VRAM Manager stays active for every job.")
        self.check_sage = QCheckBox("SageAttention", gen)
        self.check_spectrum = QCheckBox("Spectrum", gen)
        flags = QHBoxLayout(); flags.addWidget(self.check_vram_manager); flags.addWidget(self.check_vram_auto_bypass); flags.addWidget(self.check_sage); flags.addWidget(self.check_spectrum); flags.addStretch(1)
        form.addRow("Acceleration / VRAM:", flags)
        lay.addWidget(gen)
        explanation = QLabel(
            "Timing rule: the song timeline is authoritative. MiniMax valid frame counts determine how much source footage is generated, "
            "then FFmpeg trims each source clip to its exact edit slot. Small timing mismatches are repaired during assembly instead of aborting.",
            self.page_settings,
        ); explanation.setWordWrap(True); lay.addWidget(explanation); lay.addStretch(1)
        self.slider_frames.valueChanged.connect(self._update_frame_label)
        self._update_frame_label()

    # ---- state conversion ----
    def _pull_ui(self) -> None:
        self.project.audio_path = self.edit_audio.text().strip()
        self.project.output_dir = self.edit_output.text().strip() or str(OUTPUT_ROOT)
        self.project.title = self.edit_title.text().strip()
        self.project.main_idea = self.edit_idea.toPlainText().strip()
        self.project.style_theme = self.edit_style.text().strip()
        self.project.characters_subjects = self.edit_subjects.toPlainText().strip()
        self.project.locations_world = self.edit_world.text().strip()
        self.project.camera_choreography = self.edit_camera.text().strip()
        self.project.beat_sensitivity = self.spin_sensitivity.value()
        self.project.whisper_timing_enabled = self.check_whisper_timing.isChecked()
        self.project.resolution = self.combo_resolution.currentText()
        self.project.aspect = self.combo_aspect.currentText()
        self.project.max_frames = MUSIC_FRAME_GRID[self.slider_frames.value()]
        self.project.head_padding = self.spin_head.value()
        self.project.tail_padding = self.spin_tail.value()
        self.project.phrase_snap_tolerance = self.spin_snap.value()
        self.project.steps = self.spin_steps.value()
        self.project.cfg = self.spin_cfg.value()
        self.project.shift = self.spin_shift.value()
        self.project.audio_shift = self.spin_audio_shift.value()
        self.project.ref_image_size = self.combo_ref_size.currentText()
        self.project.turbo_lora_path = self.edit_turbo_lora.text().strip()
        self.project.turbo_lora_strength = self.spin_turbo_lora.value()
        self.project.vram_manager_enabled = self.check_vram_manager.isChecked()
        self.project.vram_auto_bypass = self.check_vram_auto_bypass.isChecked()
        self.project.sage_attention = self.check_sage.isChecked()
        self.project.spectrum = self.check_spectrum.isChecked()
        self.project.references = self._refs_from_table()

    def _sync_ui_from_project(self) -> None:
        p = self.project
        self.edit_audio.setText(p.audio_path); self.edit_output.setText(p.output_dir or str(OUTPUT_ROOT)); self.edit_title.setText(p.title)
        self.edit_idea.setPlainText(p.main_idea); self.edit_style.setText(p.style_theme); self.edit_subjects.setPlainText(p.characters_subjects); self.edit_world.setText(p.locations_world); self.edit_camera.setText(p.camera_choreography)
        self.spin_sensitivity.setValue(p.beat_sensitivity)
        self.check_whisper_timing.setChecked(bool(getattr(p, "whisper_timing_enabled", True)))
        self.combo_resolution.setCurrentText(p.resolution if p.resolution in RESOLUTION_PRESETS else "832 × 480")
        self.combo_aspect.setCurrentText(p.aspect if p.aspect in ("16:9", "9:16", "1:1") else "16:9")
        nearest_idx = min(range(len(MUSIC_FRAME_GRID)), key=lambda i: abs(MUSIC_FRAME_GRID[i] - int(p.max_frames or MUSIC_FRAME_DEFAULT_MAX)))
        self.slider_frames.setValue(nearest_idx)
        self.spin_head.setValue(p.head_padding); self.spin_tail.setValue(p.tail_padding); self.spin_snap.setValue(p.phrase_snap_tolerance)
        self.spin_steps.setValue(p.steps); self.spin_cfg.setValue(p.cfg); self.spin_shift.setValue(p.shift); self.spin_audio_shift.setValue(p.audio_shift)
        self.combo_ref_size.setCurrentText(p.ref_image_size if p.ref_image_size in ("match", "max") else "match")
        self.edit_turbo_lora.setText(p.turbo_lora_path or "")
        self.spin_turbo_lora.setValue(float(p.turbo_lora_strength or 1.0))
        self.check_vram_manager.setChecked(bool(p.vram_manager_enabled)); self.check_vram_auto_bypass.setChecked(bool(p.vram_auto_bypass)); self.check_sage.setChecked(p.sage_attention); self.check_spectrum.setChecked(p.spectrum)
        self._populate_refs(); self._populate_analysis(); self._populate_shots(); self._populate_review(); self._update_frame_label()

    def _project_dict(self) -> Dict[str, Any]:
        self._pull_ui()
        return asdict(self.project)

    @staticmethod
    def _project_from_dict(data: Dict[str, Any]) -> MusicProject:
        p = MusicProject()
        simple_fields = [k for k in p.__dataclass_fields__ if k not in ("references", "lyrics", "analysis", "shots")]
        for key in simple_fields:
            if key in data:
                setattr(p, key, data[key])
        p.references = [ReferenceAsset(**x) for x in data.get("references", []) if isinstance(x, dict)]
        p.lyrics = [LyricSegment(**x) for x in data.get("lyrics", []) if isinstance(x, dict)]
        a = data.get("analysis") if isinstance(data.get("analysis"), dict) else {}
        p.analysis = AnalysisResult(
            duration=float(a.get("duration", 0.0) or 0.0),
            beats=[Beat(**x) for x in a.get("beats", []) if isinstance(x, dict)],
            sections=[Section(**x) for x in a.get("sections", []) if isinstance(x, dict)],
        )
        p.shots = [MusicShot(**x) for x in data.get("shots", []) if isinstance(x, dict)]
        return p

    # ---- project file operations ----
    def _new_project(self) -> None:
        # Keep user generation preferences when starting a new project. Project-specific
        # material (song, creative brief, references, analysis and shots) is cleared.
        self._pull_ui()
        old = self.project
        self.project = MusicProject(output_dir=str(OUTPUT_ROOT))
        for name in (
            "resolution", "aspect", "max_frames", "head_padding", "tail_padding",
            "phrase_snap_tolerance", "steps", "cfg", "shift", "audio_shift",
            "ref_image_size", "turbo_lora_path", "turbo_lora_strength",
            "vram_manager_enabled", "vram_auto_bypass", "vram_residency_engine",
            "vram_runtime_free_gb", "vram_text_headroom_gb", "vram_diffusion_headroom_gb",
            "vram_offload_chunk_mb", "vram_max_resident_weights_gb", "vram_block_check_interval",
            "vram_async_streams", "vram_video_vae_reserve_gb", "vram_audio_vae_reserve_gb",
            "vram_residency_fill", "vram_residency_target_free_gb", "vram_residency_warmup_blocks",
            "vram_residency_refill_interval", "sage_attention", "spectrum", "beat_sensitivity", "whisper_timing_enabled",
        ):
            setattr(self.project, name, getattr(old, name))
        self.project_path = ""
        self._sync_ui_from_project()
        self.status.setText("New project.")
        self._save_settings()
        self._write_autosave(force=True)

    def _browse_audio(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Select master song", "", "Audio (*.wav *.mp3 *.flac *.m4a *.aac *.ogg);;All files (*.*)")
        if path:
            self.edit_audio.setText(path)
            if not self.edit_title.text().strip(): self.edit_title.setText(Path(path).stem)
            if self.edit_output.text().strip() in ("", str(OUTPUT_ROOT)):
                self.edit_output.setText(str(OUTPUT_ROOT / _safe_stem(path)))
            dur = probe_duration(path)
            self.label_duration.setText(f"Duration: {_fmt_time(dur)} ({dur:.2f} s)" if dur else "Duration: unknown")

    def _browse_output(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "Select project output folder", self.edit_output.text().strip() or str(OUTPUT_ROOT))
        if path: self.edit_output.setText(path)

    def _save_project(self, force_as: bool = False) -> None:
        if force_as or not self.project_path:
            default_dir = Path(self.edit_output.text().strip() or OUTPUT_ROOT); default_dir.mkdir(parents=True, exist_ok=True)
            path, _ = QFileDialog.getSaveFileName(self, "Save MiniMax music project", str(default_dir / "minimax_music_project.json"), "JSON (*.json)")
            if not path: return
            self.project_path = path
        Path(self.project_path).parent.mkdir(parents=True, exist_ok=True)
        Path(self.project_path).write_text(json.dumps(self._project_dict(), indent=2, ensure_ascii=False), encoding="utf-8")
        self.status.setText(f"Saved project: {self.project_path}")
        self._save_settings()
        self._write_autosave(force=True)

    def _open_project(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Open MiniMax music project", "", "JSON (*.json)")
        if not path: return
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8")); self.project = self._project_from_dict(data); self.project_path = path; self._sync_ui_from_project(); self.status.setText(f"Opened: {path}")
            self._write_autosave(force=True)
        except Exception as exc:
            QMessageBox.critical(self, "Open project failed", str(exc))

    # ---- refs ----
    def _add_reference(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(self, "Add MiniMax reference images", "", "Images (*.png *.jpg *.jpeg *.webp *.bmp);;All files (*.*)")
        if not paths: return
        current = self._refs_from_table()
        for path in paths:
            if len(current) >= 9: break
            if any(os.path.normcase(r.path) == os.path.normcase(path) for r in current): continue
            current.append(ReferenceAsset(name=Path(path).stem, kind="Character", path=path))
        self.project.references = current; self._populate_refs()

    def _remove_reference(self) -> None:
        rows = sorted({i.row() for i in self.refs_table.selectedIndexes()}, reverse=True)
        for row in rows: self.refs_table.removeRow(row)
        self.project.references = self._refs_from_table()

    def _populate_refs(self) -> None:
        self.refs_table.blockSignals(True); self.refs_table.setRowCount(0)
        for ref in self.project.references[:9]:
            row = self.refs_table.rowCount(); self.refs_table.insertRow(row); self.refs_table.setRowHeight(row, 92)
            use = QTableWidgetItem(""); use.setFlags(use.flags() | Qt.ItemIsUserCheckable); use.setCheckState(Qt.Checked if ref.enabled else Qt.Unchecked); self.refs_table.setItem(row, 0, use)
            preview = QLabel(self.refs_table); preview.setAlignment(Qt.AlignCenter); preview.setMinimumSize(112, 78); preview.setMaximumSize(112, 78)
            pix = QPixmap(ref.path) if ref.path and Path(ref.path).is_file() else QPixmap()
            if not pix.isNull():
                preview.setPixmap(pix.scaled(108, 74, Qt.KeepAspectRatio, Qt.SmoothTransformation))
                preview.setToolTip(ref.path)
            else:
                preview.setText("No preview")
            self.refs_table.setCellWidget(row, 1, preview)
            self.refs_table.setItem(row, 2, QTableWidgetItem(ref.name))
            role_combo = QComboBox(self.refs_table)
            role_combo.addItems(list(REFERENCE_TYPES))
            role_combo.setCurrentText(_normalise_reference_kind(ref.kind))
            role_combo.setToolTip(
                "Character = reusable person/identity. Background / Location = recurring environment. Object / Prop = reusable item. "
                "Style / Mood = visual treatment. Picture / Composition anchor = use the image itself as framing/keyframe/shot-planning guidance."
            )
            self.refs_table.setCellWidget(row, 3, role_combo)
            self.refs_table.setItem(row, 4, QTableWidgetItem(ref.path)); self.refs_table.setItem(row, 5, QTableWidgetItem(ref.description))
        self.refs_table.blockSignals(False)

    def _refs_from_table(self) -> List[ReferenceAsset]:
        refs: List[ReferenceAsset] = []
        for row in range(self.refs_table.rowCount()):
            def txt(col): return self.refs_table.item(row, col).text().strip() if self.refs_table.item(row, col) else ""
            role_widget = self.refs_table.cellWidget(row, 3)
            kind = _normalise_reference_kind(role_widget.currentText() if isinstance(role_widget, QComboBox) else txt(3))
            refs.append(ReferenceAsset(name=txt(2) or Path(txt(4)).stem, kind=kind, path=txt(4), description=txt(5), enabled=bool(self.refs_table.item(row,0) and self.refs_table.item(row,0).checkState() == Qt.Checked)))
        return refs[:9]

    # ---- analysis ----
    def _require_audio(self) -> Optional[str]:
        path = self.edit_audio.text().strip()
        if not path or not Path(path).is_file(): QMessageBox.warning(self, "Song missing", "Select a readable master song first."); return None
        return path

    def _set_busy(self, text: str) -> None:
        self.progress.setRange(0, 0); self.status.setText(text)

    def _set_ready(self, text: str = "Ready.") -> None:
        self.progress.setRange(0, 1); self.progress.setValue(0); self.status.setText(text)

    def _run_worker(self, fn, success, *args) -> None:
        if self.worker is not None and self.worker.isRunning(): QMessageBox.information(self, "Busy", "A job is already running."); return
        self.worker = FunctionWorker(fn, *args); self.worker.progress.connect(self._worker_progress); self.worker.succeeded.connect(success); self.worker.failed.connect(self._worker_failed); self.worker.finished.connect(lambda: self.progress.setRange(0,1)); self.worker.start()

    def _worker_progress(self, text: str) -> None:
        marker = "__MINIMAX_SHOT_DONE__|"
        fail_marker = "__MINIMAX_SHOT_FAILED__|"
        if text.startswith(marker):
            parts = text.split("|", 2)
            if len(parts) == 3:
                try:
                    index = int(parts[1]); output_path = parts[2]
                    for shot in self.project.shots:
                        if shot.index == index:
                            shot.output_path = output_path; shot.status = "Generated"; break
                    self._populate_review(select_index=index)
                    self.status.setText(f"Shot {index} finished. Continuing with the remaining shots...")
                    return
                except Exception:
                    pass
        elif text.startswith(fail_marker):
            parts = text.split("|", 2)
            if len(parts) >= 2:
                try:
                    index = int(parts[1])
                    for shot in self.project.shots:
                        if shot.index == index:
                            shot.status = "Failed"; break
                    self._populate_review(select_index=index)
                    self.status.setText(f"Shot {index} failed. Continuing with the remaining shots...")
                    return
                except Exception:
                    pass
        self.status.setText(text)

    def _worker_failed(self, message: str) -> None:
        if hasattr(self, "btn_stop_generation"):
            self.btn_stop_generation.setEnabled(False)
        self._set_ready("Failed."); QMessageBox.critical(self, "MiniMax Music Clip Creator", message)

    def _start_analysis(self) -> None:
        audio = self._require_audio()
        if not audio: return
        self._pull_ui(); self._set_busy("Analyzing track..."); self._run_worker(_analysis_task, self._analysis_done, audio, self.spin_sensitivity.value())

    def _analysis_done(self, result: AnalysisResult) -> None:
        self.project.analysis = result; self._populate_analysis(); self._set_ready("Music analysis complete.")

    def _update_whisper_buttons(self) -> None:
        ready = _whisper_cpp_ready()
        if hasattr(self, "btn_whisper"):
            self.btn_whisper.setEnabled(ready)
            self.btn_whisper.setToolTip(
                "Transcribe the selected song with the portable multilingual Whisper.cpp Small model."
                if ready else "Download Whisper.cpp Small first."
            )
        if hasattr(self, "btn_download_whisper"):
            self.btn_download_whisper.setText("Whisper.cpp Small installed" if ready else "Download Whisper.cpp Small")
            self.btn_download_whisper.setEnabled(not ready)

    def _download_whisper_cpp(self) -> None:
        if _whisper_cpp_ready():
            self._update_whisper_buttons()
            return
        self._set_busy("Downloading Whisper.cpp Small multilingual...")
        self._run_worker(_download_whisper_cpp_task, self._whisper_download_done)

    def _whisper_download_done(self, _path: str) -> None:
        self._update_whisper_buttons()
        self._set_ready("Whisper.cpp Small multilingual installed in presets/bin/whisper.")

    def _start_whisper(self) -> None:
        if not _whisper_cpp_ready():
            QMessageBox.information(self, "Whisper.cpp not installed", "Use the Download Whisper.cpp Small button first. It will be stored in presets/bin/whisper.")
            return
        audio = self._require_audio()
        if not audio: return
        self._set_busy("Transcribing lyrics with standalone Whisper.cpp..."); self._run_worker(_whisper_task, self._whisper_done, audio)

    def _whisper_done(self, lyrics: List[LyricSegment]) -> None:
        self.project.lyrics = lyrics; self._populate_analysis(); self._set_ready(f"Whisper.cpp produced {len(lyrics)} cleaned lyric phrases for safe shot timing." if lyrics else "Whisper.cpp found no lyric phrases; instrumental planning will be used.")

    def _clear_lyrics(self) -> None:
        self.project.lyrics = []; self._populate_analysis(); self.status.setText("Lyrics cleared. Planner will use instrumental timing.")

    def _populate_analysis(self) -> None:
        a = self.project.analysis
        dur = a.duration or (probe_duration(self.project.audio_path) if self.project.audio_path else 0.0)
        self.label_duration.setText(f"Duration: {_fmt_time(dur)} ({dur:.2f} s)" if dur else "Duration: not analyzed")
        mode = "Whisper lyric timing" if (self.project.whisper_timing_enabled and self.project.lyrics) else ("Lyrics loaded / default timing" if self.project.lyrics else "Instrumental/default timing")
        self.analysis_summary.setText(f"{mode} • {len(a.beats)} beat candidates • {len(a.sections)} energy sections • {len(self.project.lyrics)} cleaned Whisper phrases")
        self.lyrics_table.setRowCount(0)
        for seg in self.project.lyrics:
            r = self.lyrics_table.rowCount(); self.lyrics_table.insertRow(r); self.lyrics_table.setItem(r,0,QTableWidgetItem(_fmt_time(seg.start))); self.lyrics_table.setItem(r,1,QTableWidgetItem(_fmt_time(seg.end))); self.lyrics_table.setItem(r,2,QTableWidgetItem(seg.text))

    # ---- director ----
    def _create_plan(self) -> None:
        audio = self._require_audio()
        if not audio: return
        self._pull_ui()
        if self.project.analysis.duration <= 0:
            self.project.analysis.duration = probe_duration(audio)
        try:
            self.project.shots = build_shot_plan(self.project)
            for shot in self.project.shots:
                shot.reference_names = auto_assign_references(self.project, shot); shot.prompt = build_default_prompt(self.project, shot)
            self._populate_shots(); self._populate_review(); self.status.setText(f"Created {len(self.project.shots)} MiniMax shots.")
        except Exception as exc:
            QMessageBox.critical(self, "Planning failed", str(exc))

    def _rebuild_prompts(self) -> None:
        self._pull_ui()
        for shot in self.project.shots:
            shot.reference_names = auto_assign_references(self.project, shot); shot.prompt = build_default_prompt(self.project, shot)
        self._populate_shots(); self.status.setText("MiniMax-H3 Ref2VA prompts and reference suggestions rebuilt.")

    def _populate_shots(self) -> None:
        selected_index = None
        rows = sorted({i.row() for i in self.shot_table.selectedIndexes()})
        if rows and 0 <= rows[0] < len(self.project.shots):
            selected_index = self.project.shots[rows[0]].index
        self.shot_table.blockSignals(True); self.shot_table.setRowCount(0)
        select_row = -1
        for shot in self.project.shots:
            r = self.shot_table.rowCount(); self.shot_table.insertRow(r)
            values = [
                str(shot.index), f"{_fmt_time(shot.edit_start)} – {_fmt_time(shot.edit_end)}", f"{shot.edit_duration:.2f}s",
                str(shot.frames), f"{shot.generation_duration:.2f}s", shot.section, shot.lyrics,
                ", ".join(_fmt_time(x) for x in shot.internal_cuts), ", ".join(shot.reference_names), shot.prompt,
            ]
            for c, value in enumerate(values):
                item = QTableWidgetItem(value)
                if c not in (8, 9): item.setFlags(item.flags() & ~Qt.ItemIsEditable)
                self.shot_table.setItem(r, c, item)
            self.shot_table.setRowHeight(r, 72)
            if selected_index == shot.index:
                select_row = r
        self.shot_table.blockSignals(False)
        if select_row >= 0:
            self.shot_table.selectRow(select_row)
        self._show_selected_shot_prompt()

    def _shot_table_item_changed(self, item: QTableWidgetItem) -> None:
        row = item.row()
        if not (0 <= row < len(self.project.shots)): return
        shot = self.project.shots[row]
        if item.column() == 8:
            allowed = {r.name for r in self._refs_from_table()}
            shot.reference_names = [x.strip() for x in item.text().split(",") if x.strip() in allowed][:9]
        elif item.column() == 9:
            shot.prompt = item.text().strip()
            if row in {i.row() for i in self.shot_table.selectedIndexes()}:
                self.shot_prompt_editor.blockSignals(True)
                self.shot_prompt_editor.setPlainText(shot.prompt)
                self.shot_prompt_editor.blockSignals(False)

    def _selected_director_shot(self) -> Optional[MusicShot]:
        rows = sorted({i.row() for i in self.shot_table.selectedIndexes()})
        if not rows or not (0 <= rows[0] < len(self.project.shots)):
            return None
        return self.project.shots[rows[0]]

    def _show_selected_shot_prompt(self) -> None:
        shot = self._selected_director_shot()
        self.shot_prompt_editor.blockSignals(True)
        if shot is None:
            self.shot_prompt_editor.clear()
            self.shot_prompt_editor.setPlaceholderText("Select a shot above to view or edit its complete MiniMax-H3 Ref2VA prompt.")
        else:
            self.shot_prompt_editor.setPlainText(shot.prompt or "")
        self.shot_prompt_editor.blockSignals(False)

    def _selected_shot_prompt_changed(self) -> None:
        shot = self._selected_director_shot()
        if shot is None:
            return
        text = self.shot_prompt_editor.toPlainText()
        shot.prompt = text
        row = next((r for r, s in enumerate(self.project.shots) if s.index == shot.index), -1)
        if row >= 0:
            item = self.shot_table.item(row, 9)
            if item is not None and item.text() != text:
                self.shot_table.blockSignals(True)
                item.setText(text)
                self.shot_table.blockSignals(False)

    def _next_job_seed(self) -> int:
        return random.SystemRandom().randint(0, 2_147_483_647)

    def _ensure_job_seed(self) -> int:
        try:
            current = int(self.project.job_seed)
        except Exception:
            current = -1
        if current < 0:
            current = self._next_job_seed()
            self.project.job_seed = current
        return current

    def _prepare_generation_seeds(self, shot_indices: Sequence[int]) -> int:
        base_seed = self._ensure_job_seed()
        target_set = {int(x) for x in shot_indices}
        for shot in self.project.shots:
            if shot.seed < 0:
                # Default behavior: one random seed is chosen once for the full job
                # and reused for every shot unless the user manually overrides a row.
                shot.seed = base_seed
            elif shot.index in target_set and shot.seed >= 0:
                # Explicit per-shot retry seed override; keep it as entered.
                pass
        return base_seed

    def _review_table_item_changed(self, item: QTableWidgetItem) -> None:
        if item.column() != 5:
            return
        row = item.row()
        if not (0 <= row < len(self.project.shots)):
            return
        shot = self.project.shots[row]
        text = (item.text() or "").strip()
        try:
            value = int(text) if text else -1
        except Exception:
            self.review_table.blockSignals(True)
            item.setText(str(shot.seed))
            self.review_table.blockSignals(False)
            QMessageBox.warning(self, "Invalid seed", "Seed must be an integer. Use -1 to follow the job seed.")
            return
        if value < -1:
            self.review_table.blockSignals(True)
            item.setText(str(shot.seed))
            self.review_table.blockSignals(False)
            QMessageBox.warning(self, "Invalid seed", "Seed must be -1 or a non-negative integer.")
            return
        shot.seed = value
        if value >= 0:
            self.status.setText(f"Shot {shot.index} seed override set to {value}.")
        else:
            self.status.setText(f"Shot {shot.index} will follow the job seed.")
        self._update_job_seed_label()
        self._write_autosave(force=True)

    def _update_job_seed_label(self) -> None:
        base = getattr(self.project, "job_seed", -1)
        if base is None or int(base) < 0:
            text = "Job seed: auto (first generation locks one random seed for the whole job). Edit a shot's Seed value below to override it for retries."
        else:
            overrides = sum(1 for s in self.project.shots if int(getattr(s, "seed", -1)) >= 0 and int(getattr(s, "seed", -1)) != int(base))
            if overrides:
                text = f"Job seed: {int(base)} ({overrides} shot override{'s' if overrides != 1 else ''}). Enter -1 in a shot row to follow the job seed again."
            else:
                text = f"Job seed: {int(base)}. All shots use this seed unless you enter a different value in the Seed column for a retry."
        self.label_job_seed.setText(text)

    # ---- generation / assembly ----
    def _sync_existing_generated_outputs(self) -> int:
        """Reconcile saved shot state with raw clips already present on disk."""
        if not self.project.shots:
            return 0
        out_dir = Path(self.project.output_dir or OUTPUT_ROOT / _safe_stem(self.project.audio_path)).resolve()
        raw_dir = out_dir / "raw_clips"
        found = 0
        for shot in self.project.shots:
            expected = raw_dir / f"shot_{shot.index:03d}.mp4"
            current = Path(shot.output_path) if shot.output_path else None
            if current is not None and current.is_file():
                if shot.status != "Generated":
                    shot.status = "Generated"
                found += 1
                continue
            if expected.is_file():
                shot.output_path = str(expected)
                shot.status = "Generated"
                found += 1
            elif shot.status == "Generated":
                shot.output_path = ""
                shot.status = "Planned"
        return found

    def _populate_review(self, select_index: Optional[int] = None) -> None:
        self._sync_existing_generated_outputs()
        current_index = select_index
        if current_index is None:
            rows = sorted({i.row() for i in self.review_table.selectedIndexes()})
            if rows and 0 <= rows[0] < len(self.project.shots): current_index = self.project.shots[rows[0]].index
        self.review_table.blockSignals(True); self.review_table.setRowCount(0)
        select_row = -1
        for shot in self.project.shots:
            r = self.review_table.rowCount(); self.review_table.insertRow(r)
            vals = [str(shot.index), shot.status, str(shot.frames), f"{_fmt_time(shot.edit_start)}–{_fmt_time(shot.edit_end)}", shot.output_path, str(shot.seed)]
            for c, value in enumerate(vals):
                item = QTableWidgetItem(value)
                if c != 5:
                    item.setFlags(item.flags() & ~Qt.ItemIsEditable)
                self.review_table.setItem(r, c, item)
            if current_index == shot.index: select_row = r
        self.review_table.blockSignals(False)
        self._update_job_seed_label()
        if select_row >= 0:
            self.review_table.selectRow(select_row)
        self._show_selected_clip_preview()

    def _selected_review_shot(self) -> Optional[MusicShot]:
        rows = sorted({i.row() for i in self.review_table.selectedIndexes()})
        if not rows or not (0 <= rows[0] < len(self.project.shots)): return None
        return self.project.shots[rows[0]]

    def _video_thumbnail(self, video_path: str) -> Optional[QPixmap]:
        path = Path(video_path)
        if not path.is_file(): return None
        ffmpeg = ffmpeg_path()
        if not ffmpeg: return None
        preview_dir = OUTPUT_ROOT / "_preview_cache"; preview_dir.mkdir(parents=True, exist_ok=True)
        key = f"{path.stem}_{int(path.stat().st_mtime_ns)}.jpg"
        thumb = preview_dir / key
        if not thumb.is_file():
            subprocess.run([ffmpeg, "-y", "-ss", "0.5", "-i", str(path), "-frames:v", "1", "-vf", "scale=640:-2", str(thumb)], capture_output=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if not thumb.is_file(): return None
        pix = QPixmap(str(thumb))
        return None if pix.isNull() else pix

    def _show_selected_clip_preview(self) -> None:
        if not hasattr(self, "review_preview"): return
        shot = self._selected_review_shot()
        if not shot:
            self.review_preview.setPixmap(QPixmap()); self.review_preview.setText("Select a generated shot to preview it."); return
        if not shot.output_path or not Path(shot.output_path).is_file():
            self.review_preview.setPixmap(QPixmap()); self.review_preview.setText(f"Shot {shot.index}: {shot.status or 'Not generated yet'}"); return
        pix = self._video_thumbnail(shot.output_path)
        if pix is None:
            self.review_preview.setPixmap(QPixmap()); self.review_preview.setText(f"Shot {shot.index} generated. Use Play selected clip to review it."); return
        self.review_preview.setText("")
        self.review_preview.setPixmap(pix.scaled(640, 260, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        self.review_preview.setToolTip(shot.output_path)

    def _play_selected_clip(self) -> None:
        shot = self._selected_review_shot()
        if not shot or not shot.output_path or not Path(shot.output_path).is_file():
            QMessageBox.information(self, "No clip", "Select a generated shot first."); return
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(Path(shot.output_path).resolve())))

    def _open_selected_clip_folder(self) -> None:
        shot = self._selected_review_shot()
        if not shot or not shot.output_path or not Path(shot.output_path).is_file():
            QMessageBox.information(self, "No clip", "Select a generated shot first."); return
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(Path(shot.output_path).resolve().parent)))

    def _queue_mode_active(self) -> bool:
        return callable(self.queue_adapter)

    def _prepare_shot_queue_job(self, shot: MusicShot) -> Dict[str, Any]:
        if not MINIMAX_PY.is_file():
            raise RuntimeError(f"MiniMax environment Python not found: {MINIMAX_PY}")
        if not GENERATE_REF.is_file():
            raise RuntimeError("MiniMax Ref2VA launcher not found: helpers/generate_ref.py")
        if not Path(self.project.audio_path).is_file():
            raise RuntimeError("The project song file is missing.")
        out_dir = Path(self.project.output_dir or OUTPUT_ROOT / _safe_stem(self.project.audio_path)).resolve()
        raw_dir = out_dir / "raw_clips"
        audio_dir = out_dir / "audio_chunks"
        raw_dir.mkdir(parents=True, exist_ok=True)
        audio_dir.mkdir(parents=True, exist_ok=True)
        audio_chunk = audio_dir / f"shot_{shot.index:03d}.wav"
        _extract_audio_slice(self.project.audio_path, audio_chunk, shot.generation_start, shot.frames / FPS)
        out_path = raw_dir / f"shot_{shot.index:03d}.mp4"
        refs_by_name = {r.name: r for r in self.project.references if r.enabled and Path(r.path).is_file()}
        selected_names = [n for n in shot.reference_names if n in refs_by_name]
        if not selected_names:
            selected_names = [n for n in auto_assign_references(self.project, shot) if n in refs_by_name]
        if not selected_names and refs_by_name:
            selected_names = [next(iter(refs_by_name))]
        selected = [refs_by_name[n] for n in selected_names[:9]]
        generation_prompt = build_generation_prompt(self.project, shot, selected)
        width, height = RESOLUTION_PRESETS[self.project.resolution][self.project.aspect]
        args = [
            "helpers/generate_ref.py",
            "--prompt", generation_prompt,
            "--width", str(width), "--height", str(height),
            "--frames", str(shot.frames), "--steps", str(self.project.steps),
            "--cfg", str(self.project.cfg), "--seed", str(shot.seed),
            "--shift", str(self.project.shift), "--audio-shift", str(self.project.audio_shift),
            "--ref-image-size", self.project.ref_image_size,
            "--ref-audio", str(audio_chunk),
            "--output", str(out_path),
        ]
        if self.project.vram_manager_enabled:
            args += ["--vram-manager-auto" if self.project.vram_auto_bypass else "--vram-manager"]
            args += [
                "--vram-residency-engine", str(self.project.vram_residency_engine or "static"),
                "--vram-runtime-free-gb", str(float(self.project.vram_runtime_free_gb)),
                "--vram-text-headroom-gb", str(float(self.project.vram_text_headroom_gb)),
                "--vram-diffusion-headroom-gb", str(float(self.project.vram_diffusion_headroom_gb)),
                "--vram-offload-chunk-mb", str(int(self.project.vram_offload_chunk_mb)),
                "--vram-max-resident-weights-gb", str(float(self.project.vram_max_resident_weights_gb)),
                "--vram-block-check-interval", str(int(self.project.vram_block_check_interval)),
                "--vram-async-streams", str(int(self.project.vram_async_streams)),
                "--vram-video-vae-reserve-gb", str(float(self.project.vram_video_vae_reserve_gb)),
                "--vram-audio-vae-reserve-gb", str(float(self.project.vram_audio_vae_reserve_gb)),
                "--vram-residency-target-free-gb", str(float(self.project.vram_residency_target_free_gb)),
                "--vram-residency-warmup-blocks", str(int(self.project.vram_residency_warmup_blocks)),
                "--vram-residency-refill-interval", str(int(self.project.vram_residency_refill_interval)),
            ]
            args += ["--vram-residency-fill" if self.project.vram_residency_fill else "--no-vram-residency-fill"]
        if self.project.sage_attention:
            args += ["--sage-attention"]
        if self.project.spectrum:
            args += ["--spectrum"]
        turbo_lora = str(self.project.turbo_lora_path or "").strip()
        if turbo_lora:
            turbo_path = Path(turbo_lora)
            if not turbo_path.is_file():
                raise RuntimeError(f"Turbo LoRA not found: {turbo_lora}")
            args += ["--lora", str(turbo_path.resolve()), "--lora-strength", str(float(self.project.turbo_lora_strength))]
        for ref in selected:
            args += ["--ref-image", ref.path]
        shot.output_path = str(out_path)
        shot.status = "Queued"
        return {
            "args": args,
            "output": str(out_path),
            "label": f"Music Clip Shot {shot.index}: {(self.project.title or _safe_stem(self.project.audio_path))}",
            "frames": int(shot.frames),
            "steps": int(self.project.steps),
            "seed": int(shot.seed),
            "resolution": f"{width} × {height}",
            "prompt": generation_prompt,
            "music_shot_index": int(shot.index),
            "music_project_output": str(out_dir),
        }

    def _queue_shots(self, indices: Sequence[int]) -> None:
        if not self._queue_mode_active():
            return
        self._pull_ui()
        self._prepare_generation_seeds(list(indices))
        by_index = {s.index: s for s in self.project.shots}
        queued = 0
        try:
            for index in indices:
                shot = by_index.get(int(index))
                if shot is None:
                    continue
                spec = self._prepare_shot_queue_job(shot)
                self.queue_adapter(spec)
                queued += 1
            self._populate_review(select_index=(int(indices[0]) if len(indices) == 1 else None))
            self._write_autosave(force=True)
            self.status.setText(f"Added {queued} Music Clip shot{'s' if queued != 1 else ''} to the MiniMax queue.")
        except Exception as exc:
            QMessageBox.critical(self, "Could not queue Music Clip shot", str(exc))

    def _queue_assembly(self) -> None:
        if not self._queue_mode_active():
            return
        self._pull_ui()
        self._sync_existing_generated_outputs()
        out_dir = Path(self.project.output_dir or OUTPUT_ROOT / _safe_stem(self.project.audio_path)).resolve()
        raw_dir = out_dir / "raw_clips"
        for shot in self.project.shots:
            expected = raw_dir / f"shot_{shot.index:03d}.mp4"
            if not shot.output_path:
                shot.output_path = str(expected)
        queue_dir = out_dir / "_queue"
        queue_dir.mkdir(parents=True, exist_ok=True)
        snapshot = queue_dir / f"assembly_project_{int(time.time() * 1000)}.json"
        snapshot.write_text(json.dumps(asdict(self.project), ensure_ascii=False, indent=2), encoding="utf-8")
        final = out_dir / f"{_safe_stem(self.project.title or self.project.audio_path)}_minimax_music_video.mp4"
        spec = {
            "args": ["helpers/minimax_music_clip.py", "--queue-assemble", str(snapshot)],
            "output": str(final),
            "label": f"Assemble Music Video: {(self.project.title or _safe_stem(self.project.audio_path))}",
            "frames": 0,
            "steps": 0,
            "seed": -1,
            "resolution": "Music assembly",
            "prompt": "Assemble trimmed Music Clip shots and mux the original master song.",
            "music_assembly": True,
            "music_project_output": str(out_dir),
        }
        self.queue_adapter(spec)
        self.status.setText("Added final Music Clip assembly to the MiniMax queue.")

    def external_queue_updated(self, job: Optional[Dict[str, Any]] = None) -> None:
        """Called by the standalone host when its queue changes or a Music Clip job ends."""
        changed = self._sync_existing_generated_outputs()
        if changed or (job and (job.get("music_shot_index") or job.get("music_assembly"))):
            self._populate_review(select_index=(int(job.get("music_shot_index")) if job and job.get("music_shot_index") else None))
            self._write_autosave(force=True)

    def _generate_selected(self) -> None:
        rows = sorted({i.row() for i in self.review_table.selectedIndexes()})
        if not rows: QMessageBox.information(self, "Select shot", "Select a shot row first."); return
        indices = [self.project.shots[rows[0]].index]
        if self._queue_mode_active():
            self._queue_shots(indices)
            return
        self._generate_indices(indices)

    def _generate_all(self) -> None:
        self._sync_existing_generated_outputs()
        indices = [s.index for s in self.project.shots if not s.output_path or not Path(s.output_path).is_file() or s.status == "Failed"]
        if not indices: self.status.setText("All planned shots already have outputs."); return
        if self._queue_mode_active():
            self._queue_shots(indices)
            return
        self._generate_indices(indices)

    def _generate_indices(self, indices: List[int]) -> None:
        if not self.project.shots: QMessageBox.warning(self, "No plan", "Create a shot plan first."); return
        _GENERATION_CANCEL.clear()
        self.btn_stop_generation.setEnabled(True)
        self._pull_ui()
        base_seed = self._prepare_generation_seeds(indices)
        selected_index = indices[0] if len(indices) == 1 else None
        self._populate_review(select_index=selected_index)
        self._set_busy(f"Starting MiniMax Ref2VA generation... job seed {base_seed}")
        self._write_autosave(force=True)
        self._run_worker(_generation_task, self._generation_done, self.project, indices)

    def _stop_generation(self) -> None:
        if self.worker is None or not self.worker.isRunning():
            self.btn_stop_generation.setEnabled(False)
            return
        _GENERATION_CANCEL.set()
        self.btn_stop_generation.setEnabled(False)
        self.status.setText("Stopping MiniMax generation...")
        _terminate_generation_process_tree()

    def _generation_done(self, results: List[Dict[str, Any]]) -> None:
        by_index = {s.index: s for s in self.project.shots}
        failures = []
        cancelled = False
        for result in results:
            shot = by_index.get(int(result.get("index", -1)))
            if not shot: continue
            if result.get("ok"):
                shot.output_path = str(result.get("output_path") or ""); shot.status = "Generated"
            elif result.get("cancelled"):
                shot.status = "Planned"
                cancelled = True
            else:
                shot.status = "Failed"; failures.append(f"Shot {shot.index}: {result.get('message','failed')}")
        self.btn_stop_generation.setEnabled(False)
        self._populate_review()
        if cancelled or _GENERATION_CANCEL.is_set():
            self._set_ready("Generation stopped. Finished clips were kept; unfinished clips remain planned.")
        else:
            self._set_ready("Generation finished." if not failures else f"Generation finished with {len(failures)} failed shot(s).")
        if failures: QMessageBox.warning(self, "Some shots failed", "\n\n".join(failures[:5]))

    def _assemble(self) -> None:
        if not self.project.shots: QMessageBox.warning(self, "No plan", "Create and generate the shot plan first."); return
        if self._queue_mode_active():
            self._queue_assembly()
            return
        self._pull_ui(); self._set_busy("Assembling final music video..."); self._run_worker(_assembly_task, self._assembly_done, self.project)

    def _assembly_done(self, path: str) -> None:
        self._set_ready(f"Final video saved: {path}")
        QMessageBox.information(self, "Finished", f"Final music video saved:\n{path}")

    def _open_output_folder(self) -> None:
        path = Path(self.edit_output.text().strip() or OUTPUT_ROOT); path.mkdir(parents=True, exist_ok=True); QDesktopServices.openUrl(QUrl.fromLocalFile(str(path.resolve())))

    def _browse_turbo_lora(self) -> None:
        start = self.edit_turbo_lora.text().strip()
        if start and Path(start).is_file():
            start = str(Path(start).parent)
        if not start:
            start = str(ROOT / "models" / "minimax_h3" / "loras")
        path, _ = QFileDialog.getOpenFileName(self, "Select MiniMax Turbo / speed LoRA", start, "LoRA files (*.safetensors *.pt *.bin);;All files (*.*)")
        if path:
            self.edit_turbo_lora.setText(path)

    # ---- working-session autosave ----
    def _autosave_payload(self, pull_ui: bool = True) -> Dict[str, Any]:
        """Return the complete recoverable working state.

        ``pull_ui`` must be False while *loading* a saved session. The widgets still
        contain their construction defaults at that point; pulling them would overwrite
        the just-restored project (idea text, references, LoRA, steps, etc.).
        """
        if pull_ui:
            self._pull_ui()
        return {
            "version": 2,
            "project_path": str(self.project_path or ""),
            "project": asdict(self.project),
        }

    def _load_autosave(self) -> bool:
        try:
            if not AUTOSAVE_PATH.is_file():
                return False
            data = json.loads(AUTOSAVE_PATH.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                return False
            # Current format wraps the project so we can also remember the explicit
            # project filename. Accept an old/direct project dict as a safe fallback.
            project_data = data.get("project") if isinstance(data.get("project"), dict) else data
            self.project = self._project_from_dict(project_data)
            self.project_path = str(data.get("project_path") or "") if isinstance(data.get("project"), dict) else ""
            # Critical: do NOT pull values from the not-yet-synchronised widgets here.
            # Doing that used to replace restored fields with UI defaults on every start.
            self._autosave_last_text = json.dumps(self._autosave_payload(pull_ui=False), ensure_ascii=False, sort_keys=True)
            return True
        except Exception:
            return False

    def _write_autosave(self, force: bool = False) -> None:
        try:
            payload = self._autosave_payload()
            compare_text = json.dumps(payload, ensure_ascii=False, sort_keys=True)
            if not force and compare_text == self._autosave_last_text:
                return
            AUTOSAVE_PATH.parent.mkdir(parents=True, exist_ok=True)
            tmp = AUTOSAVE_PATH.with_suffix(AUTOSAVE_PATH.suffix + ".tmp")
            tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
            os.replace(str(tmp), str(AUTOSAVE_PATH))
            self._autosave_last_text = compare_text
            # Generation preferences also survive deliberate New Project/session resets.
            self._save_settings()
        except Exception:
            # Autosave must never interrupt generation or normal GUI use.
            pass

    def _start_autosave(self) -> None:
        self._autosave_timer = QTimer(self)
        self._autosave_timer.setInterval(1500)
        self._autosave_timer.timeout.connect(self._write_autosave)
        self._autosave_timer.start()
        # Create presets/setsave/minimax_music_clip.json immediately, even before
        # the first edit, so the persistence location is predictable.
        self._write_autosave(force=True)
        app = QApplication.instance()
        if app is not None:
            try:
                app.aboutToQuit.connect(lambda: self._write_autosave(force=True))
            except Exception:
                pass

    # ---- settings ----
    def _update_frame_label(self) -> None:
        frames = MUSIC_FRAME_GRID[self.slider_frames.value()]
        self.label_frames.setText(f"{frames} frames (~{frames / FPS:.2f} s)")

    def _load_settings(self) -> None:
        try:
            if SETTINGS_PATH.is_file():
                data = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
                self.project.output_dir = str(data.get("output_dir") or self.project.output_dir)
                self.project.resolution = str(data.get("resolution") or self.project.resolution)
                self.project.aspect = str(data.get("aspect") or self.project.aspect)
                self.project.max_frames = int(data.get("max_frames") or self.project.max_frames)
                self.project.steps = int(data.get("steps") or self.project.steps)
                self.project.turbo_lora_path = str(data.get("turbo_lora_path") or self.project.turbo_lora_path)
                self.project.turbo_lora_strength = float(data.get("turbo_lora_strength", self.project.turbo_lora_strength))
                # Migration: older Music Clip Creator builds stored one vram_auto flag.
                if "vram_manager_enabled" in data:
                    self.project.vram_manager_enabled = bool(data.get("vram_manager_enabled"))
                    self.project.vram_auto_bypass = bool(data.get("vram_auto_bypass", True))
                elif "vram_auto" in data:
                    # Old checked state meant --vram-manager-auto; old unchecked state meant no manager.
                    self.project.vram_manager_enabled = bool(data.get("vram_auto"))
                    self.project.vram_auto_bypass = True
                for key in (
                    "vram_residency_engine", "vram_runtime_free_gb", "vram_text_headroom_gb",
                    "vram_diffusion_headroom_gb", "vram_offload_chunk_mb", "vram_max_resident_weights_gb",
                    "vram_block_check_interval", "vram_async_streams", "vram_video_vae_reserve_gb",
                    "vram_audio_vae_reserve_gb", "vram_residency_fill", "vram_residency_target_free_gb",
                    "vram_residency_warmup_blocks", "vram_residency_refill_interval",
                ):
                    if key in data:
                        setattr(self.project, key, data[key])
                self.project.sage_attention = bool(data.get("sage_attention", self.project.sage_attention))
                self.project.spectrum = bool(data.get("spectrum", self.project.spectrum))
        except Exception:
            pass

    def _save_settings(self) -> None:
        try:
            SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
            data = {
                "output_dir": self.project.output_dir, "resolution": self.project.resolution, "aspect": self.project.aspect,
                "max_frames": self.project.max_frames, "steps": self.project.steps,
                "vram_manager_enabled": self.project.vram_manager_enabled, "vram_auto_bypass": self.project.vram_auto_bypass,
                "vram_residency_engine": self.project.vram_residency_engine,
                "vram_runtime_free_gb": self.project.vram_runtime_free_gb,
                "vram_text_headroom_gb": self.project.vram_text_headroom_gb,
                "vram_diffusion_headroom_gb": self.project.vram_diffusion_headroom_gb,
                "vram_offload_chunk_mb": self.project.vram_offload_chunk_mb,
                "vram_max_resident_weights_gb": self.project.vram_max_resident_weights_gb,
                "vram_block_check_interval": self.project.vram_block_check_interval,
                "vram_async_streams": self.project.vram_async_streams,
                "vram_video_vae_reserve_gb": self.project.vram_video_vae_reserve_gb,
                "vram_audio_vae_reserve_gb": self.project.vram_audio_vae_reserve_gb,
                "vram_residency_fill": self.project.vram_residency_fill,
                "vram_residency_target_free_gb": self.project.vram_residency_target_free_gb,
                "vram_residency_warmup_blocks": self.project.vram_residency_warmup_blocks,
                "vram_residency_refill_interval": self.project.vram_residency_refill_interval,
                "turbo_lora_path": self.project.turbo_lora_path, "turbo_lora_strength": self.project.turbo_lora_strength,
                "sage_attention": self.project.sage_attention, "spectrum": self.project.spectrum,
            }
            SETTINGS_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")
        except Exception:
            pass


class MiniMaxMusicClipWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("MiniMax H3 Music Clip Creator")
        self.resize(1500, 900)
        self.setCentralWidget(MiniMaxMusicClipWidget(self))


def install_minimax_music_clip_tool(parent: QWidget, container: QWidget, queue_adapter=None) -> MiniMaxMusicClipWidget:
    """Small integration hook for FrameVision/standalone hosts."""
    widget = MiniMaxMusicClipWidget(container, queue_adapter=queue_adapter)
    layout = container.layout()
    if layout is None:
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
    layout.addWidget(widget)
    return widget


def _queue_cli_main(argv: Sequence[str]) -> Optional[int]:
    if "--queue-assemble" not in argv:
        return None
    try:
        pos = list(argv).index("--queue-assemble")
        project_path = Path(argv[pos + 1]).resolve()
        data = json.loads(project_path.read_text(encoding="utf-8"))
        project = MiniMaxMusicClipWidget._project_from_dict(data)
        result = _assembly_task(lambda text: print(text, flush=True), project)
        print(f"Saved final music video: {result}", flush=True)
        return 0
    except Exception as exc:
        print(f"Music Clip assembly failed: {exc}", file=sys.stderr, flush=True)
        return 2


def main() -> int:
    cli_rc = _queue_cli_main(sys.argv[1:])
    if cli_rc is not None:
        return cli_rc
    app = QApplication.instance() or QApplication(sys.argv)
    win = MiniMaxMusicClipWindow(); win.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
