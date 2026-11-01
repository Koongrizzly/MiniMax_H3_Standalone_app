"""Portable project files, beat analysis, timed-text import and FFmpeg probing.

No internet calls and no model dependency in this module.
"""
from __future__ import annotations

import json
import math
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
SETTINGS_FILE = ROOT / 'presets' / 'setsave' / 'beatsyncfx_settings.json'

DEFAULT_OPTIONS = {
    'lyric_style': 'Karaoke line',
    'lyric_position': 78,
    'lyric_size': 72,
    'lyric_color': '#F8F9FF',
    'highlight_color': '#D9FA37',
    'lyric_font': 'Anton',
    'beat_style': 'Off',
    'beat_color': '#22DDEE',
    'beat_intensity': 60,
    'beat_decay': 0.24,
    'beat_every': 1,
    'lyric_offset': 0.0,
    'fps': 24,
    'crf': 18,
    'encoder': 'libx264',
    'snap_events': True,
}


def new_project() -> dict:
    return {'version': 1, 'video': '', 'audio': '', 'duration': 0.0,
            'width': 1280, 'height': 720, 'bpm': 120.0, 't0': 0.0,
            'lyrics_text': '', 'words': [], 'lyric_segments': [], 'events': [], 'options': dict(DEFAULT_OPTIONS)}


def load_settings() -> dict:
    try:
        val = json.loads(SETTINGS_FILE.read_text(encoding='utf-8'))
        return val if isinstance(val, dict) else {}
    except (OSError, ValueError):
        return {}


def save_settings(settings: dict) -> None:
    SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
    atomic_json(SETTINGS_FILE, settings)


def atomic_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + '.tmp')
    with temp.open('w', encoding='utf-8') as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    temp.replace(path)


def _make_relative(filename: str, parent: Path) -> str:
    if not filename:
        return ''
    try:
        return os.path.relpath(filename, parent)
    except ValueError:  # different Windows drive
        return str(filename)


def save_project(path: str, project: dict) -> None:
    dest = Path(path).resolve()
    dat = json.loads(json.dumps(project))
    for key in ('video', 'audio'):
        dat[key] = _make_relative(dat.get(key, ''), dest.parent)
    for event in dat.get('events', []):
        if event.get('style') == 'Image / GIF':
            event['text'] = _make_relative(event.get('text', ''), dest.parent)
    atomic_json(dest, dat)




def save_effects_template(path: str, project: dict) -> None:
    """Save reusable cue/effect configuration without any source-media identity.

    Keep referenced user overlays (PNG/GIF): they are effect assets, not the video or
    soundtrack. Relative paths resolve from the template's own location.
    """
    dest = Path(path).resolve()
    dat = json.loads(json.dumps(project))
    for key in ('video', 'audio', 'audio_envelopes_source', 'audio_envelopes'):
        dat.pop(key, None)
    for key in ('width', 'height', 'duration'):
        dat.pop(key, None)
    dat['format'] = 'beatsyncfx-effects-template-v1'
    for event in dat.get('events', []):
        if event.get('style') == 'Image / GIF':
            event['text'] = _make_relative(event.get('text', ''), dest.parent)
    atomic_json(dest, dat)


def load_effects_template(path: str, current_project: dict | None = None) -> dict:
    """Merge reusable effects onto currently selected media without moving the media."""
    loc = Path(path).resolve()
    dat = json.loads(loc.read_text(encoding='utf-8-sig'))
    if not isinstance(dat, dict) or dat.get('format') != 'beatsyncfx-effects-template-v1':
        raise ValueError('Not a BeatSync FX effects template. Use Load project for full project files.')
    base = json.loads(json.dumps(current_project)) if current_project else new_project()
    for key in ('lyrics_text', 'words', 'lyric_segments', 'events', 'bpm', 't0'):
        if key in dat:
            base[key] = dat[key]
    base['options'] = {**DEFAULT_OPTIONS, **base.get('options', {}), **dat.get('options', {})}
    # Audio envelope data belongs to the current audio. Never import it from a template.
    for event in base.get('events', []):
        if event.get('style') == 'Image / GIF' and event.get('text'):
            file = Path(event['text'])
            event['text'] = str((loc.parent / file).resolve()) if not file.is_absolute() else str(file)
    return base


