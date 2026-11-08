"""Background tasks. No cloud calls, no registry, no persistent implicit settings."""
from __future__ import annotations

import json
import subprocess
import threading
import sys
import tempfile
from pathlib import Path

from PySide6.QtCore import QThread, Signal
from PySide6.QtGui import QImage, QPainter

from .core import ROOT, _windows_no_console, analyze_audio
from .paint import paint_effects
from .render_audio import prepare_render_audio
from .video_distortion import needs_video, apply_video_effects, stutter_target


class BeatWorker(QThread):
    ready = Signal(object)
    failed = Signal(str)

    def __init__(self, ffmpeg: str, audio: str):
        super().__init__()
        self.ffmpeg, self.audio = ffmpeg, audio

    def run(self):
        try:
            self.ready.emit(analyze_audio(self.ffmpeg, self.audio))
        except Exception as e:
            self.failed.emit(str(e))


class WhisperWorker(QThread):
    ready = Signal(object)
    failed = Signal(str)

    def __init__(self, input_audio: str, model_dir: str, device: str = 'cpu', compute_type: str = 'int8'):
        super().__init__()
        self.input_audio, self.model_dir = input_audio, model_dir
        self.device, self.compute_type = device, compute_type

    def run(self):
        try:
            # Prefer the shared GrizzlyMax Whisper environment when embedded.
            grizzly_root = Path(__file__).resolve().parents[2]
            shared = grizzly_root / 'environments/.whisper/Scripts/python.exe'
            runner = grizzly_root / 'presets/extra_env/shared_whisper_transcribe.py'
            if shared.is_file() and runner.is_file():
                with tempfile.TemporaryDirectory(prefix='beatsync_whisper_') as temp:
                    out = Path(temp) / 'words.json'
                    proc = subprocess.run([str(shared), str(runner), '--audio', self.input_audio,
                        '--model', self.model_dir, '--output', str(out), '--device', self.device,
                        '--compute-type', self.compute_type], capture_output=True, text=True,
                        timeout=1800, **_windows_no_console())
                    if proc.returncode:
                        raise RuntimeError('Shared Whisper failed: ' + (proc.stderr or proc.stdout)[-1500:])
                    self.ready.emit(json.loads(out.read_text(encoding='utf-8')))
                    return
            from faster_whisper import WhisperModel
            model = Path(self.model_dir)
            if not (model / 'model.bin').is_file() or not (model / 'config.json').is_file():
                raise RuntimeError(f'Not a Faster-Whisper/CTranslate2 model folder: {model}. Expected model.bin and config.json.')
            # CPU INT8 is the safe default. CUDA requires compatible installed runtime libraries.
            engine = WhisperModel(str(model), device=self.device, compute_type=self.compute_type,
                                  local_files_only=True)
            segments, _info = engine.transcribe(self.input_audio, beam_size=5,
                                                word_timestamps=True,
                                                condition_on_previous_text=False,
                                                vad_filter=False)
            rows = []
            for li, seg in enumerate(segments):
                for w in seg.words or []:
                    token = w.word.strip()
                    if token:
                        rows.append({'w': token, 't0': round(w.start, 3),
                                     't1': round(w.end, 3), 'line': li})
            if not rows:
                raise ValueError('No speech/lyrics recognized. Try a clean vocal stem or import manual timings.')
            self.ready.emit(rows)
        except Exception as exc:
            self.failed.emit(str(exc))


class WhisperCppWorker(QThread):
    """Local whisper.cpp CLI invocation; no Python whisper dependency."""
    ready = Signal(object)
    failed = Signal(str)

    def __init__(self, audio, model, executable, ffmpeg):
        super().__init__()
        self.audio, self.model, self.executable, self.ffmpeg = audio, model, executable, ffmpeg

    def run(self):
        import tempfile
        try:
            from .whisper_cpp import extract_words
            with tempfile.TemporaryDirectory(prefix='beatsync_whisper_') as tmp:
                wav = str(Path(tmp) / 'audio.wav')
                flags = _windows_no_console()
                convert = subprocess.run([self.ffmpeg, '-nostdin', '-y', '-hide_banner', '-loglevel', 'error',
                    '-i', self.audio, '-vn', '-ac', '1', '-ar', '16000', '-c:a', 'pcm_s16le', wav],
                    capture_output=True, text=True, **flags)
                if convert.returncode:
                    raise RuntimeError('FFmpeg audio conversion failed: ' + convert.stderr[-1800:])
                output = str(Path(tmp) / 'words')
                cmd = [self.executable, '-m', self.model, '-f', wav, '-ojf', '-of', output, '-ml', '1']
                result = subprocess.run(cmd, capture_output=True, text=True, **flags)
                if result.returncode:
                    raise RuntimeError('whisper.cpp failed: ' + (result.stderr or result.stdout)[-2400:])
                result_file = Path(output + '.json')
                if not result_file.is_file():
                    raise RuntimeError('whisper.cpp did not create expected JSON output. Ensure your executable supports -ojf (JSON full).')
                words = extract_words(json.loads(result_file.read_text(encoding='utf-8')))
                if not words:
                    raise RuntimeError('No timed words returned. Try another model or a clean vocal stem.')
                self.ready.emit(words)
        except Exception as exc:
            self.failed.emit(str(exc))


