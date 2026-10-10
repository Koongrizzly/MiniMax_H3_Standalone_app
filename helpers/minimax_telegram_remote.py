# -*- coding: utf-8 -*-
from __future__ import annotations

"""Telegram remote control for the standalone GrizzlyMax MiniMax H3 app.

This module is intentionally self-contained.  It talks to Telegram with outbound
long polling and hands completed generation requests back to the existing GUI,
which then enqueues them in GrizzlyMax's own queue.
"""

import json
import mimetypes
import os
import queue
import re
import shutil
import subprocess
import time
import urllib.parse
import urllib.request
import uuid
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from PySide6 import QtCore

_IMAGE_EXTS = {'.png', '.jpg', '.jpeg', '.webp', '.bmp', '.tif', '.tiff'}
_VIDEO_EXTS = {'.mp4', '.mov', '.mkv', '.webm', '.avi', '.m4v'}
_AUDIO_EXTS = {'.mp3', '.wav', '.flac', '.m4a', '.aac', '.ogg', '.opus'}
_SAFE_TELEGRAM_BYTES = 49 * 1024 * 1024


def load_config(path: Path) -> Dict[str, Any]:
    try:
        data = json.loads(Path(path).read_text(encoding='utf-8'))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_config(path: Path, data: Dict[str, Any]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + '.tmp')
    tmp.write_text(json.dumps(dict(data or {}), indent=2, ensure_ascii=False), encoding='utf-8')
    os.replace(str(tmp), str(path))


class TelegramApi:
    def __init__(self, token: str, timeout: int = 35):
        self.token = str(token or '').strip()
        self.base = f'https://api.telegram.org/bot{self.token}'
        self.file_base = f'https://api.telegram.org/file/bot{self.token}'
        self.timeout = int(timeout)

    def _json_call(self, method: str, params: Optional[Dict[str, Any]] = None) -> Any:
        data = urllib.parse.urlencode({k: str(v) for k, v in (params or {}).items() if v is not None}).encode('utf-8')
        req = urllib.request.Request(self.base + '/' + method, data=data)
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            obj = json.loads(resp.read().decode('utf-8', errors='replace'))
        if not obj.get('ok'):
            raise RuntimeError(str(obj.get('description') or 'Telegram API call failed'))
        return obj.get('result')

    def get_me(self) -> Dict[str, Any]:
        return dict(self._json_call('getMe') or {})

    def get_updates(self, offset: int = 0, timeout: int = 25) -> List[Dict[str, Any]]:
        return list(self._json_call('getUpdates', {
            'offset': offset, 'timeout': timeout,
            'allowed_updates': json.dumps(['message'])
        }) or [])

    def send_message(self, chat_id: str, text: str) -> None:
        clean = str(text or '').strip() or 'Done.'
        while clean:
            chunk, clean = clean[:3900], clean[3900:]
            self._json_call('sendMessage', {'chat_id': chat_id, 'text': chunk})

    def get_file_path(self, file_id: str) -> str:
        info = dict(self._json_call('getFile', {'file_id': file_id}) or {})
        return str(info.get('file_path') or '')

    def download_file(self, file_path: str, destination: Path) -> Path:
        destination.parent.mkdir(parents=True, exist_ok=True)
        with urllib.request.urlopen(self.file_base + '/' + file_path, timeout=90) as resp, destination.open('wb') as out:
            shutil.copyfileobj(resp, out)
        return destination

    def send_file(self, chat_id: str, path: Path, caption: str = '') -> None:
        path = Path(path)
        if not path.is_file():
            raise FileNotFoundError(path)
        ext = path.suffix.lower()
        method = 'sendPhoto' if ext in _IMAGE_EXTS else 'sendVideo' if ext in _VIDEO_EXTS else 'sendAudio' if ext in _AUDIO_EXTS else 'sendDocument'
        field = {'sendPhoto': 'photo', 'sendVideo': 'video', 'sendAudio': 'audio'}.get(method, 'document')
        boundary = '----GrizzlyMaxTelegram' + uuid.uuid4().hex
        parts: List[bytes] = []

        def add_field(name: str, value: str) -> None:
            parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode())

        add_field('chat_id', str(chat_id))
        if caption:
            add_field('caption', str(caption)[:900])
        mime = mimetypes.guess_type(path.name)[0] or 'application/octet-stream'
        header = (f'--{boundary}\r\nContent-Disposition: form-data; name="{field}"; filename="{path.name}"\r\nContent-Type: {mime}\r\n\r\n').encode()
        parts.append(header + path.read_bytes() + b'\r\n')
        parts.append(f'--{boundary}--\r\n'.encode())
        req = urllib.request.Request(
            self.base + '/' + method,
            data=b''.join(parts),
            headers={'Content-Type': f'multipart/form-data; boundary={boundary}'},
        )
        with urllib.request.urlopen(req, timeout=240) as resp:
            obj = json.loads(resp.read().decode('utf-8', errors='replace'))
        if not obj.get('ok'):
            raise RuntimeError(str(obj.get('description') or 'Telegram file upload failed'))


