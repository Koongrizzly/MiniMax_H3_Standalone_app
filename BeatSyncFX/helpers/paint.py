"""Deterministic native-Qt compositing. All visuals are functions of timestamp and project JSON.

Render and preview use this same function. No browser, cloud API, model or retained animation state.
"""
from __future__ import annotations

import math
import colorsys
from pathlib import Path
from functools import lru_cache
from collections import defaultdict

from .lyric_visibility import active_words, visible_at
from .audio_reactivity import reaction_transform, energy_at, spectrum_at

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QPainter, QPainterPath, QPen, QImage, QTransform, QPolygonF

from .animated_images import decode_frames, frame_index
from .motion_math import transform
from .frame_fx import FRAME_STYLES, draw_frame


def color(s: str, alpha=1.0) -> QColor:
    c = QColor(s)
    if not c.isValid():
        c = QColor('#ffffff')
    c.setAlphaF(max(0.0, min(1.0, alpha)))
    return c


def clamp(x, a=0.0, b=1.0):
    return min(b, max(a, x))


def ease_out_cubic(v: float) -> float:
    v = clamp(v)
    return 1.0 - (1.0 - v) ** 3


def event_fade(event: dict, t: float, start: float, end: float, default_in: float = 0.28, default_out: float = 0.28) -> float:
    fade_in = max(0.0, float(event.get('fade_in', default_in)))
    fade_out = max(0.0, float(event.get('fade_out', default_out)))
    alpha_in = 1.0 if fade_in <= 1e-6 else clamp((t - start) / fade_in)
    alpha_out = 1.0 if fade_out <= 1e-6 else clamp((end - t) / fade_out)
    return clamp(alpha_in * alpha_out)


def pulse(t: float, bpm: float, t0: float, decay: float, every: int = 1) -> tuple[float, int]:
    if bpm <= 0 or t < t0:
        return 0.0, -1
    beat = (t - t0) * bpm / 60.0
    num = math.floor(beat + 1e-8)
    if num % max(1, every):
        return 0.0, num
    d = (beat - num) * 60 / bpm
    v = 1.0 - d / max(.01, decay)
    return max(0.0, v) ** 2, num


def _font(family: str, px: float, bold=True) -> QFont:
    f = QFont(family or 'Arial')
    f.setPixelSize(max(6, int(px)))
    f.setWeight(QFont.Weight.Bold if bold else QFont.Weight.Medium)
    f.setHintingPreference(QFont.HintingPreference.PreferFullHinting)
    return f


def _text_path(text: str, family: str, px: float) -> tuple[QPainterPath, float, float]:
    font = _font(family, px)
    fm = QFontMetricsF(font)
    path = QPainterPath()
    # Baseline set to +ascent: bounds may be slightly different for display fonts.
    path.addText(QPointF(0, fm.ascent()), font, text)
    return path, fm.horizontalAdvance(text), fm.height()


def draw_text(p: QPainter, text: str, x: float, y: float, size: float, family: str,
              fill: str, outline: str = '#10121a', opacity=1.0, scale=1.0,
              tilt=0.0, anchor='center', backing=False, width_scale=1.0, tracking=0.0, outline_only=False):
    if not text or opacity <= 0:
        return
    size = max(9, size)
    path, tw, th = _text_path(text, family, size)
    if abs(tracking) > 0.001:
        font = _font(family, size)
        font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, tracking)
        fm = QFontMetricsF(font)
        path = QPainterPath()
        path.addText(QPointF(0, fm.ascent()), font, text)
        tw, th = fm.horizontalAdvance(text), fm.height()
    p.save()
    p.setOpacity(clamp(opacity))
    p.translate(x, y)
    if tilt:
        p.rotate(tilt)
    p.scale(scale * max(0.1, width_scale), scale)
    dx = {'center': -tw / 2, 'left': 0, 'right': -tw}.get(anchor, -tw / 2)
    if backing:
        bg = QRectF(dx - 12, -th / 2 - 10, tw + 24, th + 16)
        p.setBrush(color('#080e17', 0.66)); p.setPen(Qt.PenStyle.NoPen)
        p.drawRoundedRect(bg, 8, 8)
    p.translate(dx, -th / 2)
    p.setPen(QPen(color(outline), max(1.0, size * 0.076), Qt.PenStyle.SolidLine,
                  Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
    p.setBrush(Qt.BrushStyle.NoBrush if outline_only else color(fill))
    p.drawPath(path)
    p.restore()


BEAT_EFFECTS = ('Border pulse', 'Side bars', 'Circle kick', 'Full-frame flash',
                'Beat grid', 'Beat rays', 'Beat diamonds', 'Beat scanline',
                'Horizontal Tearing', 'RGB Split', 'Frame Stutter',
                'Signal Dropout', 'Block Corruption', 'Impact Zoom')


def _draw_beats(p: QPainter, w: int, h: int, t: float, project: dict, override=None):
    o = override if override is not None else project.get('options', {})
    kind = o.get('beat_style', 'Off')
    if kind == 'Off' or kind in ('Horizontal Tearing', 'RGB Split', 'Frame Stutter',
                                 'Signal Dropout', 'Block Corruption', 'Impact Zoom'):
        return
    strength, n = pulse(t, float(project.get('bpm', 120)), float(project.get('t0', 0)),
                        float(o.get('beat_decay', .24)), int(o.get('beat_every', 1)))
    if strength < 0.004:
        return
    alpha = strength * float(o.get('beat_intensity', 60)) / 100
    chosen = o.get('beat_color', '#22ddee')
    if str(o.get('beat_palette', 'Solid')) == 'Rainbow':
        r,g,b = colorsys.hsv_to_rgb((t * .18 + n * .047) % 1.0, 0.90, 1.0)
        chosen = '#%02X%02X%02X' % (int(r*255), int(g*255), int(b*255))
    c = color(chosen, clamp(alpha))
    sf = min(w / 1280, h / 720)
    p.save()
    if kind == 'Full-frame flash':
        p.fillRect(QRectF(0, 0, w, h), c)
    elif kind == 'Border pulse':
        pen = QPen(c, max(2, 18 * sf * (.8 + strength)))
        p.setBrush(Qt.BrushStyle.NoBrush); p.setPen(pen)
        inset = pen.widthF() / 2
        p.drawRect(QRectF(inset, inset, w - 2 * inset, h - 2 * inset))
        if n % 4 == 0:
            p.fillRect(QRectF(0, 0, w, max(4, 14 * sf)), c)
    elif kind == 'Side bars':
        a = w * (0.04 + .10 * strength)
        p.fillRect(QRectF(0, 0, a, h), c)
        p.fillRect(QRectF(w-a, 0, a, h), c)
    elif kind == 'Circle kick':
        radius = min(w, h) * (0.11 + .20 * (1 - strength))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(c, max(3, 17 * sf * strength)))
        p.drawEllipse(QPointF(w / 2, h / 2), radius, radius)
        if n % 4 == 0:
            p.drawEllipse(QPointF(w / 2, h / 2), radius * 1.25, radius * 1.25)
    elif kind == 'Beat grid':
        p.setPen(QPen(c, max(2, 6 * sf)))
        for i in range(1, 9):
            x = i*w/9
            p.drawLine(QPointF(x, 0), QPointF(x, h * (.04 + .12 * strength)))
            p.drawLine(QPointF(x, h), QPointF(x, h - h * (.04 + .12 * strength)))
    elif kind == 'Beat rays':
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(c, max(1.5, 6 * sf * strength)))
        for i in range(20):
            angle = i * math.tau / 20
            r1 = min(w,h) * (.16 + .08*(1-strength))
            r2 = r1 + min(w,h)*(.08 + .19*strength)
            cx,cy = w/2,h/2
            p.drawLine(QPointF(cx+math.cos(angle)*r1,cy+math.sin(angle)*r1),
                       QPointF(cx+math.cos(angle)*r2,cy+math.sin(angle)*r2))
    elif kind == 'Beat diamonds':
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(c, max(2, 9*sf*strength)))
        for i in range(3):
            rad=min(w,h)*(.13+i*.065+(.07*strength))
            path=QPainterPath();path.moveTo(w/2,h/2-rad)
            path.lineTo(w/2+rad,h/2);path.lineTo(w/2,h/2+rad)
            path.lineTo(w/2-rad,h/2);path.closeSubpath();p.drawPath(path)
    elif kind == 'Beat scanline':
        y=h*((.13 + (n * .19) % .75))
        thickness=max(3, 14*sf*strength)
        p.fillRect(QRectF(0,y,w,thickness),c)
        p.fillRect(QRectF(0,y-thickness*2,w,thickness*.45),c)
    p.restore()


def _lyric_lines(words: list[dict]):
    lines = defaultdict(list)
    for entry in words:
        if entry.get('w'):
            lines[int(entry.get('line', 0))].append(entry)
    return list(lines.values())


def _active_line(t: float, words: list[dict]):
    """Only a line containing a word active at this exact timestamp can draw."""
    active = active_words(words, t)
    if not active:
        return []
    selected = int(active[0].get('line', 0))
    return [word for word in words if int(word.get('line', 0)) == selected]