def load_project(path: str) -> dict:
    loc = Path(path).resolve()
    dat = json.loads(loc.read_text(encoding='utf-8-sig'))
    if not isinstance(dat, dict):
        raise ValueError('Project JSON must contain an object')
    project = new_project()
    project.update(dat)
    project['options'] = {**DEFAULT_OPTIONS, **dat.get('options', {})}
    for key in ('video', 'audio'):
        if project.get(key):
            file = Path(project[key])
            project[key] = str((loc.parent / file).resolve()) if not file.is_absolute() else str(file)
    for event in project.get('events', []):
        if event.get('style') == 'Image / GIF' and event.get('text'):
            file = Path(event['text'])
            event['text'] = str((loc.parent / file).resolve()) if not file.is_absolute() else str(file)
    return project


def ffmpeg_default_directory() -> Path:
    """Prefer the host GrizzlyMax bundle when BeatSyncFX is embedded.

    Standalone installs continue to use BeatSyncFX/presets/bin.
    """
    host_bundle = ROOT.parent / 'presets' / 'bin'
    if (host_bundle / 'ffmpeg.exe').is_file() and (host_bundle / 'ffprobe.exe').is_file():
        return host_bundle
    return ROOT / 'presets' / 'bin'


def locate_ffmpeg(saved: str = '') -> tuple[str, str]:
    """Find a matched FFmpeg/FFprobe bundle, preferring the host by default.

    An explicitly chosen saved path remains usable if it is a complete bundle.
    A saved missing or incomplete bundle cannot hide a valid GrizzlyMax install.
    """
    host_bundle = ROOT.parent / 'presets' / 'bin'
    own_bundle = ROOT / 'presets' / 'bin'
    candidates = [saved,
                  host_bundle / 'ffmpeg.exe', host_bundle / 'ffmpeg',
                  own_bundle / 'ffmpeg.exe', own_bundle / 'ffmpeg',
                  shutil.which('ffmpeg')]
    for candidate in candidates:
        if not candidate:
            continue
        f = Path(candidate)
        if f.is_dir():
            f = f / ('ffmpeg.exe' if (f / 'ffmpeg.exe').is_file() else 'ffmpeg')
        if not f.is_file():
            continue
        probe = f.with_name('ffprobe.exe' if f.suffix.lower() == '.exe' else 'ffprobe')
        if probe.is_file():
            return str(f), str(probe)
    return '', ''