def _ffmpeg_path(root: Path) -> Optional[Path]:
    for name in ('ffmpeg.exe', 'ffmpeg'):
        p = root / 'presets' / 'bin' / name
        if p.is_file():
            return p
    found = shutil.which('ffmpeg')
    return Path(found) if found else None


def make_telegram_video(root: Path, source: Path, max_bytes: int = _SAFE_TELEGRAM_BYTES) -> Path:
    source = Path(source)
    if source.stat().st_size <= max_bytes:
        return source
    ffmpeg = _ffmpeg_path(Path(root))
    if ffmpeg is None:
        return source
    out_dir = Path(root) / 'temp' / 'telegram' / 'send'
    out_dir.mkdir(parents=True, exist_ok=True)
    # Telegram copy only; never replace the generated original.
    attempts = [
        (28, 1280),
        (31, 960),
        (34, 832),
        (36, 720),
        (38, 640),
    ]
    last = source
    for crf, max_w in attempts:
        dest = out_dir / f'{source.stem}_telegram_{max_w}w_crf{crf}.mp4'
        vf = f"scale='min({max_w},iw)':-2"
        cmd = [str(ffmpeg), '-y', '-i', str(source), '-vf', vf,
               '-c:v', 'libx264', '-preset', 'veryfast', '-crf', str(crf),
               '-c:a', 'aac', '-b:a', '128k', '-movflags', '+faststart', str(dest)]
        cp = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0) if os.name == 'nt' else 0)
        if cp.returncode == 0 and dest.is_file():
            last = dest
            if dest.stat().st_size <= max_bytes:
                return dest
    return last