def _mix(i: int, salt: int = 0) -> float:
    # Consistent visual layout for the same project on preview and export.
    n = (int(i) * 1664525 + salt * 1013904223 + 12345) & 0xffffffff
    n ^= n >> 16
    n = (n * 2246822519) & 0xffffffff
    n ^= n >> 13
    return (n & 0xffffff) / float(0xffffff)


def _motion_words(p, w, h, t, project, style, line, o):
    sf = min(w/1280, h/720)
    size = max(16, float(o.get('lyric_size', 72))*sf)
    fam = o.get('lyric_font', 'Anton')
    normal, highlight = o.get('lyric_color','#fff'), o.get('highlight_color','#d9fa37')
    beat, beat_index = pulse(t, float(project.get('bpm',120)),
                             float(project.get('t0',0)), .22)
    visible = active_words(line, t)
    for j, wd in enumerate(visible):
        start, end = float(wd['t0']), float(wd['t1'])
        local = t-start
        idx = int(wd.get('line',0))*997+j
        rng = lambda salt: _mix(idx,salt)
        col = highlight if rng(10)>.42 else normal
        x = w*(.14+.72*rng(1)); y=h*(.17+.66*rng(2))
        angle=(rng(3)-.5)*45
        scale=.65+1.25*rng(4)
        alpha=1.0
        if style == 'Word Explosion':
            progress=clamp(max(0,local)/max(.3,end-start+.22))
            x += (rng(5)-.5)*w*.52*progress
            y += (rng(6)-.5)*h*.5*progress
            angle += (rng(7)-.5)*130*progress
            scale *= .4+1.7*(1-clamp(abs(local)/.55))
            alpha=1.0
        elif style == 'Ransom Note':
            scale *= .60
            angle=(rng(3)-.5)*34
            y=h*(.28+.43*rng(2))
            scale *= 1 + .13*beat
        elif style == 'Word Avalanche':
            x=w*(.1+.8*rng(1)); y=h*(.25+.53*rng(2))
            angle=(rng(3)-.5)*30
            scale*=.72*(1+.06*beat)
            alpha=1.0
        elif style == 'Comic Impact':
            x=w/2+(rng(1)-.5)*w*.28
            y=h/2+(rng(2)-.5)*h*.25
            angle=(rng(3)-.5)*22
            scale*=.9+1.2*math.exp(-max(0,local)*12)+beat*.17
            alpha=1.0
            p.save()
            p.setPen(QPen(color(col, alpha*.85), max(2,3*sf)))
            for ray in range(10):
                theta=ray*math.tau/10+rng(8)
                radius=size*(1.1+.4*rng(ray+18))
                cx=x+math.cos(theta)*radius*scale
                cy=y+math.sin(theta)*radius*scale
                p.drawLine(QPointF(cx,cy),QPointF(cx+math.cos(theta)*size*.48,cy+math.sin(theta)*size*.48))
            p.restore()
        word = str(wd['w'])
        _,tw,_ = _text_path(word,fam,size)
        scale *= min(1.0, w*.65/max(1,tw*scale))
        draw_text(p,word,x,y,size,fam,col,opacity=alpha,scale=scale,tilt=angle,
                  backing=(style=='Ransom Note'))


def _draw_lyrics(p: QPainter, w: int, h: int, t: float, project: dict):
    o = project.get('options', {})
    style = o.get('lyric_style', 'Karaoke line')
    # Last matching segment wins. A segment alters only its own time range,
    # with no changes to original word timestamps or global styling.
    segments = project.get('lyric_segments', [])
    for seg in segments:
        if float(seg.get('start',0)) <= t < float(seg.get('end',0)):
            style = seg.get('style', style)
    if style == 'Off':
        return
    if style in ('Word Explosion','Ransom Note','Word Avalanche','Comic Impact'):
        adjusted_t = t
        # A segment can intentionally span multiple lines, but its drawing is
        # evaluated from original absolute word times, not redistributed timings.
        motion_line = _active_line(adjusted_t, project.get('words', []))
        if motion_line:
            _motion_words(p,w,h,adjusted_t,project,style,motion_line,o)
        return
    song_t = t
    # All word timestamps are absolute video times; no hidden lyric offset.
    line = _active_line(t, project.get('words', []))
    if not line:
        return
    line = active_words(line, t)
    if not line:
        return
    sf = min(w / 1280, h / 720)
    size = max(18, float(o.get('lyric_size', 72)) * sf)
    y = h * float(o.get('lyric_position', 78)) / 100
    fam = o.get('lyric_font', 'Anton')
    normal = o.get('lyric_color', '#f8f9ff')
    active = o.get('highlight_color', '#d9fa37')
    if style in ('Word pop', 'Beat bounce'):
        current = next((x for x in line if float(x['t0']) <= t < float(x['t1'])), None)
        if not current:
            return
        local = max(0.0, t - float(current['t0']))
        beat, _ = pulse(song_t, float(project.get('bpm', 120)), float(project.get('t0', 0)), .2)
        zoom = 1 + .23 * math.exp(-local * 13) * math.cos(local * 21)
        if style == 'Beat bounce':
            zoom *= 1 + .14 * beat
        word = str(current['w'])
        _, tw, _ = _text_path(word, fam, size)
        zoom *= min(1.0, w*.82 / max(1, tw))
        draw_text(p, word, w / 2, y, size, fam, active, scale=zoom, backing=True)
        return
    shown = line  # Every style draws only words in [Start, End).
    if not shown:
        return
    parts = [str(x['w']) for x in shown]
    f = _font(fam, size)
    fm = QFontMetricsF(f)
    gap = fm.horizontalAdvance(' ') * .85
    widths = [fm.horizontalAdvance(part) for part in parts]
    total = sum(widths) + gap * (len(parts)-1)
    ratio = min(1.0, w * .87 / max(1, total))
    if ratio < 1:
        size *= ratio
        fm = QFontMetricsF(_font(fam, size))
        widths = [fm.horizontalAdvance(part) for part in parts]
        gap = fm.horizontalAdvance(' ') * .85
        total = sum(widths) + gap * (len(parts) - 1)
    x = (w - total) / 2
    for word, word_width in zip(shown, widths):
        start, end = float(word['t0']), float(word['t1'])
        highlight = start <= t < end
        fade = .52 if t < start else 1
        # Word positions remain stable in karaoke mode; only the active word changes.
        draw_text(p, str(word['w']), x, y, size, fam,
                  active if highlight else normal, opacity=fade, anchor='left',
                  backing=False)
        x += word_width + gap


@lru_cache(maxsize=4)
def _overlay_frames(filepath: str, mtime_ns: int):
    """Decode GIF/WebP animation frames with their exact delays and transparency.

    The same timestamp-derived frame selection is used by live preview and export.
    A file's modification timestamp invalidates the cache when it is replaced.
    """
    decoded, length = decode_frames(filepath)
    qt_frames = []
    for started, width, height, rgba in decoded:
        img = QImage(rgba, width, height, width * 4, QImage.Format.Format_RGBA8888).copy()
        qt_frames.append((started, img))
    return tuple(qt_frames), length


def overlay_frame(filepath: str, local_seconds: float, speed: float = 1.0):
    path = Path(filepath)
    if not path.is_file():
        return None
    try:
        frames, length = _overlay_frames(str(path.resolve()), path.stat().st_mtime_ns)
    except (OSError, ValueError, EOFError, SyntaxError):
        return None
    if not frames:
        return None
    idx = frame_index(frames, length, local_seconds, speed)
    return frames[idx][1]