class RenderWorker(QThread):
    progress = Signal(int, int)
    done = Signal(str)
    failed = Signal(str)
    cancelled = Signal()
    audio_analyzed = Signal(object, object)
    analysis_status = Signal(str)

    def __init__(self, project: dict, ffmpeg: str, destination: str):
        super().__init__()
        self.project = project
        self.ffmpeg = ffmpeg
        self.destination = destination
        self.stop_now = False
        self._proc = None

    def cancel(self):
        self.stop_now = True

    def run(self):
        p = self.project
        out = Path(self.destination)
        width, height = int(p['width']), int(p['height'])
        opts = p.get('options', {})
        fps = int(opts.get('fps', 24))
        count = int(round(float(p.get('duration', 0)) * fps))
        if count < 1:
            self.failed.emit('Video duration is missing. Please select a valid source video.')
            return
        video = str(p['video'])
        music = str(p.get('audio', ''))
        if not Path(video).is_file():
            self.failed.emit('Source video no longer exists: ' + video)
            return
        if music and not Path(music).is_file():
            self.failed.emit('Selected replacement audio does not exist: ' + music)
            return
        if self.stop_now:
            self.cancelled.emit()
            return
        try:
            refreshed = prepare_render_audio(p, self.ffmpeg, self.analysis_status.emit)
        except Exception as exc:
            self.failed.emit('Cannot prepare audio-reactive effects: ' + str(exc))
            return
        if self.stop_now:
            self.cancelled.emit()
            return
        if refreshed is not None:
            self.audio_analyzed.emit(*refreshed)
        out.parent.mkdir(parents=True, exist_ok=True)
        process_video = needs_video(p)
        cmd = [self.ffmpeg, '-hide_banner', '-nostdin', '-y', '-loglevel', 'error',
               '-i', video,
               '-f', 'rawvideo', '-pixel_format', 'bgra', '-video_size', f'{width}x{height}',
               '-framerate', str(fps), '-i', 'pipe:0']
        if music:
            cmd += ['-i', music]
        if process_video:
            # The raw input contains the processed source AND its original overlays.
            filters = '[1:v:0]format=yuv420p[vout]'
        else:
            filters = (f'[0:v:0]fps={fps},scale={width}:{height}:flags=lanczos,setsar=1[base];'
                       '[base][1:v:0]overlay=x=0:y=0:shortest=1:format=auto,'
                       'format=yuv420p[vout]')
        cmd += ['-filter_complex', filters, '-map', '[vout]']
        if music:
            cmd += ['-map', '2:a:0', '-af', 'apad']
        else:
            cmd += ['-map', '0:a:0?']
        encoder = str(opts.get('encoder', 'libx264'))
        crf = int(opts.get('crf', 18))
        if encoder == 'h264_nvenc':
            cmd += ['-c:v', 'h264_nvenc', '-preset', 'p5', '-rc', 'vbr',
                    '-cq', str(crf), '-b:v', '0']
        else:
            cmd += ['-c:v', 'libx264', '-preset', 'medium', '-crf', str(crf)]
        cmd += ['-pix_fmt', 'yuv420p', '-c:a', 'aac', '-b:a', '256k',
                '-r', str(fps), '-frames:v', str(count), '-t', f'{count/fps:.6f}',
                '-movflags', '+faststart', str(out)]
        # Portable render log to support debugging without any system-wide state.
        try:
            log = ROOT / 'logs' / 'render_latest.json'
            log.parent.mkdir(parents=True, exist_ok=True)
            log.write_text(json.dumps({'command': cmd, 'bpm': p.get('bpm'),
                      'first_beat': p.get('t0'), 'options': opts,
                      'words': len(p.get('words', [])), 'events': p.get('events', [])},
                      indent=2, ensure_ascii=False), encoding='utf-8')
        except OSError:
            pass
        err = []
        def read_stderr(proc):
            for raw in proc.stderr:
                err.append(raw.decode('utf-8', errors='replace').rstrip())
                if len(err) > 120:
                    del err[:60]
        decode_proc = None
        from .media_layers import VideoFrames
        media_provider = VideoFrames(self.ffmpeg, fps=fps)
        p['_media_frame_provider'] = media_provider
        try:
            if process_video:
                decoder_cmd = [self.ffmpeg, '-hide_banner', '-nostdin', '-loglevel', 'error',
                               '-i', video, '-an', '-vf',
                               f'fps={fps},scale={width}:{height}:flags=lanczos,setsar=1',
                               '-f', 'rawvideo', '-pix_fmt', 'bgra', 'pipe:1']
                decode_proc = subprocess.Popen(decoder_cmd, stdout=subprocess.PIPE,
                                               stderr=subprocess.DEVNULL, **_windows_no_console())
            self._proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE,
                                          stdout=subprocess.DEVNULL, bufsize=0, **_windows_no_console())
            reader = threading.Thread(target=read_stderr, args=(self._proc,), daemon=True)
            reader.start()
            transparent = QImage(width, height, QImage.Format.Format_ARGB32)
            freeze_frame = None
            freeze_time = None
            byte_count = width*height*4
            for i in range(count):
                if self.stop_now:
                    break
                t = i/fps
                if process_video:
                    raw = decode_proc.stdout.read(byte_count)
                    if len(raw) != byte_count:
                        raise RuntimeError(f'Source video decode ended at frame {i} of {count}')
                    source = QImage(raw,width,height,width*4,QImage.Format.Format_ARGB32).copy()
                    target = stutter_target(p,t,fps)
                    if target is not None:
                        if freeze_time != target or freeze_frame is None:
                            freeze_frame=source.copy()
                            freeze_time=target
                        source=freeze_frame.copy()
                    else:
                        freeze_frame=None
                        freeze_time=None
                    transparent=apply_video_effects(source,p,t)
                else:
                    transparent.fill(0)
                painter = QPainter(transparent)
                try:
                    paint_effects(painter, width, height, t, p)
                finally:
                    painter.end()
                # Format_ARGB32 on little-endian Windows is BGRA, matching ffmpeg's rawvideo input.
                self._proc.stdin.write(transparent.bits().tobytes())
                if i % max(1, fps // 2) == 0 or i == count - 1:
                    self.progress.emit(i + 1, count)
            if decode_proc is not None:
                decode_proc.stdout.close()
                decode_proc.wait(timeout=10)
            try:
                self._proc.stdin.close()
            except OSError:
                pass
            if self.stop_now:
                self._proc.terminate()
            ret = self._proc.wait(timeout=90)
            reader.join(timeout=2)
            if self.stop_now:
                out.unlink(missing_ok=True)
                self.cancelled.emit()
            elif ret:
                out.unlink(missing_ok=True)
                self.failed.emit('FFmpeg export failed:\n' + '\n'.join(err[-16:]))
            else:
                self.done.emit(str(out))
        except Exception as exc:
            try:
                if decode_proc and decode_proc.poll() is None:
                    decode_proc.kill()
                    decode_proc.wait(timeout=10)
            except Exception:
                pass
            try:
                if self._proc and self._proc.poll() is None:
                    self._proc.kill()
                    self._proc.wait(timeout=10)
            except Exception:
                pass
            out.unlink(missing_ok=True)
            self.failed.emit(f'Export stopped: {exc}\n' + '\n'.join(err[-12:]))
        finally:
            media_provider.close()
            p.pop('_media_frame_provider', None)

class MultibandWorker(QThread):
    ready = Signal(object)
    failed = Signal(str)

    def __init__(self, ffmpeg, audio):
        super().__init__()
        self.ffmpeg,self.audio=ffmpeg,audio

    def run(self):
        try:
            from .core import decode_mono
            from .audio_reactivity import extract_envelopes
            samples=decode_mono(self.ffmpeg,self.audio,sr=11025)
            self.ready.emit(extract_envelopes(samples))
        except Exception as exc:
            self.failed.emit(str(exc))