class TelegramBridgeThread(QtCore.QThread):
    incoming = QtCore.Signal(object)
    statusChanged = QtCore.Signal(str)

    def __init__(self, root: str, token: str, allowed_user_ids: Iterable[str], parent=None):
        super().__init__(parent)
        self.root = Path(root).resolve()
        self.token = str(token or '').strip()
        self.allowed = {str(x).strip() for x in allowed_user_ids if str(x).strip()}
        self._stop_flag = False
        self._outbox: 'queue.Queue[tuple]' = queue.Queue()
        self._offset = 0
        self._api: Optional[TelegramApi] = None

    def stop(self) -> None:
        self._stop_flag = True

    def send_text(self, chat_id: str, text: str) -> None:
        self._outbox.put(('text', str(chat_id), str(text), ''))

    def send_file(self, chat_id: str, path: str, caption: str = '') -> None:
        self._outbox.put(('file', str(chat_id), str(path), str(caption)))

    def _flush_outbox(self) -> None:
        if self._api is None:
            return
        for _ in range(20):
            try:
                kind, chat_id, payload, caption = self._outbox.get_nowait()
            except queue.Empty:
                break
            try:
                if kind == 'file':
                    p = Path(payload)
                    send_path = make_telegram_video(self.root, p) if p.suffix.lower() in _VIDEO_EXTS else p
                    extra = ''
                    if send_path != p:
                        extra = f'\nTelegram copy: {send_path.stat().st_size / (1024*1024):.1f} MB'
                    self._api.send_file(chat_id, send_path, (caption + extra).strip())
                else:
                    self._api.send_message(chat_id, payload)
            except Exception as exc:
                self.statusChanged.emit(f'Telegram send error: {exc}')

    def _allowed(self, message: Dict[str, Any]) -> bool:
        uid = str((message.get('from') or {}).get('id') or '')
        chat_id = str((message.get('chat') or {}).get('id') or '')
        # Private Telegram chats normally use the same numeric value for user ID
        # and chat ID. Accept either so users can paste the ID they can most easily
        # retrieve. Do not silently ignore a mismatch: surface the received ID in
        # GrizzlyMax so pairing mistakes are immediately obvious.
        ok = bool((uid and uid in self.allowed) or (chat_id and chat_id in self.allowed))
        if not ok and (uid or chat_id):
            shown = uid or chat_id
            self.statusChanged.emit(
                f'Telegram message ignored from user ID {shown}. Add this ID to Allowed user IDs and reconnect.'
            )
        return ok

    def _attachment_from_message(self, message: Dict[str, Any], chat_id: str) -> List[Dict[str, str]]:
        if self._api is None:
            return []
        item = None; kind = ''; filename = ''
        if message.get('photo'):
            photos = list(message.get('photo') or [])
            item = photos[-1] if photos else None; kind = 'image'; filename = 'photo.jpg'
        elif message.get('video'):
            item = dict(message.get('video') or {}); kind = 'video'; filename = str(item.get('file_name') or 'video.mp4')
        elif message.get('audio'):
            item = dict(message.get('audio') or {}); kind = 'audio'; filename = str(item.get('file_name') or 'audio.mp3')
        elif message.get('voice'):
            item = dict(message.get('voice') or {}); kind = 'audio'; filename = 'voice.ogg'
        elif message.get('document'):
            item = dict(message.get('document') or {}); filename = str(item.get('file_name') or 'document.bin')
            ext = Path(filename).suffix.lower()
            kind = 'image' if ext in _IMAGE_EXTS else 'video' if ext in _VIDEO_EXTS else 'audio' if ext in _AUDIO_EXTS else 'file'
        if not item:
            return []
        fid = str(item.get('file_id') or '')
        if not fid:
            return []
        remote = self._api.get_file_path(fid)
        if not remote:
            return []
        safe = re.sub(r'[^A-Za-z0-9._-]+', '_', Path(filename).name)[:120] or ('upload' + Path(remote).suffix)
        dest = self.root / 'temp' / 'telegram' / str(chat_id) / f'{int(time.time())}_{uuid.uuid4().hex[:6]}_{safe}'
        self._api.download_file(remote, dest)
        return [{'kind': kind, 'path': str(dest.resolve()), 'name': safe}]

    def run(self) -> None:
        if not self.token:
            self.statusChanged.emit('Telegram disabled: bot token is empty.')
            return
        if not self.allowed:
            self.statusChanged.emit('Telegram disabled: add at least one allowed Telegram user ID.')
            return
        try:
            self._api = TelegramApi(self.token)
            me = self._api.get_me()
            self.statusChanged.emit('Telegram connected as @' + str(me.get('username') or me.get('first_name') or 'bot'))
        except Exception as exc:
            self.statusChanged.emit(f'Telegram connection failed: {exc}')
            return
        while not self._stop_flag:
            try:
                self._flush_outbox()
                updates = self._api.get_updates(self._offset, 5)
                for upd in updates:
                    self._offset = max(self._offset, int(upd.get('update_id') or 0) + 1)
                    msg = dict(upd.get('message') or {})
                    if not msg or not self._allowed(msg):
                        continue
                    chat_id = str((msg.get('chat') or {}).get('id') or '')
                    user_id = str((msg.get('from') or {}).get('id') or '')
                    text = str(msg.get('text') or msg.get('caption') or '').strip()
                    attachments = self._attachment_from_message(msg, chat_id)
                    self.incoming.emit({'chat_id': chat_id, 'user_id': user_id, 'text': text, 'attachments': attachments})
                self._flush_outbox()
            except Exception as exc:
                msg = str(exc)
                if '409' in msg or 'Conflict' in msg or 'terminated by other getUpdates' in msg:
                    self.statusChanged.emit(
                        'Telegram polling conflict: another app/process is using getUpdates for this same bot token. '
                        'Stop the other Telegram bot instance or use a separate bot token.'
                    )
                else:
                    self.statusChanged.emit(f'Telegram polling error: {exc}')
                for _ in range(10):
                    if self._stop_flag:
                        break
                    time.sleep(0.5)
        try:
            self._flush_outbox()
        except Exception:
            pass
        self.statusChanged.emit('Telegram stopped.')