def _draw_countdown(p, w, h, t, event, project):
    start, target = float(event.get('start', 0)), float(event.get('end', 0))
    if not start <= t < target:
        return
    fade = event_fade(event, t, start, target)
    if fade <= 0:
        return
    remaining = max(0, target - t)
    minutes = int(remaining // 60)
    seconds = int(remaining % 60)
    thousandths = int(round((remaining - int(remaining)) * 1000))
    if thousandths == 1000:
        seconds += 1; thousandths = 0
    x = w * float(event.get('x', 50)) / 100
    y = h * float(event.get('y', 50)) / 100
    sf = min(w / 1280, h / 720)
    size = float(event.get('size', 130)) * sf
    label = str(event.get('text', '')).strip()
    if label:
        draw_text(p, label, x, y - size*.95, size*.27,
                  event.get('font_family') or project.get('options', {}).get('lyric_font', 'Anton'), event.get('color', '#F7E43E'),
                  opacity=fade)
    draw_text(p, f'{minutes:02}:{seconds:02}.{thousandths:03}', x, y, size,
              event.get('font_family') or project.get('options', {}).get('lyric_font', 'Anton'), event.get('color', '#F7E43E'),
              opacity=fade)


def _draw_overlay(p, w, h, t, event):
    start, end = float(event.get('start', 0)), float(event.get('end', 0))
    if not start <= t < end:
        return
    fade = event_fade(event, t, start, end)
    if fade <= 0:
        return
    img = overlay_frame(str(event.get('text', '')), t - start, float(event.get('speed', 1)))
    if img is None:
        return
    sf = min(w / 1280, h / 720)
    intended_width = max(1, float(event.get('size', 250)) * sf)
    intended_height = intended_width * img.height() / max(1, img.width())
    x = w * float(event.get('x', 50)) / 100
    y = h * float(event.get('y', 50)) / 100
    p.save()
    p.setOpacity(clamp(float(event.get('opacity', 100)) / 100) * fade)
    p.translate(x, y)
    p.rotate(float(event.get('rotation', 0)))
    p.drawImage(QRectF(-intended_width/2, -intended_height/2, intended_width, intended_height), img)
    p.restore()


# Visual effects are deterministic, transparent and clipped to event time boundaries.
MOTION_STUDIO_STYLES = (
    'Comic Burst', 'Particle Spray', 'Neon Rings', 'Radar Sweep',
    'Warning Stripes', 'Lightning Bolts', 'Glitch Blocks', 'Film Burn',
    'Animated Arrows', 'Echo Typography', 'Word Storm', 'Starfield Rush',
    'Halftone Burst', 'Kinetic Grid',
    'Scramble Title', 'Rolling Number', 'Speech Balloon / Callout',
    'Speedometer HUD', 'Warning Meter', 'Scanning HUD', 'Arrow Impact',
    'Heartbeat', 'Rotary Mechanism',
    'Equalizer 3-Band', 'Equalizer 12-Band', 'Beat Heart',
    'Linked Callout', 'Data Card', 'Analysis Panel / Counter', 'Text Reveal', 'Planar Overlay',
    *FRAME_STYLES,
)


def _draw_motion_studio(p, w, h, t, event, project, kind):
    start = float(event.get('start', 0))
    end = float(event.get('end', start + 1))
    if not start <= t < end:
        return
    d = max(.001, end-start)
    local = t-start
    progress = local/d
    x = w*float(event.get('x', 50))/100
    y = h*float(event.get('y', 50))/100
    sf = min(w/1280, h/720)
    base = max(8, float(event.get('size', 110))*sf) * float(event.get('_audio_size_multiplier', 1.0))
    cstr = event.get('color','#F6DD36')
    label = str(event.get('text','')).strip()
    effect_font = str(event.get('font_family') or project.get('options',{}).get('lyric_font','Anton'))
    bpm = float(project.get('bpm',120))
    kick,_ = pulse(t,bpm,float(project.get('t0',0)),.24)
    opacity = clamp(float(event.get('opacity',100))/100)
    rotation = float(event.get('rotation',0))
    fade = event_fade(event, t, start, end)
    if fade <= 0:
        return
    p.save()
    p.setOpacity(clamp(opacity * fade * float(event.get('_audio_opacity_multiplier', 1.0))))
    p.translate(x,y)
    p.rotate(rotation)
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.setPen(QPen(color(cstr), max(1, 4*sf)))
    seed = int(event.get('seed',1234))
    radius = base*(1+.4*kick)
    react_band = event.get('audio_react', 'Off')
    react_value = 0.0
    if react_band != 'Off':
        react_value = clamp(energy_at(project.get('audio_envelopes'), t, react_band, float(event.get('audio_smooth', 0.12))))
    if kind == 'Analysis Panel / Counter':
        # Values are defined by the effect timeline; no inferred data is claimed.
        a=float(event.get('counter_from',0)); b=float(event.get('counter_to',100))
        transition=max(.001,float(event.get('counter_duration',1.5)))
        phase=clamp(local/transition)
        phase=phase*phase*(3-2*phase)
        value=a+(b-a)*phase
        decimals=max(0,min(3,int(event.get('counter_decimals',0))))
        heading=str(event.get('text') or 'SYSTEM ANALYSIS')
        suffix=str(event.get('counter_suffix','%'))
        width=max(90.,w*clamp(float(event.get('panel_width',24))/100,.06,.85))
        height=max(88.,base*.85)
        left=-width/2; top=-height/2
        p.setPen(QPen(color(cstr,.8),max(1.0,1.6*sf)))
        p.setBrush(color(event.get('panel_background','#091724'),.74))
        p.drawRoundedRect(QRectF(left,top,width,height),max(3,5*sf),max(3,5*sf))
        fs=max(9,min(height*.19, float(event.get('panel_font_size',22))*sf))
        p.setPen(QPen(color(cstr)))
        p.setFont(_font(effect_font,fs))
        p.drawText(QPointF(left+12*sf,top+fs+10*sf),heading)
        p.setFont(_font(effect_font,fs*1.6))
        shown=f'{value:.{decimals}f}{suffix}'
        p.drawText(QPointF(left+12*sf,top+height*.68),shown)
        pad=12*sf; bw=max(1,width-2*pad); bh=max(3,6*sf)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(color(cstr,.2))
        p.drawRoundedRect(QRectF(left+pad,top+height-bh-pad,bw,bh),bh/3,bh/3)
        # By default the counter sweeps a full progress bar; optionally define
        # the progress target independently of the displayed number.
        fraction=clamp(float(event.get('counter_progress',100))/100)*phase
        p.setBrush(color(cstr,.9))
        if fraction>0:
            p.drawRoundedRect(QRectF(left+pad,top+height-bh-pad,max(.01,bw*fraction),bh),bh/3,bh/3)
    elif kind == 'Text Reveal':
        text=str(event.get('text') or '')
        dur=max(.001,float(event.get('reveal_duration',1.0)))
        fraction=clamp(local/dur)
        mode=str(event.get('reveal_mode','Words'))
        fs=max(9, float(event.get('reveal_font_size',54))*sf)
        if mode=='Characters':
            count=min(len(text),int(len(text)*fraction+1e-7))
            result=text[:count]
        elif mode=='Words':
            import re
            spans=list(re.finditer(r'\S+',text))
            count=min(len(spans),int(len(spans)*fraction+1e-7))
            result=text[:spans[count-1].end()] if count else ''
        else:
            result=text
        if result:
            if mode in ('Wipe Left','Wipe Right','Wipe Down','Wipe Up'):
                metrics=QFontMetricsF(_font(effect_font,fs))
                tw=max(1.,metrics.horizontalAdvance(text)); th=max(1.,metrics.height())
                # Local clip rectangle is transformed with the whole effect.
                if mode=='Wipe Left': clip=QRectF(-tw/2,-th/2,tw*fraction,th)
                elif mode=='Wipe Right': clip=QRectF(tw/2-tw*fraction,-th/2,tw*fraction,th)
                elif mode=='Wipe Down': clip=QRectF(-tw/2,-th/2,tw,th*fraction)
                else: clip=QRectF(-tw/2,th/2-th*fraction,tw,th*fraction)
                p.save();p.setClipRect(clip)
                draw_text(p,text,0,0,fs,effect_font,cstr,outline='#081727')
                p.restore()
            else:
                # Anchor staged text to the left edge of the complete sentence
                # to avoid jumping as words appear.
                fm=QFontMetricsF(_font(effect_font,fs))
                offset=-fm.horizontalAdvance(text)/2+fm.horizontalAdvance(result)/2
                draw_text(p,result,offset,0,fs,effect_font,cstr,outline='#081727')
    if kind == 'Linked Callout':
        # One tracked anchor owns frame, connector and label. The frame's
        # existing shape_keyframes animate this entire composite effect.
        fw = w * clamp(float(event.get('frame_width', 18))/100, .01, 1)
        fh = h * clamp(float(event.get('frame_height', 26))/100, .01, 1)
        thick = max(1.0, float(event.get('line_thickness', 2.5)) * sf)
        reach = .24 if event.get('callout_frame','Corners') == 'Corners' else .33
        p.setPen(QPen(color(cstr), thick))
        p.setBrush(Qt.BrushStyle.NoBrush)
        if event.get('callout_frame') == 'Box':
            p.drawRect(QRectF(-fw/2,-fh/2,fw,fh))
        else:
            for sx in (-1,1):
                for sy in (-1,1):
                    xx=sx*fw/2; yy=sy*fh/2
                    p.drawLine(QPointF(xx,yy),QPointF(xx-sx*fw*reach,yy))
                    p.drawLine(QPointF(xx,yy),QPointF(xx,yy-sy*fh*reach))
        ox=w*float(event.get('callout_offset_x', 17))/100
        oy=h*float(event.get('callout_offset_y', -12))/100
        # Connector attaches at nearest horizontal edge of the moving frame.
        edge=fw/2 if ox>=0 else -fw/2
        bend=QPointF(edge+ox*.42,oy)
        p.drawLine(QPointF(edge,0),bend)
        p.drawLine(bend,QPointF(ox,oy))
        p.setFont(_font(effect_font,max(10, float(event.get('callout_font_size',22))*sf)))
        text=str(event.get('text') or 'TARGET IDENTIFIED')
        metrics=QFontMetricsF(p.font())
        tw=metrics.horizontalAdvance(text)
        pad=max(7,8*sf)
        text_left=ox+pad if ox>=0 else ox-tw-pad
        label_rect=QRectF(text_left-pad,oy-metrics.ascent()-pad/2,tw+2*pad,metrics.height()+pad)
        p.setPen(QPen(color(cstr,.8),max(1,sf)))
        p.setBrush(color('#081727',.72))
        p.drawRoundedRect(label_rect,3*sf,3*sf)
        p.setPen(QPen(color(cstr),max(1,sf)))
        p.drawText(QPointF(text_left,oy),text)
    elif kind == 'Data Card':
        cw=w*clamp(float(event.get('card_width',25))/100,.05,.9)
        rows=[part.strip() for part in str(event.get('card_rows','TYPE | SYNTHETIC\nSTATUS | ACTIVE\nID | 001')).splitlines() if part.strip()][:12]
        fs=max(10,float(event.get('card_font_size',21))*sf)
        titlefs=fs*1.2
        pad=max(9,14*sf)
        rowh=fs*1.65
        ch=max(fs*4, titlefs*1.6+pad*2+len(rows)*rowh+pad)
        rect=QRectF(-cw/2,-ch/2,cw,ch)
        p.setBrush(color(str(event.get('card_background','#0B1725')),.8))
        p.setPen(QPen(color(cstr,.85),max(1.2,2*sf)))
        p.drawRoundedRect(rect,5*sf,5*sf)
        left=-cw/2+pad; right=cw/2-pad; top=-ch/2+pad
        p.setFont(_font(effect_font,titlefs))
        p.setPen(QPen(color(cstr)))
        p.drawText(QPointF(left,top+titlefs),str(event.get('text') or 'DATA RECORD'))
        p.setPen(QPen(color(cstr,.45),max(1,sf)))
        p.drawLine(QPointF(left,top+titlefs+pad*.45),QPointF(right,top+titlefs+pad*.45))
        p.setFont(_font(effect_font,fs,False))
        metrics=QFontMetricsF(p.font())
        for i,row in enumerate(rows):
            k,sep,v=row.partition('|')
            yy=top+titlefs+pad+rowh*(i+1)
            p.setPen(QPen(color(cstr,.65)))
            p.drawText(QPointF(left,yy),metrics.elidedText(k.strip(),Qt.TextElideMode.ElideRight,max(12,cw*.44-pad)))
            if sep:
                p.setPen(QPen(color('#F3F6FA')))
                text=metrics.elidedText(v.strip(),Qt.TextElideMode.ElideRight,max(12,cw*.48-pad))
                p.drawText(QPointF(right-metrics.horizontalAdvance(text),yy),text)
        if bool(event.get('card_barcode',False)):
            # Decorative, deterministic barcode: not a machine-readable identifier.
            import hashlib
            digest=hashlib.sha256(str(event.get('text','DATA')).encode('utf-8')).digest()
            by=ch/2-pad*.8
            unit=(cw-pad*2)/(len(digest)*2)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(color(cstr,.75))
            for i,b in enumerate(digest):
                p.drawRect(QRectF(left+i*unit*2,by-(7+((b>>4)%7))*sf,unit*(.55+(b%3)*.3), (7+((b>>4)%7))*sf))
    elif kind in ('Equalizer 3-Band', 'Equalizer 12-Band'):
        n = 3 if kind == 'Equalizer 3-Band' else 12
        if n == 3:
            vals = [energy_at(project.get('audio_envelopes'), t, band, .065)
                    for band in ('Bass', 'Midrange', 'Treble')]
        else:
            vals = spectrum_at(project.get('audio_envelopes'), t, .055)
        # Anchor is centre of the whole instrument, with upward-growing bars.
        height = max(12., base*1.7)
        width = max(12., base*2.25)
        gap = max(2.*sf, width/(n*7))
        bw = (width-gap*(n-1))/n
        p.setPen(Qt.PenStyle.NoPen)
        for i,v in enumerate(vals):
            level = clamp(float(v))
            bh = height*(.055+.945*level)
            bx = -width/2+i*(bw+gap)
            p.setBrush(color(cstr, .16))
            p.drawRoundedRect(QRectF(bx,-height/2,bw,height),min(bw/3,5*sf),min(bw/3,5*sf))
            p.setBrush(color(cstr))
            p.drawRoundedRect(QRectF(bx,height/2-bh,bw,bh),min(bw/3,5*sf),min(bw/3,5*sf))
    elif kind == 'Beat Heart':
        bass = energy_at(project.get('audio_envelopes'), t, 'Bass', .07)
        heartbeat = max(bass, kick*.75) if project.get('audio_envelopes') else kick
        scale = 1 + .34*clamp(heartbeat)
        p.scale(scale,scale)
        r = base*.82
        heart = QPainterPath()
        heart.moveTo(0,r*.78)
        heart.cubicTo(-r*.30,r*.47,-r*1.18,-r*.12,-r*.84,-r*.65)
        heart.cubicTo(-r*.60,-r*1.06,-r*.12,-r*.87,0,-r*.47)
        heart.cubicTo(r*.12,-r*.87,r*.60,-r*1.06,r*.84,-r*.65)
        heart.cubicTo(r*1.18,-r*.12,r*.30,r*.47,0,r*.78)
        p.setPen(QPen(color(cstr), max(2.,3*sf),Qt.PenStyle.SolidLine,Qt.PenCapStyle.RoundCap))
        p.setBrush(color(cstr,.22+.35*clamp(heartbeat)))
        p.drawPath(heart)
    elif kind in FRAME_STYLES:
        draw_frame(p, w, h, t, event, kind)
    elif kind == 'Speedometer HUD':
        # Instrument cluster: if audio reaction is selected, the needle/value follow that band directly.
        idle = clamp((0.24 + .22*math.sin(local*1.8) + .12*kick))
        value = react_value if react_band != 'Off' else idle
        sweep = math.radians(240)
        start_angle = math.radians(150)
        r = radius * 1.05
        p.setPen(QPen(color(cstr, .35),max(2,7*sf))); p.setBrush(Qt.BrushStyle.NoBrush)
        path = QPainterPath()
        for i in range(81):
            a=start_angle+sweep*i/80
            point=QPointF(r*math.cos(a),r*math.sin(a))
            if not i: path.moveTo(point)
            else: path.lineTo(point)
        p.drawPath(path)
        p.setPen(QPen(color(cstr),max(2,3*sf)))
        for i in range(13):
            a=start_angle+sweep*i/12
            ra=r*(.75 if i%3==0 else .86)
            p.drawLine(QPointF(ra*math.cos(a),ra*math.sin(a)),QPointF(r*math.cos(a),r*math.sin(a)))
        angle=start_angle+sweep*value
        tip=QPointF(r*.73*math.cos(angle),r*.73*math.sin(angle))
        p.setPen(QPen(color('#FFFFFF'),max(2,7*sf),Qt.PenStyle.SolidLine,Qt.PenCapStyle.RoundCap))
        p.drawLine(QPointF(0,0),tip)
        p.setPen(Qt.PenStyle.NoPen);p.setBrush(color(cstr));p.drawEllipse(QPointF(0,0),7*sf,7*sf)
        draw_text(p,f'{round(value*240):03d}',0,r*.30,base*.35,effect_font,'#FFFFFF',cstr)
        draw_text(p,label or 'SPEED',0,r*.61,base*.15,effect_font,cstr)
    elif kind == 'Warning Meter':
        # Three-state urgency display: audio reaction can directly drive the fill amount.
        value = react_value if react_band != 'Off' else clamp(.20+.62*progress+.16*kick)
        ww=base*2.8; hh=max(12,base*.25)
        p.setPen(QPen(color(cstr),max(2,3*sf)));p.setBrush(color('#111822',.65))
        p.drawRoundedRect(QRectF(-ww/2,0,ww,hh),hh*.25,hh*.25)
        p.setPen(Qt.PenStyle.NoPen);p.setBrush(color(cstr))
        p.drawRect(QRectF(-ww/2+4*sf,4*sf,max(0,(ww-8*sf)*value),max(1,hh-8*sf)))
        p.setPen(QPen(color(cstr),max(2,5*sf)))
        pulse_boost = 1 + (0.15 * value if react_band != 'Off' else .13 * kick)
        for i in range(3):
            sz=base*(.18+i*.10)*pulse_boost
            cx=(i-1)*base*.36
            p.drawLine(QPointF(cx,-base*.45-sz),QPointF(cx-sz*.40,-base*.45+sz*.2))
            p.drawLine(QPointF(cx-sz*.40,-base*.45+sz*.2),QPointF(cx+sz*.40,-base*.45+sz*.2))
            p.drawLine(QPointF(cx+sz*.40,-base*.45+sz*.2),QPointF(cx,-base*.45-sz))
        draw_text(p,label or 'WARNING',0,-base*.95,base*.44,effect_font,'#FFFFFF',cstr)
        draw_text(p,f'{round(value*100)}%',0,base*.52,base*.28,effect_font,cstr)
    elif kind == 'Scanning HUD':
        r=radius*1.15
        p.setPen(QPen(color(cstr,.65),max(2,3*sf)));p.setBrush(Qt.BrushStyle.NoBrush)
        for fraction in (.40,.72,1.0):
            p.drawEllipse(QPointF(0,0),r*fraction,r*fraction)
        for k in range(8):
            a=k*math.tau/8
            p.drawLine(QPointF(r*.78*math.cos(a),r*.78*math.sin(a)),QPointF(r*math.cos(a),r*math.sin(a)))
        a=local*3.1
        p.setPen(QPen(color('#FFFFFF',.85),max(2,5*sf)))
        p.drawLine(QPointF(0,0),QPointF(r*math.cos(a),r*math.sin(a)))
        draw_text(p,label or 'SCANNING',0,r*1.24,base*.24,effect_font,'#FFFFFF',cstr)
    elif kind == 'Arrow Impact':
        # 0-40% approach, 40% strike, 40-100% expanding ring and text burst.
        hit_at=.40
        travel=ease_out_cubic(progress/hit_at)
        left=-w*.55
        arrow_x=left+(0-left)*travel
        if progress < hit_at:
            p.setPen(QPen(color(cstr),max(3,8*sf),Qt.PenStyle.SolidLine,Qt.PenCapStyle.RoundCap))
            p.drawLine(QPointF(arrow_x-base*.75,0),QPointF(arrow_x,0))
            path=QPainterPath();path.moveTo(arrow_x+base*.26,0)
            path.lineTo(arrow_x-base*.12,-base*.24);path.lineTo(arrow_x-base*.12,base*.24);path.closeSubpath()
            p.fillPath(path,color(cstr))
        else:
            hit=clamp((progress-hit_at)/.60)
            expansion=base*(.38+hit*2.0)
            p.setPen(QPen(color(cstr,1-hit*.8),max(2,(10-8*hit)*sf)))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawEllipse(QPointF(0,0),expansion,expansion)
            for i in range(14):
                a=i*math.tau/14
                r0=expansion*.5;r1=expansion*(.9+.22*math.sin(i*5))
                p.drawLine(QPointF(r0*math.cos(a),r0*math.sin(a)),QPointF(r1*math.cos(a),r1*math.sin(a)))
            grow=.38+.62*ease_out_cubic(hit/.27)
            draw_text(p,label or 'IMPACT!',0,0,base*.72,effect_font,'#FFFFFF',cstr,scale=grow)
    elif kind == 'Heartbeat':
        # ECG line with a sharp pulse and subtle heart icon; reads nicely as a buildup or warning.
        width = base * 3.2
        line_y = 0
        p.setPen(QPen(color(cstr, .34), max(1, 2*sf)))
        p.drawLine(QPointF(-width/2, line_y), QPointF(width/2, line_y))
        pulse_phase = react_value if react_band != 'Off' else (0.5 + 0.5*math.sin(local*5.4))
        spike = max(0.0, 1.0 - abs(progress*2-1)*2) if react_band == 'Off' else pulse_phase
        xs = [-0.50, -0.26, -0.14, -0.05, 0.0, 0.06, 0.15, 0.30, 0.50]
        ys = [0.0, 0.0, 0.10, -0.10, -1.0, 0.75, -0.18, 0.0, 0.0]
        path = QPainterPath(QPointF(xs[0]*width, ys[0]*base*.70*spike + line_y))
        for xx, yy in zip(xs[1:], ys[1:]):
            path.lineTo(QPointF(xx*width, yy*base*.70*spike + line_y))
        p.setPen(QPen(color('#FFFFFF'), max(2, 5*sf)))
        p.drawPath(path)
        heart_scale = 0.55 + 0.55 * spike
        hp = QPainterPath()
        s = base * 0.22 * heart_scale
        hx = -width * 0.38; hy = -base * 0.55
        hp.moveTo(hx, hy + s*0.7)
        hp.cubicTo(hx - s*1.0, hy - s*0.2, hx - s*1.1, hy + s*1.0, hx, hy + s*1.55)
        hp.cubicTo(hx + s*1.1, hy + s*1.0, hx + s*1.0, hy - s*0.2, hx, hy + s*0.7)
        p.fillPath(hp, color(cstr, .9))
        if label:
            draw_text(p, label, 0, base*.92, base*.22, effect_font, '#FFFFFF', cstr)
    elif kind == 'Rotary Mechanism':
        # Clockwork / inside-a-clock look with nested rotating spokes and rings.
        outer = radius * 1.08
        inner = radius * 0.56
        p.setPen(QPen(color(cstr, .75), max(2, 3*sf)))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawEllipse(QPointF(0,0), outer, outer)
        p.drawEllipse(QPointF(0,0), inner, inner)
        p.drawEllipse(QPointF(0,0), outer*0.18, outer*0.18)
        spin_fast = local * 2.6 + react_value * 1.6
        spin_slow = -local * 1.25 - react_value * 0.8
        for a0, rr0, rr1, spin, count in ((0.0, inner*0.30, outer*0.94, spin_fast, 12), (0.15, inner*0.12, inner*0.92, spin_slow, 8)):
            for i in range(count):
                a = a0 + spin + i * math.tau / count
                p.drawLine(QPointF(rr0*math.cos(a), rr0*math.sin(a)), QPointF(rr1*math.cos(a), rr1*math.sin(a)))
        p.setPen(QPen(color('#FFFFFF', .9), max(1, 2*sf)))
        for i in range(24):
            a = i*math.tau/24
            rr0 = outer * (0.84 if i % 2 else 0.77)
            p.drawLine(QPointF(rr0*math.cos(a), rr0*math.sin(a)), QPointF(outer*math.cos(a), outer*math.sin(a)))
        if label:
            draw_text(p, label, 0, outer*1.22, base*.18, effect_font, '#FFFFFF', cstr)
    elif kind == 'Comic Burst':
        n=18
        outer=radius*(1.4+.15*math.sin(local*12))
        path=QPainterPath()
        for i in range(n*2):
            a=2*math.pi*i/(n*2)-math.pi/2
            rad=outer if i%2==0 else outer*.58
            pt=QPointF(math.cos(a)*rad,math.sin(a)*rad)
            if i==0:path.moveTo(pt)
            else:path.lineTo(pt)
        path.closeSubpath()
        p.setPen(QPen(color('#10141F'),6*sf));p.setBrush(color(cstr,.92));p.drawPath(path)
        if label:draw_text(p,label,0,0,base*.52,effect_font,'#FFFFFF','#10141F',scale=1+.12*kick)
    elif kind in ('Particle Spray','Starfield Rush'):
        for i in range(90):
            a=2*math.pi*_mix(i+seed,3)
            travel=(local*(.5+_mix(i,4))*(270 if kind=='Particle Spray' else 440)+_mix(i,5)*35)*sf
            if kind=='Starfield Rush': travel+=20*sf
            px=math.cos(a)*travel;py=math.sin(a)*travel
            size=(3+11*_mix(i,8))*sf*(1+kick*.5)
            p.setPen(Qt.PenStyle.NoPen);p.setBrush(color(cstr, .25+.72*_mix(i,9)))
            if kind=='Starfield Rush':
                p.drawEllipse(QRectF(px-size/3,py-size/3,size/1.5,size/1.5))
                p.setPen(QPen(color('#FFFFFF',.65),max(1,2*sf)))
                p.drawLine(QPointF(px,py),QPointF(px-math.cos(a)*size*3,py-math.sin(a)*size*3))
            else:p.drawEllipse(QRectF(px-size/2,py-size/2,size,size))
    elif kind in ('Neon Rings','Radar Sweep'):
        for i in range(4):
            rad=radius*(.45+i*.33+(.35*progress if kind=='Neon Rings' else 0))
            p.setPen(QPen(color(cstr,.9-i*.18),max(1,(6-i)*sf)))
            p.drawEllipse(QPointF(0,0),rad,rad)
        if kind=='Radar Sweep':
            ang=local*240
            pt=QPointF(math.cos(math.radians(ang))*radius*1.45,math.sin(math.radians(ang))*radius*1.45)
            p.drawLine(QPointF(0,0),pt)
        if label:draw_text(p,label,0,0,base*.3,effect_font,'#FFFFFF')
    elif kind=='Warning Stripes':
        p.resetTransform()
        stripe=max(14,w*.07)
        p.setClipRect(0,0,w,h)
        p.setPen(Qt.PenStyle.NoPen)
        for i in range(-8,int(w/stripe)+8):
            a=(i*stripe+local*100*sf)%(w+stripe*5)-stripe*3
            poly=QPainterPath();poly.moveTo(a,0);poly.lineTo(a+stripe*.5,0)
            poly.lineTo(a+stripe*1.6,h);poly.lineTo(a+stripe*1.1,h);poly.closeSubpath()
            p.fillPath(poly,color(cstr,.15+.08*kick))
        if label:draw_text(p,label,w/2,h/2,base*.65,effect_font,'#FFFFFF','#10141F',backing=True)
    elif kind=='Lightning Bolts':
        for k in range(7):
            a=2*math.pi*(k/7+_mix(seed,2)*.1)
            path=QPainterPath();path.moveTo(0,0)
            for j in range(1,9):
                rad=radius*j/6
                aa=a+(_mix(k*20+j+int(local*13),2)-.5)*.16
                path.lineTo(math.cos(aa)*rad,math.sin(aa)*rad)
            p.setPen(QPen(color('#FFFFFF' if k%2 else cstr),max(1,(7-k*.4)*sf)))
            p.drawPath(path)
    elif kind=='Glitch Blocks':
        for i in range(35):
            tick=int(local*15)
            xx=(_mix(i+tick*37+seed,1)-.5)*w
            yy=(_mix(i+tick*37+seed,2)-.5)*h
            ww=(.02+_mix(i,4)*.18)*w
            hh=(2+_mix(i,5)*26)*sf
            p.fillRect(QRectF(xx,yy,ww,hh),color(cstr,.12+_mix(i,7)*.55))
        if label:draw_text(p,label,0,0,base*.5,effect_font,'#FFFFFF',scale=1+.12*kick)
    elif kind=='Film Burn':
        p.resetTransform();p.setPen(Qt.PenStyle.NoPen)
        for i in range(12):
            xx=_mix(i+seed,1)*w;yy=_mix(i+seed,2)*h
            rad=(.1+.7*_mix(i,4))*max(w,h)*(1+.25*math.sin(local*3+i))
            p.setBrush(color('#FF7722' if i%2 else cstr, .035+.035*kick))
            p.drawEllipse(QRectF(xx-rad/2,yy-rad/2,rad,rad))
    elif kind=='Animated Arrows':
        for i in range(8):
            a=math.pi*2*i/8+local*.35
            dist=radius*(.7+.22*math.sin(local*4+i))
            p.save();p.translate(math.cos(a)*dist,math.sin(a)*dist);p.rotate(math.degrees(a))
            poly=QPainterPath();poly.moveTo(-20*sf,-11*sf);poly.lineTo(12*sf,-11*sf)
            poly.lineTo(12*sf,-22*sf);poly.lineTo(39*sf,0)
            poly.lineTo(12*sf,22*sf);poly.lineTo(12*sf,11*sf)
            poly.lineTo(-20*sf,11*sf);poly.closeSubpath()
            p.fillPath(poly,color(cstr,.85));p.restore()
        if label:draw_text(p,label,0,0,base*.55,effect_font,'#FFFFFF')
    elif kind in ('Echo Typography','Word Storm'):
        words=label.split() if label else ['BEAT','DROP','GO','NOW']
        if kind=='Echo Typography':
            for i in range(7,0,-1):
                off=i*7*sf*(1+.25*kick)
                draw_text(p,label or 'ECHO',off,-off,base*.65,effect_font,cstr,'#10141F',opacity=.1+.07*(7-i),tilt=-7)
            draw_text(p,label or 'ECHO',0,0,base*.7,effect_font,'#FFFFFF',cstr)
        else:
            for i in range(36):
                word=words[i%len(words)]
                xx=(_mix(i+seed,1)-.5)*w*.85
                yy=(_mix(i+seed,2)-.5)*h*.75
                angle=(_mix(i,5)-.5)*70
                wobble=math.sin(local*4+i)*10*sf
                draw_text(p,word,xx+wobble,yy,base*(.19+.38*_mix(i,3)),effect_font,cstr,
                          opacity=.45+.45*_mix(i,4),tilt=angle)
    elif kind=='Halftone Burst':
        for row in range(-12,13):
            for col in range(-22,23):
                px=col*22*sf;py=row*22*sf
                dist=math.hypot(px,py)
                if dist<radius*2:
                    rr=max(0,(1-dist/max(1,radius*2)))*8*sf*(1+.35*kick)
                    p.setPen(Qt.PenStyle.NoPen);p.setBrush(color(cstr,.35))
                    p.drawEllipse(QPointF(px,py),rr,rr)
        if label:draw_text(p,label,0,0,base*.7,effect_font,'#FFFFFF')
    elif kind=='Kinetic Grid':
        for i in range(-8,9):
            xx=i*radius/4+math.sin(local*3+i)*radius*.06
            yy=i*radius/4+math.sin(local*3+i)*radius*.06
            p.setPen(QPen(color(cstr,.3+.3*kick),max(1,3*sf)))
            p.drawLine(QPointF(xx,-radius*1.8),QPointF(xx,radius*1.8))
            p.drawLine(QPointF(-radius*1.8,yy),QPointF(radius*1.8,yy))
        if label:draw_text(p,label,0,0,base*.6,effect_font,'#FFFFFF')
    p.restore()


def _draw_planar_overlay(p, w, h, t, event, project):
    """Warp an offscreen text/image canvas to the animated quadrilateral."""
    from .planar import quad_at
    quad=quad_at(event,t)
    if len(quad)!=4 or any(len(v)!=2 for v in quad):return
    target=QPolygonF([QPointF(float(x)*w/100, float(y)*h/100) for x,y in quad])
    # Avoid degenerate or inverted quads; keep the app stable while editing.
    if abs(sum(target[i].x()*target[(i+1)%4].y()-target[(i+1)%4].x()*target[i].y() for i in range(4)))<10:return
    iw,ih=800,240
    canvas=QImage(iw,ih,QImage.Format.Format_ARGB32_Premultiplied)
    canvas.fill(Qt.GlobalColor.transparent)
    pp=QPainter(canvas)
    pp.setRenderHint(QPainter.RenderHint.Antialiasing,True)
    path=str(event.get('planar_image','')).strip()
    if path:
        from pathlib import Path
        source=Path(path)
        if not source.is_absolute():
            from .core import ROOT
            source=Path(ROOT)/source
        img=QImage(str(source)) if source.is_file() else QImage()
        if not img.isNull():pp.drawImage(QRectF(0,0,iw,ih),img)
    else:
        pp.setPen(color(event.get('color','#DCE5F0')))
        pp.setFont(_font(str(event.get('font_family','Anton')),max(12,int(event.get('planar_font_size',88)))))
        pp.drawText(QRectF(15,0,iw-30,ih),Qt.AlignmentFlag.AlignCenter,str(event.get('text','ROAD TEXT')))
    pp.end()
    src=QPolygonF([QPointF(0,0),QPointF(iw,0),QPointF(iw,ih),QPointF(0,ih)])
    transform=QTransform()
    if not QTransform.quadToQuad(src,target,transform):return
    p.save()
    p.setOpacity(max(0,min(1,float(event.get('opacity',100))/100)))
    p.setTransform(transform,True)
    p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform,True)
    p.drawImage(QPointF(0,0),canvas)
    p.restore()