def _windows_no_console() -> dict:
    return {'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {}


def probe_video(ffprobe: str, video: str) -> dict:
    if not ffprobe or not Path(ffprobe).is_file():
        raise RuntimeError('ffprobe.exe not found. Select an FFmpeg distribution containing ffprobe.exe.')
    p = subprocess.run([ffprobe, '-v', 'error', '-show_entries',
                        'format=duration:stream=codec_type,width,height,avg_frame_rate',
                        '-of', 'json', video], capture_output=True, text=True,
                       timeout=35, **_windows_no_console())
    if p.returncode:
        raise RuntimeError(p.stderr.strip() or 'ffprobe failed')
    info = json.loads(p.stdout)
    streams = [s for s in info.get('streams', []) if s.get('codec_type') == 'video']
    if not streams:
        raise ValueError('This file does not contain a video stream')
    s = streams[0]
    return {'duration': float(info.get('format', {}).get('duration') or 0),
            'width': int(s.get('width') or 1280), 'height': int(s.get('height') or 720)}


def decode_mono(ffmpeg: str, audio: str, lowpass: bool = False, sr: int = 11025) -> np.ndarray:
    cmd = [ffmpeg, '-hide_banner', '-loglevel', 'error', '-i', audio, '-vn']
    if lowpass:
        cmd.extend(['-af', 'highpass=f=35,lowpass=f=175'])
    cmd.extend(['-ac', '1', '-ar', str(sr), '-f', 'f32le', 'pipe:1'])
    proc = subprocess.run(cmd, capture_output=True, timeout=180, **_windows_no_console())
    if proc.returncode:
        raise RuntimeError('FFmpeg could not decode the audio: ' + proc.stderr.decode(errors='replace')[-800:])
    samples = np.frombuffer(proc.stdout, dtype='<f4').copy()
    if samples.size < sr * 2:
        raise ValueError('At least two seconds of audio are needed for beat detection')
    return samples


def detect_beats(samples: np.ndarray, sample_rate: int = 11025) -> dict:
    """Estimate a constant-tempo kick grid. The result is editable: changing-tempo music needs manual correction.

    Works with FFmpeg-decoded PCM; does not require Demucs, librosa or AI.
    """
    hop = 128
    n = len(samples) // hop
    x = samples[:n * hop].reshape(n, hop).astype(np.float64)
    e = np.sqrt(np.mean(x * x, axis=1))
    # Short attack relative to slower local loudness envelope
    slow = np.convolve(e, np.ones(17) / 17.0, 'same')
    onset = np.maximum(0.0, e - slow * 0.72)
    onset = np.maximum(0.0, onset - np.roll(onset, 2) * 0.6)
    onset[:3] = 0.0
    limit = np.quantile(onset, 0.58)
    onset = np.maximum(0, onset - limit)
    if np.max(onset) < 1e-6:
        raise ValueError('No distinguishable rhythmic transients found. Set BPM and first beat manually.')
    # Reduce bias from a few very loud transients.
    cap = np.quantile(onset, 0.995)
    onset = np.minimum(onset, max(cap, 1e-8))
    fps = sample_rate / hop
    lo, hi = int(fps * 60 / 210), int(fps * 60 / 65)
    ft = np.fft.rfft(onset, n=1 << (2 * n - 1).bit_length())
    ac = np.fft.irfft(ft * ft.conj())[:hi + 1]
    ac = ac / np.maximum(1, n - np.arange(len(ac)))
    # Favor true quarter-note subdivisions without hard-coding an artist's tempo.
    scores = []
    for lag in range(lo, hi):
        scores.append((float(ac[lag]) + .26 * float(ac[min(2 * lag, hi)]) if 2 * lag <= hi else float(ac[lag]), lag))
    tops = sorted(scores, reverse=True)[:30]
    proposals = set()
    for _, lag in tops:
        bpm = 60 * fps / lag
        for mult in (0.5, 1.0, 2.0):
            val = bpm * mult
            if 65 <= val <= 210:
                for shift in np.arange(-4.0, 4.01, .2):
                    if 65 <= val + shift <= 210:
                        proposals.add(round(val + shift, 2))
    # Determine phase and score each candidate from audible event coincidences.
    # Peak times are picked sparsely; the underlying onset is used as weights.
    maxima = (onset > np.roll(onset, 1)) & (onset >= np.roll(onset, -1))
    maxima &= onset >= np.quantile(onset, .80)
    raw_indices = np.flatnonzero(maxima)
    # Refractory window: one bass drum hit can produce several envelope maxima.
    accepted = []
    gap = int(fps * .15)
    for ix in sorted(raw_indices, key=lambda z: onset[z], reverse=True):
        if not any(abs(ix - keep) < gap for keep in accepted):
            accepted.append(int(ix))
    indices = np.array(sorted(accepted), dtype=int)
    if len(indices) < 6:
        raise ValueError('Too few onsets for reliable automatic tempo detection.')
    times = indices / fps
    weights = onset[indices]
    best = (-1, 120.0, 0.0)
    for bpm in proposals:
        period = 60 / bpm
        # Circular phase histogram, then refine around the strongest bin.
        phase = np.mod(times, period)
        hist, _ = np.histogram(phase, bins=96, range=(0, period), weights=weights)
        smooth = (hist + np.roll(hist, 1) * .65 + np.roll(hist, -1) * .65 +
                  np.roll(hist, 2) * .25 + np.roll(hist, -2) * .25)
        k = int(np.argmax(smooth))
        phase0 = (k + .5) * period / 96
        dist = np.abs((times - phase0 + period / 2) % period - period / 2)
        match = float(np.sum(weights * np.exp(-.5 * (dist / .042) ** 2)) / max(np.sum(weights), 1e-9))
        # An octave-up BPM may align half its beats but leave every other expected kick empty.
        # This penalty resolves double-tempo ambiguity without always favoring slow tempos.
        beat_count = max(1.0, (times[-1] - times[0]) / period)
        coverage = min(1.0, float(np.count_nonzero(dist < .050)) / beat_count)
        score = match * (.72 + .28 * coverage)
        if score > best[0]:
            best = (score, bpm, phase0)
    score, bpm, phase = best
    if not math.isfinite(bpm):
        raise ValueError('Cannot estimate song tempo')
    return {'bpm': round(float(bpm), 2), 't0': round(float(phase), 4),
            'confidence': round(float(score), 3), 'duration': round(len(samples) / sample_rate, 3)}


def analyze_audio(ffmpeg: str, filename: str) -> dict:
    return detect_beats(decode_mono(ffmpeg, filename, lowpass=True))


def split_lyrics(text: str, duration: float, first_time: float = 0.0) -> list[dict]:
    """Make clearly approximate timings, grouped by lyric line; intended for manual correction/tapping."""
    rows = [re.findall(r'\S+', row) for row in text.splitlines()]
    word_count = sum(len(row) for row in rows)
    if not word_count:
        return []
    per_word = max(.04, (max(duration, first_time + word_count * .25) - first_time) / word_count)
    words = []
    j = 0
    for li, row in enumerate(rows):
        for word in row:
            t0 = first_time + j * per_word
            words.append({'w': word, 't0': round(t0, 3), 't1': round(t0 + per_word * .90, 3), 'line': li})
            j += 1
    return words


def import_slopcore_words(path: str) -> list[dict]:
    raw = json.loads(Path(path).read_text(encoding='utf-8-sig'))
    lines = raw.get('lines', [])
    if not isinstance(lines, list):
        raise ValueError('Expected Slopcore words.json with a lines array')
    result = []
    for li, line in enumerate(lines):
        for word in line.get('words', []):
            result.append({'w': str(word.get('w', '')), 't0': float(word['t0']),
                           't1': float(word['t1']), 'line': li})
    return result


def import_whisper_json(path: str) -> list[dict]:
    raw = json.loads(Path(path).read_text(encoding='utf-8-sig'))
    result = []
    for li, seg in enumerate(raw.get('segments', [])):
        for w in seg.get('words', []):
            result.append({'w': str(w.get('word', '')).strip(),
                           't0': float(w['start']), 't1': float(w['end']), 'line': li})
    return result


def import_subtitles(path: str) -> list[dict]:
    """Import LRC (line timestamps) or SRT (segment timestamps); distribute words within lines.
    This is not a forced alignment, and the user should refine word starts manually.
    """
    text = Path(path).read_text(encoding='utf-8-sig')
    result = []
    if Path(path).suffix.lower() == '.lrc':
        rows = []
        for line in text.splitlines():
            tags = re.findall(r'\[(\d+):(\d+(?:\.\d+)?)\]', line)
            lyric = re.sub(r'\[\d+:\d+(?:\.\d+)?\]', '', line).strip()
            for mm, ss in tags:
                if lyric:
                    rows.append((int(mm) * 60 + float(ss), lyric))
        rows.sort()
        for li, (start, lyric) in enumerate(rows):
            end = rows[li + 1][0] if li + 1 < len(rows) else start + 2.5
            pieces = lyric.split()
            for k, w in enumerate(pieces):
                a = start + (end - start) * k / len(pieces)
                b = start + (end - start) * (k + 1) / len(pieces)
                result.append({'w': w, 't0': round(a, 3), 't1': round(b, 3), 'line': li})
    else:
        blocks = re.split(r'\n\s*\n', text.strip().replace('\r\n', '\n'))
        time_re = re.compile(r'(\d+):(\d+):(\d+[\.,]\d+)\s*-->\s*(\d+):(\d+):(\d+[\.,]\d+)')
        for li, block in enumerate(blocks):
            m = time_re.search(block)
            if not m:
                continue
            vals = list(m.groups())
            start = int(vals[0]) * 3600 + int(vals[1]) * 60 + float(vals[2].replace(',', '.'))
            end = int(vals[3]) * 3600 + int(vals[4]) * 60 + float(vals[5].replace(',', '.'))
            phrase = block[m.end():].strip().replace('\n', ' ')
            phrase = re.sub(r'<[^>]+>', '', phrase)
            pieces = phrase.split()
            for k, w in enumerate(pieces):
                a = start + (end-start) * k / len(pieces)
                b = start + (end-start) * (k+1) / len(pieces)
                result.append({'w': w, 't0': round(a, 3), 't1': round(b, 3), 'line': li})
    if not result:
        raise ValueError('No timed lyrics found. Import .lrc, .srt, Slopcore words.json or Whisper JSON.')
    return result


def beat_position(t: float, bpm: float, t0: float) -> float:
    return (t-t0) * bpm / 60 if bpm > 0 else -100


def snap_beat(t: float, bpm: float, t0: float, division: int = 1) -> float:
    if bpm <= 0:
        return t
    return max(0, t0 + round((t - t0) * bpm / 60 * division) * 60 / bpm / division)


def import_beatmap(path: str) -> dict:
    """Use a repository beatmap.json without re-estimating the rhythm."""
    data = json.loads(Path(path).read_text(encoding='utf-8-sig'))
    bpm = float(data['bpm'])
    t0 = float(data['t0'])
    if not (30 <= bpm <= 300 and math.isfinite(t0)):
        raise ValueError('Invalid BPM or phase in beatmap.json')
    return {'bpm': bpm, 't0': t0}