class GrizzlyTelegramRemote(QtCore.QObject):
    """Small deterministic wizard for the standalone Generation tab."""

    statusChanged = QtCore.Signal(str)

    def __init__(self, host, root: Path, parent=None):
        super().__init__(parent or host)
        self.host = host
        self.root = Path(root).resolve()
        self.thread: Optional[TelegramBridgeThread] = None
        self.wizards: Dict[str, Dict[str, Any]] = {}

    def running(self) -> bool:
        return bool(self.thread is not None and self.thread.isRunning())

    def start(self, token: str, allowed_user_ids: Iterable[str]) -> None:
        self.stop()
        self.thread = TelegramBridgeThread(str(self.root), token, allowed_user_ids, self)
        self.thread.statusChanged.connect(self.statusChanged.emit)
        self.thread.incoming.connect(self._incoming)
        self.thread.finished.connect(self._thread_finished)
        self.thread.start()

    def stop(self) -> None:
        if self.thread is not None:
            self.thread.stop()
            self.thread.wait(1500)
            self.thread = None

    def _thread_finished(self) -> None:
        if self.thread is not None and not self.thread.isRunning():
            self.thread = None

    def send_text(self, chat_id: str, text: str) -> None:
        if self.thread is not None:
            self.thread.send_text(chat_id, text)

    def send_result(self, chat_id: str, path: str, caption: str = '') -> None:
        if self.thread is not None:
            self.thread.send_file(chat_id, path, caption or 'GrizzlyMax MiniMax H3 result')

    def _settings_summary(self) -> str:
        try:
            s = self.host.settings_dict()
            mode = int(s.get('mode', 0))
            mode_name = ('Text to video', 'Image/video to video', 'References to video')[max(0, min(2, mode))]
            w, h = self.host._current_resolution()
            model = 'Hybrid' if bool(s.get('use_hybrid_model')) else ('Ref2VA' if mode == 2 else 'FL2VA')
            loras = [Path(x.get('path','')).name for x in s.get('loras', []) if x.get('path') and float(x.get('strength', 0) or 0) != 0]
            frames = int(s.get('frames') or 0)
            duration = (frames / 24.0) if frames else 0.0
            return (f'{mode_name}\nModel: {model}\nResolution: {w} × {h}\n'
                    f'Duration: {duration:.2f} s ({frames} frames)\nSteps: {s.get("steps")}\n'
                    f'Seed: {s.get("seed")}\nSpectrum: {"on" if s.get("spectrum_enabled") else "off"}\n'
                    f'LoRA: {", ".join(loras) if loras else "none"}')
        except Exception:
            return 'Current saved Generation-tab settings'

    def _help(self) -> str:
        return (
            'GrizzlyMax MiniMax H3 remote\n\n'
            '/t2v - Text to video\n'
            '/i2v - Image/video to video (FL2VA)\n'
            '/ref2v - References to video (Ref2VA)\n'
            '/status - Current standalone queue\n'
            '/queue - Current standalone queue\n'
            '/cancel - Cancel current GrizzlyMax job\n'
            '/last - Send last finished result\n'
            '/stop - Cancel the current Telegram wizard\n'
            '/help - Show this help'
        )

    @QtCore.Slot(object)
    def _incoming(self, payload: object) -> None:
        d = dict(payload or {})
        chat = str(d.get('chat_id') or '')
        text = str(d.get('text') or '').strip()
        attachments = list(d.get('attachments') or [])
        low = text.lower()
        if low in {'/start', '/help', 'help'}:
            self.wizards.pop(chat, None); self.send_text(chat, self._help()); return
        if low in {'/stop', 'stop', 'cancel wizard'}:
            self.wizards.pop(chat, None); self.send_text(chat, 'Telegram generation setup cancelled.'); return
        if low in {'/status', '/queue'}:
            self.send_text(chat, self.host._telegram_queue_summary()); return
        if low == '/cancel':
            self.send_text(chat, self.host._telegram_cancel_current()); return
        if low == '/last':
            p = self.host._telegram_last_result()
            if p:
                self.send_result(chat, p, 'Last GrizzlyMax result')
            else:
                self.send_text(chat, 'No finished GrizzlyMax result was found.')
            return
        if low in {'/t2v', 't2v', 'create a video', 'create video', 'make a video', 'make video', 'generate a video', 'generate video'}:
            self._begin(chat, 0); return
        if low in {'/i2v', 'i2v', '/fl2va', 'fl2va', 'image to video', 'video to video'}:
            self._begin(chat, 1); return
        if low in {'/ref2v', 'ref2v', '/ref2va', 'ref2va', 'references to video', 'reference to video'}:
            self._begin(chat, 2); return

        state = self.wizards.get(chat)
        if not state:
            self.send_text(chat, 'Use /t2v, /i2v or /ref2v to create a clip. /help shows all commands.')
            return
        self._advance(chat, state, text, attachments)

    def _begin(self, chat: str, mode: int) -> None:
        state = {'mode': mode, 'phase': 'prompt', 'prompt': '', 'settings_mode': 'saved', 'attachments': [],
                 'seed': None, 'resolution': '', 'steps': None, 'frames': None, 'duration_request': ''}
        self.wizards[chat] = state
        mode_name = ('Text to video', 'Image/video to video', 'References to video')[mode]
        extra = ''
        if mode == 1:
            extra = '\nAfter the prompt I will ask you to upload the image/video input.'
        elif mode == 2:
            extra = '\nAfter the prompt you can upload one or more reference images/videos/audio samples.'
        self.send_text(chat, f'{mode_name}\n\nSend the prompt for the clip.{extra}\n\nSend /stop at any time to cancel.')

    def _advance(self, chat: str, s: Dict[str, Any], text: str, attachments: list) -> None:
        phase = str(s.get('phase') or '')
        low = str(text or '').strip().lower()
        if phase == 'prompt':
            if not str(text or '').strip():
                self.send_text(chat, 'Send the prompt as text.'); return
            s['prompt'] = str(text).strip()
            if int(s['mode']) == 0:
                s['phase'] = 'settings_choice'
                self.send_text(chat, 'Use the Generation-tab settings currently saved in GrizzlyMax? Reply `saved` or `custom`.')
            else:
                s['phase'] = 'inputs'
                if int(s['mode']) == 1:
                    self.send_text(chat, 'Upload an image for First frame, or upload a video to Continue from. When the upload arrives I will continue automatically.')
                else:
                    self.send_text(chat, 'Upload reference image(s), video(s), and/or audio sample(s). You may send several messages. Reply `done` when all references are uploaded.')
            return

        if phase == 'inputs':
            if attachments:
                s['attachments'].extend(attachments)
                if int(s['mode']) == 1:
                    valid = [a for a in s['attachments'] if a.get('kind') in {'image', 'video'}]
                    if not valid:
                        self.send_text(chat, 'That file is not an image or video. Upload the FL2VA input.'); return
                    s['attachments'] = valid[:1]
                    s['phase'] = 'settings_choice'
                    a = s['attachments'][0]
                    self.send_text(chat, f'Received {a.get("kind")}: {a.get("name")}\n\nUse the Generation-tab settings currently saved in GrizzlyMax? Reply `saved` or `custom`.')
                    return
                counts = {k: sum(1 for a in s['attachments'] if a.get('kind') == k) for k in ('image','video','audio')}
                self.send_text(chat, f'References received: {counts["image"]} image(s), {counts["video"]} video(s), {counts["audio"]} audio sample(s). Send more, or reply `done`.')
                return
            if int(s['mode']) == 2 and low == 'done':
                if not s['attachments']:
                    self.send_text(chat, 'Upload at least one reference first.'); return
                s['phase'] = 'settings_choice'
                self.send_text(chat, 'Use the Generation-tab settings currently saved in GrizzlyMax? Reply `saved` or `custom`.')
                return
            self.send_text(chat, 'Upload the requested media file' + ('s, or reply `done`.' if int(s['mode']) == 2 else '.'))
            return

        if phase == 'settings_choice':
            if low in {'saved', 'save', 'current', 'use saved', 'yes'}:
                s['settings_mode'] = 'saved'; s['phase'] = 'confirm'; self._show_confirm(chat, s); return
            if low in {'custom', 'own', 'change', 'no'}:
                s['settings_mode'] = 'custom'; s['phase'] = 'custom_resolution'
                self.send_text(chat, 'Resolution? Examples: `832x448`, `960x544`, `1280x704`, `1344x576`, `1792x768`, or `saved`.')
                return
            self.send_text(chat, 'Reply `saved` or `custom`.'); return

        if phase == 'custom_resolution':
            s['resolution'] = str(text or '').strip()
            s['phase'] = 'custom_seed'; self.send_text(chat, 'Seed? Send `-1` for random, or any integer.'); return
        if phase == 'custom_seed':
            try: s['seed'] = int(str(text).strip())
            except Exception:
                self.send_text(chat, 'Seed must be an integer, for example `-1` or `12345`.'); return
            s['phase'] = 'custom_steps'; self.send_text(chat, 'Steps? Send a number such as `4`, `9`, `12`, or reply `saved`.'); return
        if phase == 'custom_steps':
            if low != 'saved':
                try: s['steps'] = max(1, int(str(text).strip()))
                except Exception:
                    self.send_text(chat, 'Steps must be a whole number, or reply `saved`.'); return
            s['phase'] = 'custom_duration'
            self.send_text(chat, 'Duration? Send seconds such as `10` or `10s`, exact frames such as `243 frames`, or reply `saved`. I will snap seconds to the nearest valid MiniMax H3 frame count.')
            return
        if phase == 'custom_duration':
            if low == 'saved':
                s['frames'] = None
                s['duration_request'] = 'saved'
            else:
                try:
                    frames, requested = self.host._telegram_parse_duration(text)
                except Exception as exc:
                    self.send_text(chat, str(exc) or 'Send a duration such as `10`, `10s`, `243 frames`, or `saved`.')
                    return
                s['frames'] = int(frames)
                s['duration_request'] = str(requested or text or '').strip()
            s['phase'] = 'confirm'; self._show_confirm(chat, s); return

        if phase == 'confirm':
            if low in {'yes', 'y', 'start', 'go', 'confirm', 'generate', 'do it'}:
                ok, message, job = self.host._telegram_enqueue_generation(dict(s), chat)
                self.wizards.pop(chat, None)
                self.send_text(chat, message)
                return
            if low in {'no', 'n', 'cancel', 'stop'}:
                self.wizards.pop(chat, None); self.send_text(chat, 'Generation cancelled.'); return
            self.send_text(chat, 'Reply `yes` to add this to the GrizzlyMax queue, or `no` to cancel.')

    def _show_confirm(self, chat: str, s: Dict[str, Any]) -> None:
        mode = int(s.get('mode', 0))
        mode_name = ('Text to video', 'Image/video to video', 'References to video')[mode]
        lines = [f'About to create: {mode_name}', '']
        if s.get('settings_mode') == 'saved':
            lines += ['Settings: current saved Generation-tab settings', self._settings_summary()]
        else:
            lines += [f'Resolution: {s.get("resolution") or "saved"}', f'Seed: {s.get("seed")}', f'Steps: {s.get("steps") if s.get("steps") is not None else "saved"}']
            if s.get('frames') is not None:
                frames = int(s.get('frames'))
                req = str(s.get('duration_request') or '').strip()
                suffix = f' (requested {req})' if req else ''
                lines += [f'Duration: {frames / 24.0:.3f} s • {frames} frames{suffix}']
            else:
                lines += ['Duration: saved']
            try:
                current = self.host.settings_dict()
                lines += [f'Hybrid: {"on" if current.get("use_hybrid_model") else "off"}',
                          f'Spectrum forecasting: {"on" if current.get("spectrum_enabled") else "off"}']
                loras = [Path(x.get('path','')).name for x in current.get('loras', []) if x.get('path') and float(x.get('strength', 0) or 0) != 0]
                lines += [f'LoRA: {", ".join(loras) if loras else "none"}']
            except Exception:
                pass
        if s.get('attachments'):
            lines.append('Inputs: ' + ', '.join(str(a.get('name') or a.get('kind')) for a in s['attachments']))
        prompt = str(s.get('prompt') or '').strip()
        lines += ['', 'Prompt:', prompt[:1200], '', 'Reply `yes` to queue it, or `no` to cancel.']
        self.send_text(chat, '\n'.join(lines))