def _draw_speech_balloon(p: QPainter, w: int, h: int, t: float, event: dict):
    """Procedural transparent speech/thought/HUD/comic labels, shared by preview/export."""
    start = float(event.get('start', 0)); end = float(event.get('end', start+1))
    if not start <= t < end:
        return
    sf = min(w/1280.0, h/720.0)
    alpha = clamp(float(event.get('opacity', 100))/100) * event_fade(event,t,start,end)
    if alpha <= 0: return
    kind = str(event.get('balloon_shape', 'Speech bubble'))
    fs = max(8., float(event.get('balloon_font_size', 30))*sf)
    font = _font(str(event.get('font_family') or 'Arial'), fs, False)
    fm = QFontMetricsF(font)
    lines = str(event.get('text', 'HELLO!')).splitlines()[:10] or ['']
    padding = max(4., float(event.get('balloon_padding', 18))*sf)
    min_w = max(60*sf, float(event.get('balloon_width', 340))*sf)
    max_w = max(min_w, w*.8)
    # Manual line breaks control layout; excessively long lines wrap deterministically.
    wrapped = []
    for line in lines:
        if fm.horizontalAdvance(line) <= max_w-2*padding:
            wrapped.append(line)
        else:
            cur = ''
            for word in line.split(' '):
                candidate = (cur+' '+word).strip()
                if cur and fm.horizontalAdvance(candidate) > max_w-2*padding:
                    wrapped.append(cur); cur=word
                else: cur=candidate
            wrapped.append(cur)
    wrapped = wrapped[:12]
    bw = min(max_w, max(min_w, max((fm.horizontalAdvance(line) for line in wrapped),default=0)+2*padding))
    line_h = fm.lineSpacing()*1.13
    bh = max(line_h+2*padding, len(wrapped)*line_h+2*padding)
    local=t-start
    anim=max(.05,float(event.get('balloon_animate_duration', .4)))
    how=str(event.get('balloon_animation', 'Pop'))
    scale=1.0
    if how=='Pop': scale=.65+.35*ease_out_cubic(local/anim)
    if how=='Slide up':
        shift=(1-ease_out_cubic(local/anim))*35*sf
    else: shift=0.
    p.save()
    p.setOpacity(alpha)
    p.translate(w*float(event.get('x',50))/100,h*float(event.get('y',50))/100+shift)
    p.rotate(float(event.get('rotation',0)))
    p.scale(scale,scale)
    r=QRectF(-bw/2,-bh/2,bw,bh)
    ink=str(event.get('balloon_text_color','#151B23'))
    outline=str(event.get('color','#DCE5F0'))
    fill=str(event.get('balloon_fill','#F3F5F7'))
    fill_alpha=clamp(float(event.get('balloon_fill_opacity',92))/100)
    thick=max(.5,float(event.get('balloon_border',2))*sf)
    p.setPen(QPen(color(outline),thick))
    p.setBrush(color(fill,fill_alpha))
    tipx=w*float(event.get('balloon_tail_x',12))/100
    tipy=h*float(event.get('balloon_tail_y',18))/100
    # Tail endpoint is an independent local offset, not locked to the text box.
    if kind in ('Speech bubble','Rounded bubble','Thought bubble'):
        body=QPainterPath()
        radius = (min(28*sf,bh*.3) if kind!='Thought bubble' else min(bh*.32,bw*.14))
        body.addRoundedRect(r,radius,radius)
        if kind=='Thought bubble':
            p.drawPath(body)
            for factor,size in ((.60,10),(.83,6), (1.,3.5)):
                p.drawEllipse(QPointF(tipx*factor,tipy*factor),size*sf,size*sf)
        else:
            # Triangle tail joins the closest side of the body.
            tx=max(r.left()+15*sf,min(r.right()-15*sf,tipx))
            ty=max(r.top()+15*sf,min(r.bottom()-15*sf,tipy))
            if abs(tipy)>abs(tipx)*bh/max(1,bw):
                edge=r.bottom() if tipy>=0 else r.top()
                tail=QPainterPath();tail.moveTo(tx-11*sf,edge)
                tail.lineTo(tipx,tipy);tail.lineTo(tx+11*sf,edge);tail.closeSubpath()
            else:
                edge=r.right() if tipx>=0 else r.left()
                tail=QPainterPath();tail.moveTo(edge,ty-11*sf)
                tail.lineTo(tipx,tipy);tail.lineTo(edge,ty+11*sf);tail.closeSubpath()
            body=body.united(tail)
            p.drawPath(body)
    elif kind=='Comic burst':
        path=QPainterPath()
        n=20
        for i in range(n):
            a=-math.pi/2+i*2*math.pi/n
            rad=1.0 if i%2==0 else .80
            point=QPointF(math.cos(a)*bw*.59*rad,math.sin(a)*bh*.69*rad)
            if i==0:path.moveTo(point)
            else:path.lineTo(point)
        path.closeSubpath();p.drawPath(path)
    else:
        if kind=='HUD callout':
            p.setBrush(color(fill,fill_alpha*.68))
            p.drawRoundedRect(r,4*sf,4*sf)
            p.drawLine(QPointF(0,r.bottom()),QPointF(tipx*.55,tipy))
            p.drawEllipse(QPointF(tipx,tipy),3*sf,3*sf)
        elif kind=='Caption box':
            p.drawRoundedRect(r,5*sf,5*sf)
        else:  # Pointer label
            p.drawRoundedRect(r,8*sf,8*sf)
            p.drawLine(QPointF(0,r.bottom()),QPointF(tipx,tipy))
    p.setFont(font)
    p.setPen(color(ink))
    top = -len(wrapped)*line_h/2
    for i,line in enumerate(wrapped):
        yy=top+i*line_h+(line_h-fm.height())/2+fm.ascent()
        p.drawText(QPointF(-fm.horizontalAdvance(line)/2,yy),line)
    p.restore()


