"""Native offline Media Layers assets, shared by preview and render.

No global Qt settings or persistent decoder caches. Video frame readers are owned
by the calling preview/render worker; all timing is derived from project time.
"""
from __future__ import annotations

import json
import subprocess
from collections import OrderedDict
from pathlib import Path

from PySide6.QtGui import QImage

VIDEO_EXTENSIONS = {'.mp4', '.mov', '.mkv', '.webm', '.avi', '.m4v'}
IMAGE_EXTENSIONS = {'.png', '.jpg', '.jpeg', '.bmp', '.gif', '.webp'}


def is_video(path):
    return Path(str(path)).suffix.lower() in VIDEO_EXTENSIONS


def normalize_legacy(project):
    """Move existing PNG/GIF event entries to the media tab once, preserving fields."""
    project.setdefault('media_layers', [])
    remaining = []
    for event in project.get('events', []):
        if event.get('style') == 'Image / GIF':
            layer = dict(event)
            layer['path'] = layer.pop('text', '')
            layer.pop('style', None)
            layer.setdefault('name', Path(layer['path']).name)
            layer.setdefault('trim', 0.0)
            layer.setdefault('loop', True)
            project['media_layers'].append(layer)
        else:
            remaining.append(event)
    project['events'] = remaining
    return project


def media_duration(ffprobe, filename):
    if not ffprobe:
        return 0.0
    try:
        cmd = [str(ffprobe), '-v', 'error', '-show_entries', 'format=duration',
               '-of', 'default=noprint_wrappers=1:nokey=1', str(filename)]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=12)
        return max(0.0, float(result.stdout.strip())) if result.returncode == 0 else 0.0
    except (ValueError, OSError, subprocess.TimeoutExpired):
        return 0.0


def source_time(layer, timeline_t):
    """Return media-relative source time, or None if its clip isn't active."""
    start, end = float(layer.get('start', 0)), float(layer.get('end', 0))
    if not start <= timeline_t < end:
        return None
    speed = max(.01, float(layer.get('speed', 1)))
    position = float(layer.get('trim', 0)) + (timeline_t - start) * speed
    duration = float(layer.get('source_duration', 0))
    if duration > 0:
        trim = min(max(0.,float(layer.get('trim', 0))), duration)
        span = duration - trim
        if span <= 0:
            return None
        if bool(layer.get('loop', False)):
            position = trim + ((position-trim) % span)
        elif position >= duration:
            return None
    return position


class VideoFrames:
    """Render: sequential FFmpeg stdout decode. Preview: bounded seek cache.

    Render skips frames outside layer time and reopens decoders upon timeline
    discontinuity/loop. Processing is single threaded within each owner.
    """
    def __init__(self, ffmpeg, fps=24, preview=False):
        self.ffmpeg = str(ffmpeg or '')
        self.fps = max(1, int(fps))
        self.preview = preview
        self.cache = OrderedDict()
        self.streams = {}

    def close(self):
        for info in list(self.streams.values()):
            proc = info['proc']
            if proc.poll() is None:
                proc.kill()
            try:
                proc.stdout.close()
                proc.wait(timeout=2)
            except (OSError, subprocess.TimeoutExpired):
                pass
        self.streams.clear()
        self.cache.clear()

    def _start_stream(self, key, layer, timestamp):
        self._close_one(key)
        path = str(layer.get('path', ''))
        width = max(64, min(1280, int(layer.get('decode_width', 960))))
        # A fixed output canvas avoids variable frame sizes or dependence on probing.
        ratio = max(.1,min(10.,float(layer.get('source_ratio',16/9))))
        height = max(64, int(round(width/ratio/2))*2)
        vf = f'scale={width}:{height}:flags=bicubic,setsar=1,fps={self.fps}'
        args = [self.ffmpeg, '-hide_banner', '-nostdin', '-loglevel', 'error',
                '-ss', f'{max(0,timestamp):.5f}', '-i', path, '-an', '-sn',
                '-vf', vf, '-f', 'rawvideo', '-pix_fmt', 'bgra', 'pipe:1']
        kwargs = {'stdout': subprocess.PIPE, 'stderr': subprocess.DEVNULL,
                  'bufsize': width*height*4*2}
        if hasattr(subprocess, 'CREATE_NO_WINDOW'):
            kwargs['creationflags'] = subprocess.CREATE_NO_WINDOW
        proc = subprocess.Popen(args, **kwargs)
        self.streams[key] = {'proc':proc, 'width':width, 'height':height,
                             'next':timestamp, 'interval':1/self.fps, 'path':path}
        return self.streams[key]

    def _close_one(self,key):
        old = self.streams.pop(key,None)
        if old:
            proc=old['proc']
            if proc.poll() is None:
                proc.kill()
            try:
                proc.stdout.close()
                proc.wait(timeout=2)
            except (OSError,subprocess.TimeoutExpired):
                pass

    def frame(self, key, layer, source_t):
        path = str(layer.get('path',''))
        if not path or not Path(path).is_file() or not self.ffmpeg:
            return None
        if self.preview:
            # Quantized preview frames, bounded to 20 images, avoids excessive seeks.
            source_t = round(source_t * 8) / 8
            cache_key = (path, source_t, int(layer.get('decode_width',960)))
            if cache_key in self.cache:
                self.cache.move_to_end(cache_key)
                return self.cache[cache_key]
            self._start_stream(key,layer,source_t)
            result = self._read(key)
            self._close_one(key)
            if result is not None:
                self.cache[cache_key]=result
                while len(self.cache)>12:
                    self.cache.popitem(last=False)
            return result
        stream = self.streams.get(key)
        # FPS decoding may require a single frame skip for non-integral playback speeds.
        if (stream is None or stream['path'] != path or
            source_t < stream['next']-0.02 or source_t-stream['next'] > 1.0):
            stream = self._start_stream(key,layer,source_t)
        output = None
        while stream['next'] <= source_t + 0.015:
            output=self._read(key)
            if output is None:
                break
            stream['next'] += stream['interval']
        if output is not None:
            stream['last_image'] = output
        return stream.get('last_image')

    def _read(self,key):
        info = self.streams.get(key)
        if not info:
            return None
        w,h=info['width'],info['height']
        need=w*h*4
        raw=bytearray()
        while len(raw)<need:
            chunk=info['proc'].stdout.read(need-len(raw))
            if not chunk:
                return None
            raw.extend(chunk)
        return QImage(bytes(raw),w,h,w*4,QImage.Format.Format_ARGB32).copy()