def _scramble_hash(seed: int, frame: int, index: int, stream: int = 0) -> int:
    """Stable pseudo-random character index, independent of playback/render order."""
    n = (int(seed) ^ (int(frame)*0x9E3779B1) ^ (int(index)*0x85EBCA77)
         ^ (int(stream)*0xC2B2AE3D)) & 0xffffffff
    n ^= n >> 16
    n = (n * 0x7FEB352D) & 0xffffffff
    n ^= n >> 15
    n = (n * 0x846CA68B) & 0xffffffff
    return (n ^ (n >> 16)) & 0xffffffff


def _scramble_chars(text: str, elapsed: float, duration: float, order: str,
                    seed: int, rate: float, stream: int = 0) -> str:
    """Resolve characters independently without advancing state between frames."""
    if not text:
        return ''
    positions = [i for i, ch in enumerate(text) if not ch.isspace()]
    if order == 'Right to left':
        positions.reverse()
    elif order == 'Random':
        positions.sort(key=lambda i: _scramble_hash(seed, 0, i, stream))
    ranks = {i: rank for rank, i in enumerate(positions)}
    alphabet = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789#$%&@'
    step = max(0, int(max(0.0, elapsed)*max(1.0, rate)))
    count = len(positions)
    result = []
    for i, ch in enumerate(text):
        if i not in ranks:
            result.append(ch)
        elif elapsed >= duration * (ranks[i] + 1) / max(1, count):
            result.append(ch)
        else:
            result.append(alphabet[_scramble_hash(seed, step, i, stream) % len(alphabet)])
    return ''.join(result)


def _draw_scramble_effect(p: QPainter, w: int, h: int, t: float, event: dict, project: dict):
    """Two-line title or independent digit reels. Same timestamps in preview and export."""
    start = float(event.get('start', 0))
    end = float(event.get('end', start+1))
    if not start <= t < end:
        return
    local = t-start
    sf = min(w/1280.0, h/720.0)
    kind = event.get('style')
    font_family = str(event.get('font_family') or 'Arial')
    main_size = max(9.0, float(event.get('scramble_font_size', 85)) * sf)
    color_main = str(event.get('color', '#ACACAC'))
    alpha = clamp(float(event.get('opacity', 100))/100) * event_fade(event,t,start,end)
    if alpha <= 0:
        return
    p.save()
    p.setOpacity(alpha)
    p.translate(w*float(event.get('x', 50))/100, h*float(event.get('y', 50))/100)
    p.rotate(float(event.get('rotation', 0)))
    p.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
    seed = int(event.get('seed', 1234))
    order = str(event.get('scramble_order', 'Left to right'))
    rate = max(1, float(event.get('scramble_rate', 22)))
    duration = max(.01, float(event.get('scramble_duration', 1.5)))

    def draw_cells(text, y, size, tint, fixed_pitch=False):
        if not text:
            return
        font = _font(font_family if not fixed_pitch else 'Consolas', size)
        if fixed_pitch:
            font.setStyleHint(QFont.StyleHint.Monospace)
        metrics = QFontMetricsF(font)
        # Equal advance for every character; the final target never shifts sideways.
        advance = max(metrics.horizontalAdvance(ch) for ch in 'MW@#0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz ') + 1.0*sf
        total = len(text)*advance
        p.setFont(font)
        p.setPen(color(tint))
        baseline = y + (metrics.ascent()-metrics.descent())/2
        for i, ch in enumerate(text):
            cx = -total/2 + (i+.5)*advance
            p.drawText(QPointF(cx-metrics.horizontalAdvance(ch)/2, baseline), ch)

    if kind == 'Scramble Title':
        title = str(event.get('text', 'THE SIMULATION'))
        subtitle = str(event.get('scramble_subtitle', 'Produced by Koon Grizzly'))
        delay = max(0.0, float(event.get('scramble_subtitle_delay', 1.0)))
        sub_duration = max(.01, float(event.get('scramble_subtitle_duration', 1.5)))
        sub_size = max(7.0, float(event.get('scramble_subtitle_size', 29))*sf)
        gap = max(0.0, float(event.get('scramble_gap', 32))*sf)
        first = _scramble_chars(title, local, duration, order, seed, rate, 0)
        draw_cells(first, -(sub_size+gap)/2, main_size, color_main)
        if local >= delay:
            second = _scramble_chars(subtitle, local-delay, sub_duration, order, seed, rate, 1)
            draw_cells(second, (main_size+gap)/2, sub_size,
                       str(event.get('scramble_subtitle_color', '#ACACAC')))
    else:
        target = str(event.get('text', '847291'))
        # Numeric and punctuation slots remain exactly as entered.
        digits = [i for i, c in enumerate(target) if c.isdigit()]
        count = len(digits)
        ranks = {j: n for n,j in enumerate(digits if order != 'Right to left' else digits[::-1])}
        if order == 'Random':
            ranks = {j:n for n,j in enumerate(sorted(digits,key=lambda j:_scramble_hash(seed,0,j)))}
        font = _font('Consolas', main_size)
        font.setStyleHint(QFont.StyleHint.Monospace)
        fm = QFontMetricsF(font)
        cell = max(fm.horizontalAdvance('0'), fm.horizontalAdvance('8')) + 3*sf
        total = cell*len(target)
        p.setFont(font)
        p.setPen(color(color_main))
        p.setClipRect(QRectF(-total/2-5*sf,-main_size*.8,total+10*sf,main_size*1.6))
        for i,ch in enumerate(target):
            x=-total/2+(i+.5)*cell
            if i not in ranks:
                p.drawText(QPointF(x-fm.horizontalAdvance(ch)/2,(fm.ascent()-fm.descent())/2),ch)
                continue
            rank=ranks[i]
            # A staggered slowing reel ends on the exact requested digit.
            stop=duration*(rank+1)/max(1,count)
            fraction=clamp(local/max(.01,stop))
            target_digit=int(ch)
            loops=max(1, int(event.get('scramble_revolutions', 5)))
            turns=loops*10+target_digit
            position=turns*(1-(1-fraction)**2.4)
            if fraction>=1.0:
                position=float(turns)
            integer=int(math.floor(position))
            offset=position-integer
            upper=str(integer%10)
            lower=str((integer+1)%10)
            baseline=(fm.ascent()-fm.descent())/2
            p.drawText(QPointF(x-fm.horizontalAdvance(upper)/2,baseline-offset*main_size),upper)
            if offset>1e-4:
                p.drawText(QPointF(x-fm.horizontalAdvance(lower)/2,baseline+(1-offset)*main_size),lower)
    p.restore()


def _draw_event_inner(p: QPainter, w: int, h: int, t: float, event: dict, project: dict):
    start = float(event.get('start', 0))
    end = float(event.get('end', start + 1))
    if t < start or t >= end or end <= start:
        return
    local = t - start
    remain = end - t
    sf = min(w/1280, h/720)
    x = w * float(event.get('x', 50))/100
    y = h * float(event.get('y', 50))/100
    size = float(event.get('size', 85)) * sf
    kind = event.get('style', 'Stamp')
    if kind == 'Speech Balloon / Callout':
        _draw_speech_balloon(p,w,h,t,event)
        return
    if kind in ('Scramble Title', 'Rolling Number'):
        _draw_scramble_effect(p,w,h,t,event,project)
        return
    if kind == 'Planar Overlay':
        _draw_planar_overlay(p,w,h,t,event,project)
        return
    if kind in MOTION_STUDIO_STYLES:
        _draw_motion_studio(p,w,h,t,event,project,kind)
        return
    if kind == 'Text Overlay':
        draw_text(p, str(event.get('text','')), x, y, size,
                  str(event.get('font_family') or project.get('options',{}).get('lyric_font','Anton')),
                  event.get('color','#FFFFFF'), opacity=event_fade(event,t,start,end),
                  tilt=float(event.get('rotation',0)),
                  width_scale=float(event.get('text_width',100))/100,
                  tracking=float(event.get('letter_spacing',0)),
                  outline_only=bool(event.get('outline_only',False)))
        return
    if kind == 'Countdown':
        _draw_countdown(p, w, h, t, event, project)
        return
    if kind == 'Image / GIF':
        _draw_overlay(p, w, h, t, event)
        return
    fill = event.get('color', '#ffe64c')
    fade = event_fade(event, t, start, end)
    zoom = 1.0
    tilt = 0.0
    beat, _ = pulse(t, float(project.get('bpm', 120)), float(project.get('t0', 0)), .25)
    if kind == 'Stamp':
        zoom = 1 + .55 * math.exp(-local*12) * math.cos(local*25)
    elif kind == 'Slide in':
        x += (1 - clamp(local / .3) ** .45) * w*.30
    elif kind == 'Side Slam':
        intro = max(0.06, min(0.45, float(event.get('intro', 0.28))))
        prog = ease_out_cubic(local / intro)
        from_left = float(event.get('x', 50)) <= 50
        start_x = -w * 0.25 if from_left else w * 1.25
        x = start_x + (x - start_x) * prog
        zoom = 0.9 + 0.16 * (1.0 - prog)
    elif kind == 'Pop Open':
        intro = max(0.06, min(0.45, float(event.get('intro', 0.24))))
        prog = ease_out_cubic(local / intro)
        zoom = 0.05 + 1.25 * prog
        if local > intro:
            settle = local - intro
            zoom = 1.0 + 0.18 * math.exp(-settle * 9.0) * math.cos(settle * 19.0)
    elif kind == 'Beat punch':
        zoom = 1 + beat*.25
    elif kind == 'Wobble':
        tilt = math.sin(local*11)*9*math.exp(-local*.9)
    elif kind == 'Glitch':
        # Deterministic; independent of the order in which frames are rendered.
        k = int(t * 16)
        j = (k * 127 + 37) % 11 - 5
        x += j * 3 * sf
        tilt = j*.4
    elif kind == 'Neon tag':
        p.save()
        c = color(fill, fade*.7)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(c, max(2, 4*sf)))
        rect = QRectF(x-w*.15, y-size*.72, w*.30, size*1.5)
        p.drawRoundedRect(rect, 8*sf, 8*sf)
        p.restore()
    draw_text(p, str(event.get('text', '')), x, y, size,
              event.get('font_family') or project.get('options', {}).get('lyric_font', 'Anton'), fill,
              opacity=fade, scale=zoom, tilt=tilt, backing=(kind == 'Beat punch'),
              width_scale=float(event.get('text_width',100))/100,
              tracking=float(event.get('letter_spacing',0)),
              outline_only=bool(event.get('outline_only',False)))


def _draw_event(p: QPainter, w: int, h: int, t: float, event: dict, project: dict):
    """Apply optional time-based motion around the event's actual X/Y anchor."""
    start = float(event.get('start', 0)); end = float(event.get('end', start))
    if not (start <= t < end):
        return
    # Scale every selected preset around its placement anchor using cached audio envelopes.
    reactive_scale, reactive_energy = reaction_transform(event, project, t)
    # Full-frame effects reset the painter transform. Modulate their internal
    # geometry/opacity instead so audio reactivity works on these presets too.
    if event.get('style') in ('Warning Stripes', 'Film Burn') and abs(reactive_scale-1.0)>1e-5:
        adjusted = dict(event)
        adjusted['_audio_size_multiplier'] = reactive_scale
        adjusted['_audio_opacity_multiplier'] = 0.45 + min(1.0, reactive_energy) * 0.85
        _draw_event_without_audio(p,w,h,t,adjusted,project)
        return
    if abs(reactive_scale-1.0)>1e-5:
        anchor_x=w*float(event.get('x',50))/100
        anchor_y=h*float(event.get('y',50))/100
        p.save()
        p.translate(anchor_x,anchor_y)
        p.scale(reactive_scale,reactive_scale)
        p.translate(-anchor_x,-anchor_y)
        # A separate saved transform guarantees the normal motion renderer still works.
        try:
            _draw_event_without_audio(p,w,h,t,event,project)
        finally:
            p.restore()
        return
    _draw_event_without_audio(p,w,h,t,event,project)


def _draw_event_without_audio(p: QPainter, w: int, h: int, t: float, event: dict, project: dict):
    start = float(event.get('start',0)); end=float(event.get('end',start))
    entry = str(event.get('entry_motion', 'Default'))
    exit_style = str(event.get('exit_motion', 'Default Fade'))
    if entry == 'Default' and exit_style == 'Default Fade':
        _draw_event_inner(p, w, h, t, event, project)
        return
    if event.get('style') in ('Countdown', 'Image / GIF'):
        # The new motion transforms are meant for text/graphic presets, not imported files.
        _draw_event_inner(p, w, h, t, event, project)
        return
    duration = end-start
    intro = min(max(.01, float(event.get('entry_duration', .4))), duration)
    outro = min(max(.01, float(event.get('exit_duration', .28))), duration)
    dx, dy, sc, ang, opacity = transform(entry, exit_style, t-start, end-t, intro, outro)
    x = w*float(event.get('x',50))/100
    y = h*float(event.get('y',50))/100
    p.save()
    p.translate(dx*w,dy*h)
    p.translate(x,y)
    p.rotate(ang)
    p.scale(sc,sc)
    p.translate(-x,-y)
    if exit_style == 'Instant':
        # Suppress the renderer's default fade-out while keeping fade-in.
        temporary = dict(event)
        temporary['fade_out'] = 0.0
    else:
        temporary = event
    # Custom entries should pop into view instead of being hidden under the normal
    # 280 ms fade; the fade-in is still manually configurable on the event.
    if entry != 'Default':
        temporary = dict(temporary)
        temporary['fade_in'] = float(event.get('fade_in', 0.0))
    _draw_event_inner(p,w,h,t,temporary,project)
    p.restore()




def _paint_media_layers(p, width, height, t, project):
    """Media compositing shared by interactive preview and final render."""
    from .media_layers import is_video, source_time
    layers = project.get('media_layers', [])
    for index, layer in enumerate(layers):
        source_t = source_time(layer, t)
        if source_t is None:
            continue
        path = str(layer.get('path', ''))
        if is_video(path):
            provider = project.get('_media_frame_provider')
            img = provider.frame(index, layer, source_t) if provider else None
            if img is None:
                continue
            _draw_media_image(p, width, height, t, layer, img)
        else:
            # Reuse legacy animated image decoder and draw implementation.
            legacy = dict(layer, text=path)
            _draw_overlay(p, width, height, t, legacy)


def _draw_media_image(p, w, h, t, layer, img):
    start = float(layer.get('start',0))
    end = float(layer.get('end',start+1))
    alpha = event_fade(layer,t,start,end) * clamp(float(layer.get('opacity',100))/100)
    if alpha <= 0:
        return
    scale = min(w/1280,h/720)
    ww = max(1,float(layer.get('size',300))*scale)
    hh = ww * img.height()/max(1,img.width())
    x=w*float(layer.get('x',50))/100
    y=h*float(layer.get('y',50))/100
    p.save()
    p.setOpacity(alpha)
    p.translate(x,y)
    p.rotate(float(layer.get('rotation',0)))
    p.drawImage(QRectF(-ww/2,-hh/2,ww,hh),img)
    p.restore()

def paint_effects(p: QPainter, width: int, height: int, t: float, project: dict):
    """Draw only transparent foreground pixels; leaves caller's background untouched."""
    if width < 2 or height < 2:
        return
    p.save()
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    p.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
    p.setClipRect(0, 0, width, height)
    _draw_beats(p, width, height, t, project)
    _draw_lyrics(p, width, height, t, project)
    _paint_media_layers(p, width, height, t, project)
    from .shape_keyframes import geometry_at
    for event in project.get('events', []):
        if str(event.get('style', '')).startswith('Beat: '):
            if float(event.get('start',0)) <= t < float(event.get('end',0)):
                opts = {'beat_style': str(event['style'])[6:],
                        'beat_every': event.get('beat_every',1),
                        'beat_intensity': event.get('beat_intensity',60),
                        'beat_decay': event.get('beat_decay',.24),
                        'beat_color': event.get('color','#22DDEE'),
                        'beat_palette': event.get('beat_palette','Solid')}
                _draw_beats(p,width,height,t,project,opts)
        else:
            _draw_event(p, width, height, t, geometry_at(event,t), project)
    p.restore()
