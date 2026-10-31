"""Standalone offline PySide6 GUI for beat/lyric-sync effect overlays."""
from __future__ import annotations

import copy
import json
import os
import sys
import threading
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, QUrl, Signal, QThread, QProcess
from PySide6.QtGui import QColor, QFontDatabase, QImage, QKeySequence, QPainter, QShortcut, QFont, QFontMetricsF
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog, QFormLayout,
    QFrame, QGridLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit, QMainWindow,
    QMessageBox, QColorDialog, QProgressBar, QPushButton, QScrollArea, QSlider, QSpinBox,
    QSplitter, QTabWidget, QTableWidget, QTableWidgetItem, QTextEdit,
    QVBoxLayout, QWidget, QHeaderView, QInputDialog, QDialog, QRadioButton, QButtonGroup,
)

from .core import (ROOT, new_project, load_project, save_project, load_settings,
                  save_settings, save_effects_template, load_effects_template, locate_ffmpeg, probe_video, snap_beat, split_lyrics,
                  import_slopcore_words, import_whisper_json, import_subtitles, import_beatmap)
from .paint import paint_effects, MOTION_STUDIO_STYLES, BEAT_EFFECTS
from .motion_math import ENTRIES, EXITS
from .workers import BeatWorker, WhisperWorker, WhisperCppWorker, RenderWorker, MultibandWorker

try:
    from PySide6.QtMultimedia import QMediaPlayer, QAudioOutput, QVideoSink
    HAVE_MEDIA = True
except ImportError:
    HAVE_MEDIA = False


class NoWheelCombo(QComboBox):
    def wheelEvent(self, event):
        event.ignore()


class NoWheelSpin(QSpinBox):
    def wheelEvent(self, event):
        event.ignore()


class NoWheelDoubleSpin(QDoubleSpinBox):
    def wheelEvent(self, event):
        event.ignore()


def valid_hex_color(value, fallback='#FFFFFF'):
    """Normalize colors saved in older projects without silently converting to white."""
    c = QColor(str(value))
    return c.name(QColor.NameFormat.HexRgb).upper() if c.isValid() else fallback


class ColorPickerField(QWidget):
    """Visual swatch + familiar named color; retains a QLineEdit for JSON compatibility."""
    COLOR_NAMES = {
        '#FFFFFF':'White', '#000000':'Black', '#FF0000':'Red', '#00FF00':'Green',
        '#0000FF':'Blue', '#FFFF00':'Yellow', '#00FFFF':'Cyan', '#FF00FF':'Magenta',
        '#FFA500':'Orange', '#800080':'Purple', '#F8F9FF':'Soft white',
        '#D9FA37':'Lime', '#22DDEE':'Turquoise',
    }

    def __init__(self, value, parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self); layout.setContentsMargins(0,0,0,0); layout.setSpacing(6)
        self.value_edit = QLineEdit(valid_hex_color(value))
        self.value_edit.setVisible(False)
        self.button = QPushButton()
        self.button.setMinimumWidth(155)
        self.button.setToolTip('Choose a color visually; no color codes needed')
        self.button.clicked.connect(self.pick)
        layout.addWidget(self.button)
        layout.addStretch(1)
        self.set_color(value)

    def set_color(self, value):
        color = valid_hex_color(value)
        self.value_edit.setText(color)
        name = self.COLOR_NAMES.get(color, 'Custom color')
        self.button.setText('🎨  ' + name + '  ▾')
        self.button.setStyleSheet('QPushButton { background: %s; color: %s; border: 2px solid #657581; '
                                  'border-radius: 5px; padding: 7px; font-weight: bold; } '
                                  'QPushButton:hover { border: 2px solid #EEEEEE; }' %
                                  (color, '#111111' if QColor(color).lightness() > 135 else '#FFFFFF'))

    def pick(self):
        initial = QColor(self.value_edit.text())
        chosen = QColorDialog.getColor(initial, self, 'Select text / effect color',
                                       QColorDialog.ColorDialogOption.DontUseNativeDialog)
        if chosen.isValid():
            self.set_color(chosen.name())
            self.value_edit.editingFinished.emit()


class EffectsTimeline(QWidget):
    """Interactive overview: drag an event or its right edge; table remains exact editor."""
    def __init__(self, app):
        super().__init__()
        self.app=app
        self.setMinimumHeight(210)
        self.setMouseTracking(True)
        self._hit=[]
        self._drag=None

    def _duration(self):
        events=self.app.project.get('events',[])
        return max(10.0, float(self.app.project.get('duration',0)),
                   max((float(e.get('end',0)) for e in events),default=0))

    def paintEvent(self,event):
        p=QPainter(self);p.fillRect(self.rect(),QColor('#0b131c'))
        w=self.width();left=78;right=w-14;usable=max(1,right-left)
        dur=self._duration();self._hit=[]
        p.setPen(QColor('#7b8ca2'))
        for i in range(11):
            x=left+usable*i/10
            p.drawLine(int(x),21,int(x),self.height()-8)
            p.drawText(int(x)+2,15,f'{dur*i/10:.1f}s')
        for index,e in enumerate(self.app.project.get('events',[])):
            row=index%6;y=33+row*27
            start=float(e.get('start',0));end=float(e.get('end',start))
            x=left+start/dur*usable;x2=left+end/dur*usable
            width=max(3,x2-x)
            rect=(x,y,width,22)
            self._hit.append((index,rect))
            selected=any(it.row()==index for it in self.app.event_table.selectedItems())
            hue=[195,42,286,145,15,325][row]
            c=QColor.fromHsv(hue,150 if not selected else 235,185 if not selected else 245)
            p.fillRect(int(x),y,int(width),22,c)
            p.setPen(QColor('#FFFFFF'))
            p.drawText(int(x)+4,y+15,str(e.get('style','Effect'))[:max(2,int(width//7))])
            p.setPen(QColor('#0b131c'))
            p.drawLine(int(x2)-3,y+1,int(x2)-3,y+21)
        p.end()

    def _time_x(self,x):
        dur=self._duration()
        return max(0,(x-78)/max(1,self.width()-92)*dur)

    def mousePressEvent(self,event):
        if event.button()!=Qt.MouseButton.LeftButton:return
        mx=event.position().x();my=event.position().y()
        for i,(idx,(x,y,ww,hh)) in reversed(list(enumerate(self._hit))):
            if x<=mx<=x+ww and y<=my<=y+hh:
                ev=self.app.project['events'][idx]
                mode='resize' if x+ww-mx<10 else 'move'
                self._drag=(idx,mode,mx,float(ev['start']),float(ev['end']))
                self.app.event_table.selectRow(idx)
                return

    def mouseMoveEvent(self,event):
        if not self._drag:return
        idx,mode,mx,start,end=self._drag
        delta=self._time_x(event.position().x())-self._time_x(mx)
        ev=self.app.project['events'][idx]
        if mode=='move':
            ev['start']=round(max(0,start+delta),3)
            ev['end']=round(ev['start']+end-start,3)
        else:
            ev['end']=round(max(start+.04,end+delta),3)
        self.app.refresh_event_table();self.app.preview.update();self.update()

    def mouseReleaseEvent(self,event):
        self._drag=None


class Preview(QWidget):
    def __init__(self, app):
        super().__init__()
        self.app = app
        self.frame = QImage()
        self.setMinimumSize(450, 255)
        self.setStyleSheet('background: #070b11')

    def set_frame(self, image):
        if image and not image.isNull():
            self.frame = image
            self.update()

    def _canvas(self):
        w, h = max(2, int(self.app.project['width'])), max(2, int(self.app.project['height']))
        factor = min(self.width()/w, self.height()/h)
        return w, h, factor, (self.width()-w*factor)/2, (self.height()-h*factor)/2

    def _point(self, pos):
        w, h, factor, left, top = self._canvas()
        return ((pos.x()-left)/factor, (pos.y()-top)/factor)

    SHAPES = ('Object Box','Object Corners','Object Brackets','Linked Callout','Free Line','Arrow Line')

    def _shape_rect(self,e):
        w,h,_,_,_=self._canvas()
        cx=w*float(e.get('x',50))/100;cy=h*float(e.get('y',50))/100
        ww=w*float(e.get('frame_width',20))/100
        hh=h*float(e.get('frame_height',30))/100
        return cx-ww/2,cy-hh/2,ww,hh

    def _shape_hit(self,pos):
        """Test the exact *rendered* handle positions in screen pixels first."""
        import math
        px, py = pos.x(), pos.y()
        w,h,f,left,top = self._canvas()
        visible=[]
        from .shape_keyframes import geometry_at
        for idx,raw in enumerate(self.app.project.get('events',[])):
            e=geometry_at(raw,self.app.current_time())
            if e.get('style') not in self.SHAPES: continue
            if not float(e.get('start',0)) <= self.app.current_time() < float(e.get('end',0)): continue
            visible.append((idx,e))
        from .planar import quad_at
        for idx,raw in reversed(list(enumerate(self.app.project.get('events',[])))):
            if raw.get('style')!='Planar Overlay':continue
            if not float(raw.get('start',0)) <= self.app.current_time() < float(raw.get('end',0)):continue
            quad=quad_at(raw,self.app.current_time())
            for n,(qx,qy) in enumerate(quad):
                if abs(px-(left+f*w*qx/100))<=19 and abs(py-(top+f*h*qy/100))<=19:
                    return idx,'planar_corner_'+str(n)
        selected=getattr(self,'selected',None)
        visible.sort(key=lambda pair:(pair[0]==selected,pair[0]),reverse=True)
        for idx,e in visible:
            if e['style'] in ('Free Line','Arrow Line'):
                x1=left+f*w*float(e.get('x',50))/100
                y1=top+f*h*float(e.get('y',50))/100
                x2=left+f*w*float(e.get('end_x',50))/100
                y2=top+f*h*float(e.get('end_y',50))/100
                for xx,yy,mode in ((x2,y2,'endpoint'),(x1,y1,'startpoint')):
                    if math.hypot(px-xx,py-yy) <= 17: return idx,mode
                dx=x2-x1;dy=y2-y1
                fraction=max(0,min(1,((px-x1)*dx+(py-y1)*dy)/max(1,dx*dx+dy*dy)))
                if math.hypot(px-(x1+fraction*dx),py-(y1+fraction*dy)) <= 12:
                    return idx,'move'
                continue
            cx=w*float(e.get('x',50))/100
            cy=h*float(e.get('y',50))/100
            ww=w*float(e.get('frame_width',20))/100
            hh=h*float(e.get('frame_height',30))/100
            angle=math.radians(float(e.get('rotation',0)))
            ca,sa=math.cos(angle),math.sin(angle)
            # Same local corner positions and transform as Preview.paintEvent.
            for corner,dx,dy in (('tl',-ww/2,-hh/2),('tr',ww/2,-hh/2),
                                 ('bl',-ww/2,hh/2),('br',ww/2,hh/2)):
                hx=left+f*(cx+dx*ca-dy*sa)
                hy=top+f*(cy+dx*sa+dy*ca)
                if abs(px-hx)<=18 and abs(py-hy)<=18:
                    return idx,'shape_resize_'+corner
            # Body move uses unrotated local coordinates.
            x,y=self._point(pos)
            dx=x-cx;dy=y-cy
            lx=dx*ca+dy*sa
            ly=-dx*sa+dy*ca
            if abs(lx)<=ww/2+8/max(f,.001) and abs(ly)<=hh/2+8/max(f,.001):
                return idx,'move'
        return None,None

    def mouseHoverCursor(self, pos):
        idx,mode=self._shape_hit(pos)
        if mode and mode.startswith('planar_corner_'):return Qt.CursorShape.CrossCursor
        if mode and mode.startswith('shape_resize_'):
            return Qt.CursorShape.SizeFDiagCursor if mode[-2:] in ('tl','br') else Qt.CursorShape.SizeBDiagCursor
        if mode in ('endpoint','startpoint'): return Qt.CursorShape.CrossCursor
        if mode=='move': return Qt.CursorShape.SizeAllCursor
        return Qt.CursorShape.ArrowCursor

    def _create_shape(self, kind, start, end):
        w,h,_,_,_=self._canvas()
        x1,y1=start;x2,y2=end
        if abs(x2-x1)<4 and abs(y2-y1)<4:return
        now=self.app.current_time()
        finish=max(now+2.0, min(float(self.app.project.get('duration',now+2)),now+5.0))
        if kind in ('Free Line','Arrow Line'):
            e={'x':round(100*x1/w,2),'y':round(100*y1/h,2),
               'end_x':round(100*x2/w,2),'end_y':round(100*y2/h,2)}
        else:
            e={'x':round(50*(x1+x2)/w,2),'y':round(50*(y1+y2)/h,2),
               'frame_width':round(max(1,100*abs(x2-x1)/w),2),
               'frame_height':round(max(1,100*abs(y2-y1)/h),2)}
        e.update({'style':kind,'start':round(now,3),'end':round(finish,3),
                  'color':'#FFFFFF','line_thickness':2.5,'opacity':100,'rotation':0,'text':''})
        self.app.project.setdefault('events',[]).append(e)
        self.selected=len(self.app.project['events'])-1
        self.app.refresh_event_table();self.app.event_table.selectRow(self.selected)

    def _hit(self, pos):
        x, y = self._point(pos)
        for idx in reversed(range(len(self.app.project.get('events',[])))):
            e=self.app.project['events'][idx]
            if not (float(e.get('start',0)) <= self.app.current_time() < float(e.get('end',0))):
                continue
            if e.get('style') in ('Countdown','Image / GIF'):
                continue
            w,h,_,_,_=self._canvas()
            cx=w*float(e.get('x',50))/100; cy=h*float(e.get('y',50))/100
            font=QFont(str(e.get('font_family','Anton')))
            font.setPixelSize(max(8,int(float(e.get('size',100))*min(w/1280,h/720))))
            width=QFontMetricsF(font).horizontalAdvance(str(e.get('text',''))) * float(e.get('text_width',100))/100
            height=max(24, float(e.get('size',100))*min(w/1280,h/720))
            if abs(x-cx)<max(50,width/2+22) and abs(y-cy)<height+20:
                return idx
        return None

    def mousePressEvent(self, event):
        if event.button()!=Qt.MouseButton.LeftButton: return
        if getattr(self,'draw_kind',None):
            self.draw_origin=self._point(event.position())
            self.draw_current=self.draw_origin
            self.drag_mode='drawing'
            self.update();return
        shape_idx,shape_mode=self._shape_hit(event.position())
        if shape_idx is not None:
            if self.app.player and self.app.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
                self.app.player.pause()
            self.selected=shape_idx
            self.app.event_table.selectRow(shape_idx)
            e=self.app.project['events'][shape_idx]
            self.drag_mode=shape_mode
            from .shape_keyframes import geometry_at
            current=geometry_at(e,self.app.current_time())
            self.drag_start=(self._point(event.position()),dict(current))
            self.setCursor(self.mouseHoverCursor(event.position()))
            self.update();return
        idx=self._hit(event.position())
        if idx is None:
            self.selected=None
            self.app.event_table.clearSelection()
            self.update()
            return
        if self.app.project['events'][idx].get('style') in self.SHAPES:
            return  # Shapes have dedicated geometry handles, never text-style rotation.
        self.selected=idx
        self.app.event_table.selectRow(idx)
        e=self.app.project['events'][idx]
        x,y=self._point(event.position())
        w,h,_,_,_=self._canvas()
        cx=w*float(e.get('x',50))/100; cy=h*float(e.get('y',50))/100
        size=float(e.get('size',100))*min(w/1280,h/720)
        dist=((x-cx)**2+(y-cy)**2)**.5
        self.drag_mode='rotate' if abs(x-cx)<22 and (y-cy)<-size*.8 else ('resize' if dist>size*.8 else 'move')
        self.drag_start=(x,y,float(e.get('x',50)),float(e.get('y',50)),float(e.get('size',100)),float(e.get('rotation',0)),dist)
        self.update()

    def mouseMoveEvent(self, event):
        if not hasattr(self,'drag_mode') or self.drag_mode is None:
            self.setCursor(self.mouseHoverCursor(event.position()))
            return
        if self.drag_mode=='drawing':
            self.draw_current=self._point(event.position());self.update();return
        if self.drag_mode.startswith('planar_corner_'):
            idx=getattr(self,'selected',None)
            if idx is not None and idx<len(self.app.project.get('events',[])):
                from .planar import quad_at, set_quad_key
                obj=self.app.project['events'][idx]
                quad=quad_at(obj,self.app.current_time())
                x,y=self._point(event.position());w,h,_,_,_=self._canvas()
                quad[int(self.drag_mode.rsplit('_',1)[-1])]=[round(max(-50,min(150,x*100/w)),3),round(max(-50,min(150,y*100/h)),3)]
                set_quad_key(obj,self.app.current_time(),quad)
                self.update()
            return
        idx=getattr(self,'selected',None)
        if idx is None or idx>=len(self.app.project.get('events',[])):return
        e=self.app.project['events'][idx]
        x,y=self._point(event.position())
        if (self.drag_mode in ('move','endpoint','startpoint') or self.drag_mode.startswith('shape_resize_')) and isinstance(self.drag_start,tuple) and len(self.drag_start)==2:
            (sx,sy), original=self.drag_start
            w,h,_,_,_=self._canvas()
            if self.drag_mode=='move':
                dx=100*(x-sx)/w;dy=100*(y-sy)/h
                e['x']=round(max(0,min(100,float(original['x'])+dx)),2)
                e['y']=round(max(0,min(100,float(original['y'])+dy)),2)
                if 'end_x' in original:
                    e['end_x']=round(max(0,min(100,float(original['end_x'])+dx)),2)
                    e['end_y']=round(max(0,min(100,float(original['end_y'])+dy)),2)
            elif self.drag_mode=='endpoint':
                e['end_x']=round(max(-100,min(200,100*x/w)),2)
                e['end_y']=round(max(-100,min(200,100*y/h)),2)
            elif self.drag_mode=='startpoint':
                e['x']=round(max(-100,min(200,100*x/w)),2)
                e['y']=round(max(-100,min(200,100*y/h)),2)
            elif self.drag_mode.startswith('shape_resize_'):
                l,t,ww,hh=self._shape_rect(original)
                angle=float(original.get('rotation',0))
                if angle:
                    import math
                    cx=l+ww/2; cy=t+hh/2
                    dx=x-cx; dy=y-cy; rad=math.radians(-angle)
                    x=cx+dx*math.cos(rad)-dy*math.sin(rad)
                    y=cy+dx*math.sin(rad)+dy*math.cos(rad)
                right=l+ww; bottom=t+hh
                corner=self.drag_mode[-2:]
                minw=max(5,w*.008); minh=max(5,h*.008)
                if 'l' in corner: l=min(x,right-minw)
                if 'r' in corner: right=max(x,l+minw)
                if 't' in corner: t=min(y,bottom-minh)
                if 'b' in corner: bottom=max(y,t+minh)
                e['frame_width']=round(100*(right-l)/w,3)
                e['frame_height']=round(100*(bottom-t)/h,3)
                # Correct center after resizing a rotated frame: use the
                # original rotation to transform the local center movement.
                old_cx = w*float(original.get('x',50))/100
                old_cy = h*float(original.get('y',50))/100
                old_l,old_t,old_w,old_h=self._shape_rect(original)
                local_dx=(l+right)/2-old_cx
                local_dy=(t+bottom)/2-old_cy
                if angle:
                    import math
                    rad=math.radians(angle)
                    local_dx,local_dy=(local_dx*math.cos(rad)-local_dy*math.sin(rad),
                                        local_dx*math.sin(rad)+local_dy*math.cos(rad))
                e['x']=round(100*(old_cx+local_dx)/w,3)
                e['y']=round(100*(old_cy+local_dy)/h,3)
                self.app.status.setText('Frame resized: %.1f%% wide × %.1f%% high' %
                                        (e['frame_width'],e['frame_height']))
                self.app.frame_width.blockSignals(True)
                self.app.frame_width.setValue(round(e['frame_width']))
                self.app.frame_width.blockSignals(False)
                self.app.frame_height.blockSignals(True)
                self.app.frame_height.setValue(round(e['frame_height']))
                self.app.frame_height.blockSignals(False)
            if e.get('shape_keyframes'):
                # The renderer reads geometry from shape_keyframes, not from
                # the base event. Persist edits at the playhead immediately:
                # otherwise the drag appears frozen until mouse release.
                from .shape_keyframes import capture
                capture(e,self.app.current_time())
            self.update();return
        x0,y0,ex,ey,sz,rot,dist0=self.drag_start
        w,h,_,_,_=self._canvas()
        if self.drag_mode=='move':
            e['x']=round(min(100,max(0,ex+(x-x0)*100/w)),2)
            e['y']=round(min(100,max(0,ey+(y-y0)*100/h)),2)
        elif self.drag_mode=='resize':
            cx=w*ex/100;cy=h*ey/100
            dist=((x-cx)**2+(y-cy)**2)**.5
            e['size']=round(min(700,max(8,sz*dist/max(8,dist0))),1)
        else:
            import math
            cx=w*ex/100;cy=h*ey/100
            e['rotation']=round(math.degrees(math.atan2(y-cy,x-cx))+90,1)
        self.update()

    def mouseReleaseEvent(self, event):
        if getattr(self,'drag_mode',None)=='drawing':
            self._create_shape(self.draw_kind,self.draw_origin,self._point(event.position()))
            self.draw_kind=None
        if getattr(self,'drag_mode',None) is not None:
            if self.drag_mode != 'drawing' and self.selected is not None and self.selected < len(self.app.project.get('events',[])):
                obj=self.app.project['events'][self.selected]
                if obj.get('style') in self.SHAPES and obj.get('shape_keyframes'):
                    from .shape_keyframes import capture
                    capture(obj,self.app.current_time())
            self.drag_mode=None
            self.app.refresh_event_table()
            self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor('#070b11'))
        project = self.app.project
        w, h = max(2, int(project['width'])), max(2, int(project['height']))
        factor = min(self.width() / w, self.height() / h)
        dw, dh = w * factor, h * factor
        left, top = (self.width() - dw)/2, (self.height() - dh)/2
        if not self.frame.isNull():
            p.drawImage(int(left), int(top), self.frame.scaled(int(dw), int(dh),
                        Qt.AspectRatioMode.IgnoreAspectRatio,
                        Qt.TransformationMode.SmoothTransformation))
        else:
            p.setPen(QColor('#7b879e'))
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter,
                       'Load a video to preview its footage.\nEffects remain fully previewable offline.')
        p.save()
        p.translate(left, top)
        p.scale(factor, factor)
        paint_effects(p, w, h, self.app.current_time(), project)
        idx=getattr(self,'selected',None)
        if idx is not None and idx<len(project.get('events',[])) and float(project['events'][idx].get('start',0)) <= self.app.current_time() < float(project['events'][idx].get('end',0)):
            from .shape_keyframes import geometry_at
            e=geometry_at(project['events'][idx], self.app.current_time())
            cx=w*float(e.get('x',50))/100;cy=h*float(e.get('y',50))/100
            scale=min(w/1280,h/720)
            size=float(e.get('size',100))*scale
            font=QFont(str(e.get('font_family','Anton')))
            font.setPixelSize(max(8,int(size)))
            tw=QFontMetricsF(font).horizontalAdvance(str(e.get('text',''))) * float(e.get('text_width',100))/100
            p.save();p.translate(cx,cy);p.rotate(float(e.get('rotation',0)))
            p.setPen(QColor('#22DDEE'))
            if e.get('style')=='Planar Overlay':
                from .planar import quad_at
                from PySide6.QtCore import QRectF
                quad=quad_at(e,self.app.current_time())
                p.restore()
                p.setPen(QColor('#00E5FF'))
                from PySide6.QtGui import QPolygonF
                from PySide6.QtCore import QPointF
                pts=QPolygonF([QPointF(w*a/100,h*b/100) for a,b in quad])
                p.drawPolygon(pts)
                hs=13/max(.001,factor)
                p.setBrush(QColor('#00E5FF'))
                for pt in pts:p.drawRect(QRectF(pt.x()-hs/2,pt.y()-hs/2,hs,hs))
                p.restore();p.end();return
            if e.get('style') in self.SHAPES:
                from PySide6.QtCore import QRectF
                if e['style'] in ('Free Line','Arrow Line'):
                    x2=w*float(e.get('end_x',50))/100-cx
                    y2=h*float(e.get('end_y',50))/100-cy
                    p.drawLine(0,0,int(x2),int(y2))
                    p.drawEllipse(QRectF(-7,-7,14,14))
                    p.drawEllipse(QRectF(x2-7,y2-7,14,14))
                else:
                    ww=w*float(e.get('frame_width',20))/100
                    hh=h*float(e.get('frame_height',30))/100
                    p.drawRect(QRectF(-ww/2,-hh/2,ww,hh))
                    # Four easy-to-grab handles positioned EXACTLY at the corners.
                    # Constant display-pixel size even for 4K source video.
                    handle=13/max(.001,factor)
                    p.setBrush(QColor('#00E5FF'))
                    for hx,hy in ((-ww/2,-hh/2),(ww/2,-hh/2),(-ww/2,hh/2),(ww/2,hh/2)):
                        p.drawRect(QRectF(hx-handle/2,hy-handle/2,handle,handle))
                    p.setBrush(Qt.BrushStyle.NoBrush)
                p.restore();p.restore();p.end();return
            from PySide6.QtCore import QRectF
            p.drawRect(QRectF(-max(32,tw/2)-12,-size*.55-10,max(64,tw)+24,size*1.1+20))
            p.drawEllipse(0,-size*.9,8,8)
            p.drawRect(QRectF(max(32,tw/2)+5,size*.45+5,12,12))
            p.restore()
        if getattr(self,'drag_mode',None)=='drawing':
            from PySide6.QtCore import QRectF
            x1,y1=self.draw_origin;x2,y2=self.draw_current
            p.setPen(QColor('#00E5FF'))
            if self.draw_kind in ('Free Line','Arrow Line'):
                p.drawLine(int(x1),int(y1),int(x2),int(y2))
            else:
                p.drawRect(QRectF(min(x1,x2),min(y1,y2),abs(x2-x1),abs(y2-y1)))
        p.restore()
        p.end()


def button(text, fn, tooltip=''):
    b = QPushButton(text)
    b.clicked.connect(fn)
    if tooltip:
        b.setToolTip(tooltip)
    return b


def spin_double(lo, hi, val, decimals=2, step=.1):
    s = NoWheelDoubleSpin()
    s.setRange(lo, hi); s.setValue(val)
    s.setDecimals(decimals); s.setSingleStep(step)
    s.setKeyboardTracking(False)
    return s


def spin_int(lo, hi, val, step=1):
    s = NoWheelSpin(); s.setRange(lo, hi); s.setValue(val)
    s.setSingleStep(step); s.setKeyboardTracking(False)
    return s


def fixed_combo(options, current):
    c = NoWheelCombo(); c.addItems(options)
    c.setCurrentText(str(current))
    return c


def scroll_pane(child):
    area = QScrollArea()
    area.setWidgetResizable(True)
    area.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn)
    area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
    area.setMinimumWidth(260)
    area.setFrameShape(QFrame.Shape.NoFrame)
    area.setWidget(child)
    return area


class ShapeTrackerWorker(threading.Thread):
    """Plain Python thread; GUI polls outcome on the Qt event loop."""

    def __init__(self, ffmpeg, video, start, duration, box, fps=6):
        super().__init__(daemon=True)
        self.progress = (0, 0)
        self.ffmpeg, self.video, self.start = ffmpeg, video, start
        self.duration, self.box, self.fps = duration, box, fps
        self.outcome = None

    def _stage(self, message):
        # Worker-thread diagnostic: does not depend on a Qt GUI callback.
        try:
            from datetime import datetime
            target = ROOT / 'logs' / 'shape_tracking.log'
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open('a', encoding='utf-8') as fh:
                fh.write(f'{datetime.now().isoformat(timespec="seconds")} | worker | {message}\n')
        except Exception:
            pass

    def run(self):
        self._stage('entered run')
        try:
            from .tracker_numpy import decode_segment, track_frames, to_shape_keyframes
            self._stage('decoding frames')
            frames, times = decode_segment(self.ffmpeg, self.video, self.start,
                                           self.duration, self.fps)
            self._stage(f'decoded {len(frames)} frames; matching')
            result = track_frames(frames, self.box, times, search=38, progress=lambda done,total: setattr(self, "progress", (done,total)))
            self._stage(f'matched {len(result)} of {len(frames)} frames')
            if len(result) < 2:
                raise ValueError('Could not confidently track this object. Try a box with more visible detail.')
            height, width = frames.shape[1:3]
            keys = to_shape_keyframes(result, width, height, stride=2)
            self.outcome = ("success", keys, (len(result), len(frames), result[-1][2]))
            self._stage(f"success, {len(keys)} keys")
        except Exception as exc:
            self.outcome = ("error", str(exc))
            self._stage(f"error: {type(exc).__name__}: {exc}")


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.project = new_project()
        self.project_file = ''
        self.settings = load_settings()
        self.ffmpeg, self.ffprobe = locate_ffmpeg(str(self.settings.get('ffmpeg', '')))
        # Persist discovered FFmpeg and materialize the JSON settings on first launch.
        if self.ffmpeg:
            self.settings['ffmpeg'] = self.ffmpeg
        save_settings(self.settings)
        self._worker = None
        self._shape_tracker = None
        self._shape_track_index = None
        self._shape_track_start = None
        self._track_completion_timer = QTimer(self)
        self._track_completion_timer.setInterval(250)
        self._track_completion_timer.timeout.connect(self._check_tracking_completion)
        self._render = None
        self._seeking = False
        self._manual_time = 0.0
        self._tap_words = []
        self._tap_index = 0
        self._last_status = ''
        self.setWindowTitle('BeatSync FX V4.1.1 — Timed Beat Effects')
        self.resize(1430, 850)
        self._font_setup()
        self._create_ui()
        self._create_player()
        self._refresh_controls()
        self._autosave = QTimer(self)
        self._autosave.setInterval(60000)
        self._autosave.timeout.connect(self._save_recovery)
        self._autosave.start()
        self.statusBar().showMessage('Offline editor ready. Open a video to begin.')

    def _font_setup(self):
        for filename in ('Anton-Regular.ttf', 'Inter-Variable.ttf'):
            QFontDatabase.addApplicationFont(str(ROOT / 'assets' / 'fonts' / filename))

    def _create_ui(self):
        top = QWidget(); self.setCentralWidget(top)
        whole = QVBoxLayout(top)
        mainbar = QHBoxLayout(); whole.addLayout(mainbar)
        mainbar.addWidget(button('Open video', self.open_video))
        mainbar.addWidget(button('Music / soundtrack', self.open_audio,
                                 'Optional replacement song audio used for beat analysis and output soundtrack'))
        mainbar.addWidget(button('Load project', self.load_project_dialog))
        mainbar.addWidget(button('Save project + template', self.save_project_dialog))
        mainbar.addWidget(button('Load template', self.load_template_dialog))
        mainbar.addWidget(button('Locate FFmpeg', self.choose_ffmpeg))
        mainbar.addStretch()
        self.path_label = QLabel('No video selected')
        self.path_label.setMinimumWidth(180)
        mainbar.addWidget(self.path_label, stretch=2)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        whole.addWidget(splitter, 1)
        left = QWidget(); lyt = QVBoxLayout(left)
        self.preview = Preview(self)
        lyt.addWidget(self.preview, 1)
        draw_bar = QHBoxLayout(); lyt.addLayout(draw_bar)
        draw_bar.addWidget(QLabel('Draw:'))
        for title, kind in [('Box','Object Box'), ('Corners','Object Corners'),
                            ('Brackets','Object Brackets'),('Line','Free Line'),('Arrow','Arrow Line')]:
            draw_bar.addWidget(button(title, lambda checked=False, k=kind: self.begin_draw_shape(k),
                                      'Click then drag on the preview to draw'))
        draw_bar.addWidget(button('Duplicate', self.duplicate_shape))
        key_bar = QHBoxLayout(); lyt.addLayout(key_bar)
        key_bar.addWidget(QLabel('Moving objects:'))
        key_bar.addWidget(button('Set position keyframe', self.keyframe_selected_shape, 'Store selected frame position/size at this time'))
        key_bar.addWidget(button('Clear motion keys', self.clear_shape_keyframes))
        self.track_forward_button = button('Track forward (8s)', self.track_selected_shape,
                                          'Follow selected box for up to 8 seconds using offline NumPy tracking')
        key_bar.addWidget(self.track_forward_button)
        key_bar.addWidget(button('Set corner keyframe', self.keyframe_planar, 'Store four perspective corners at the playhead'))
        key_bar.addStretch()
        seeking = QHBoxLayout(); lyt.addLayout(seeking)
        self.play_button = button('▶ Play', self.toggle_play)
        seeking.addWidget(self.play_button)
        self.time_label = QLabel('00:00.000 / 00:00.000')
        seeking.addWidget(self.time_label)
        self.scrub = QSlider(Qt.Orientation.Horizontal)
        self.scrub.setRange(0, 1000)
        self.scrub.sliderPressed.connect(lambda: setattr(self, '_seeking', True))
        self.scrub.sliderReleased.connect(self._scrub_release)
        self.scrub.sliderMoved.connect(self._scrub_move)
        seeking.addWidget(self.scrub, 1)
        seeks = QHBoxLayout(); lyt.addLayout(seeks)
        seeks.addWidget(button('◀ Previous beat', lambda: self.jump_beat(-1)))
        seeks.addWidget(button('Next beat ▶', lambda: self.jump_beat(1)))
        seeks.addWidget(button('Set first beat here', self.set_first_beat))
        seeks.addStretch()
        self.preview_note = QLabel('Preview plays the original footage; graphics are composited by the same Qt renderer used for export.')
        self.preview_note.setWordWrap(True)
        lyt.addWidget(self.preview_note)
        splitter.addWidget(left)

        self.tabs = QTabWidget()
        splitter.addWidget(self.tabs)
        splitter.setSizes([800, 630])
        self._build_sync_tab()
        self._build_lyrics_tab()
        self._build_events_tab()
        self._build_export_tab()

        footer = QHBoxLayout(); whole.addLayout(footer)
        self.status = QLabel('No online services required')
        footer.addWidget(self.status, 1)
        self.progress = QProgressBar(); self.progress.setRange(0, 100)
        self.progress.setValue(0); self.progress.setMaximumWidth(220)
        footer.addWidget(self.progress)
        self.cancel_button = button('Cancel export', self.cancel_export)
        self.cancel_button.setEnabled(False)
        footer.addWidget(self.cancel_button)
        self.export_button = button('Render MP4', self.export)
        self.export_button.setObjectName('primary')
        footer.addWidget(self.export_button)

        QShortcut(QKeySequence('Ctrl+Space'), self, activated=self.tap_next)
        QShortcut(QKeySequence('Ctrl+S'), self, activated=self.save_project_dialog)

    def _build_sync_tab(self):
        panel = QWidget(); layout = QVBoxLayout(panel)
        beat = QGroupBox('Beat grid (editable after detection)'); layout.addWidget(beat)
        frm = QFormLayout(beat)
        self.bpm = spin_double(40, 260, 120, 2, .5)
        self.t0 = spin_double(0, 9999, 0, 3, .01)
        frm.addRow('Tempo (BPM)', self.bpm)
        frm.addRow('First beat (seconds)', self.t0)
        self.bpm.valueChanged.connect(lambda v: self.change_value('bpm', v))
        self.t0.valueChanged.connect(lambda v: self.change_value('t0', v))
        frm.addRow(button('Analyze beats from audio (offline)', self.analyze))
        frm.addRow(button('Import beatmap.json', self.import_beatmap))
        tip = QLabel('Uses local FFmpeg + NumPy. Kick-based estimates can land on half/double tempo or shift on unusual rhythms. Adjust BPM and first beat by ear.')
        tip.setWordWrap(True); layout.addWidget(tip)
        layout.addWidget(button('×2 BPM', lambda: self.bpm.setValue(min(260, self.bpm.value()*2))))
        layout.addWidget(button('÷2 BPM', lambda: self.bpm.setValue(max(40, self.bpm.value()/2))))

        group = QGroupBox('Timed beat effects — multiple effects, independent durations')
        layout.addWidget(group)
        style = QFormLayout(group)
        self.beat_style = fixed_combo(['Off', *BEAT_EFFECTS], 'Off')
        self.beat_every = spin_int(1, 16, 1)
        self.beat_intensity = spin_int(0, 100, 60)
        self.beat_color = ColorPickerField('#22DDEE')
        self.beat_decay = spin_double(.03, 1.5, .24, 2, .03)
        self.beat_palette = fixed_combo(['Solid','Rainbow'], 'Solid')
        self.beat_start = spin_double(0, 99999, 0, 3, .01)
        self.beat_end = spin_double(0, 99999, 5, 3, .01)
        style.addRow('Effect', self.beat_style)
        playhead_row=QHBoxLayout()
        playhead_row.addWidget(self.beat_start)
        playhead_row.addWidget(button('Use playhead',self._beat_use_playhead))
        style.addRow('Start (seconds)', playhead_row)
        style.addRow('End (seconds)', self.beat_end)
        style.addRow('Every N beats', self.beat_every)
        style.addRow('Intensity (%)', self.beat_intensity)
        style.addRow('Effect color', self.beat_color)
        style.addRow('Color mode', self.beat_palette)
        style.addRow('Pulse length (s)', self.beat_decay)
        actions=QHBoxLayout()
        actions.addWidget(button('Add timed beat effect', self.add_timed_beat_effect))
        actions.addWidget(button('Update selected', self.update_timed_beat_effect))
        actions.addWidget(button('Remove selected', self.remove_timed_beat_effect))
        style.addRow(actions)
        self.beat_timed_table=QTableWidget(0,4)
        self.beat_timed_table.setHorizontalHeaderLabels(['Start','End','Effect','Color'])
        self.beat_timed_table.horizontalHeader().setSectionResizeMode(2,QHeaderView.ResizeMode.Stretch)
        self.beat_timed_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.beat_timed_table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.beat_timed_table.setMinimumHeight(190)
        self.beat_timed_table.itemSelectionChanged.connect(self._select_timed_beat_effect)
        style.addRow(self.beat_timed_table)
        self.legacy_beat_note=QLabel('')
        layout.addWidget(self.legacy_beat_note)
        layout.addWidget(button('Disable old full-video beat effect',self.disable_legacy_beat))
        note=QLabel('Beat effects share the song beat grid but each has its own Start/End, color and settings. '
                    'Several can play together. Rainbow cycles through colors as the beat moves.')
        note.setWordWrap(True)
        layout.addWidget(note)
        self._beat_event_indices=[]
        layout.addStretch()
        self.tabs.addTab(scroll_pane(panel), 'Beat sync')

    def disable_legacy_beat(self):
        self.project.setdefault('options',{})['beat_style']='Off'
        self.refresh_timed_beat_table()
        self.preview.update()
        self.status.setText('Legacy whole-video beat effect disabled; timed effects preserved.')

    def _beat_use_playhead(self):
        start=max(0.0,self.current_time())
        self.beat_start.setValue(start)
        duration=float(self.project.get('duration',0))
        self.beat_end.setValue(min(duration if duration>0 else start+5,start+5))

    def _timed_beat_selected_index(self):
        rows=self.beat_timed_table.selectionModel().selectedRows()
        if not rows: return None
        row=rows[0].row()
        return self._beat_event_indices[row] if row < len(self._beat_event_indices) else None

    def refresh_timed_beat_table(self):
        if not hasattr(self, 'beat_timed_table'): return
        if hasattr(self,'legacy_beat_note'):
            old=self.project.get('options',{}).get('beat_style','Off')
            self.legacy_beat_note.setText('Old project global beat effect still enabled: '+str(old) if old!='Off' else '')
        table=self.beat_timed_table
        previous=self._timed_beat_selected_index()
        self._beat_event_indices=[i for i,e in enumerate(self.project.get('events',[]))
                                  if str(e.get('style','')).startswith('Beat: ')]
        table.blockSignals(True)
        table.setRowCount(len(self._beat_event_indices))
        for row,index in enumerate(self._beat_event_indices):
            e=self.project['events'][index]
            for col,value in enumerate((f"{float(e.get('start',0)):.3f}",
                      f"{float(e.get('end',0)):.3f}",e.get('style','')[6:],
                      'Rainbow' if e.get('beat_palette')=='Rainbow' else e.get('color','#22DDEE'))):
                item=QTableWidgetItem(str(value))
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                table.setItem(row,col,item)
        if previous in self._beat_event_indices:
            table.selectRow(self._beat_event_indices.index(previous))
        table.blockSignals(False)

    def _select_timed_beat_effect(self):
        index=self._timed_beat_selected_index()
        if index is None:return
        e=self.project['events'][index]
        for ctl,val in ((self.beat_start,float(e.get('start',0))),
                        (self.beat_end,float(e.get('end',0))),
                        (self.beat_every,int(e.get('beat_every',1))),
                        (self.beat_intensity,int(e.get('beat_intensity',60))),
                        (self.beat_decay,float(e.get('beat_decay',.24)))):
            ctl.blockSignals(True); ctl.setValue(val); ctl.blockSignals(False)
        for ctl,val in ((self.beat_style,e.get('style','')[6:]),
                        (self.beat_palette,e.get('beat_palette','Solid'))):
            ctl.blockSignals(True);ctl.setCurrentText(val);ctl.blockSignals(False)
        self.beat_color.set_color(e.get('color','#22DDEE'))

    def _beat_editor_values(self):
        start,end=self.beat_start.value(),self.beat_end.value()
        duration=float(self.project.get('duration',0))
        if duration>0:end=min(end,duration)
        if end<=start:
            self.status.setText('Beat effect End must be after Start and within the video.')
            return None
        if self.beat_style.currentText() not in BEAT_EFFECTS:
            self.status.setText('Choose a beat effect, not Off.')
            return None
        return dict(style='Beat: '+self.beat_style.currentText(),start=start,end=end,
                    beat_every=self.beat_every.value(),beat_intensity=self.beat_intensity.value(),
                    beat_decay=self.beat_decay.value(),beat_palette=self.beat_palette.currentText(),
                    color=self.beat_color.value_edit.text())

    def add_timed_beat_effect(self):
        values=self._beat_editor_values()
        if values is None:return
        self.project.setdefault('events',[]).append(values)
        self.refresh_event_table();self.refresh_timed_beat_table();self.preview.update()
        self.status.setText('Timed beat effect added. Repeat to layer additional effects.')

    def update_timed_beat_effect(self):
        index=self._timed_beat_selected_index()
        if index is None:
            self.status.setText('Select a timed beat effect first.');return
        values=self._beat_editor_values()
        if values is None:return
        self.project['events'][index].update(values)
        self.refresh_event_table();self.refresh_timed_beat_table();self.preview.update()

    def remove_timed_beat_effect(self):
        index=self._timed_beat_selected_index()
        if index is None:return
        del self.project['events'][index]
        self.refresh_event_table();self.refresh_timed_beat_table();self.preview.update()

    def _build_lyrics_tab(self):
        panel = QWidget(); layout = QVBoxLayout(panel)
        layout.addWidget(button('🎙  Transcribe with Whisper', self.transcribe, 'Create word timings from your music or video; replaces existing lyrics after confirmation.'))
        layout.addWidget(QLabel('TIMED WORD EDITOR — Edit the Start, End and Word cells directly. Other words keep their timestamps.'))
        self.word_table = QTableWidget(0,5)
        self.word_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.word_table.setSelectionMode(QTableWidget.SelectionMode.ExtendedSelection)
        self.word_table.setHorizontalHeaderLabels(['Start (s)','End (s)','Word','Line','Visible'])
        self.word_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.word_table.setMinimumHeight(330)
        self.word_table.itemChanged.connect(self.update_word)
        layout.addWidget(self.word_table, 1)
        lyric_actions = QHBoxLayout(); layout.addLayout(lyric_actions)
        lyric_actions.addWidget(button('＋ Add word', self.add_word))
        lyric_actions.addWidget(button('Duplicate', self.duplicate_word))
        lyric_actions.addWidget(button('Hide selected', self.delete_words))
        lyric_actions.addWidget(button('Restore selected', self.restore_words))
        lyric_actions.addWidget(button('Merge words', self.merge_words))
        lyric_actions.addWidget(button('Hide Whisper sound captions', self.clean_sound_captions))
        lyric_actions.addWidget(button('Remove selected', self.remove_words))
        note = QLabel('Changes to a word do not retime other words. Hidden cues can be restored, or permanently removed. Use Add word for missing lyrics.')
        note.setWordWrap(True); layout.addWidget(note)
        segment_box = QGroupBox('Segment styles — override the global style only in selected time ranges')
        layout.addWidget(segment_box)
        seg_layout = QVBoxLayout(segment_box)
        help_seg = QLabel('Select one or more word rows, then assign a style to their time range. Every other section keeps the global style. Set global Style = Off if you want text ONLY in assigned segments. Segments can also be edited below.')
        help_seg.setWordWrap(True); seg_layout.addWidget(help_seg)
        seg_buttons = QHBoxLayout(); seg_layout.addLayout(seg_buttons)
        self.segment_style = fixed_combo(['Off','Karaoke line','Word pop','Beat bounce','Type-on','Word Explosion','Ransom Note','Word Avalanche','Comic Impact'],'Word Explosion')
        seg_buttons.addWidget(self.segment_style,1)
        seg_buttons.addWidget(button('Assign to selected words',self.add_lyric_segment))
        seg_buttons.addWidget(button('Remove segment',self.remove_lyric_segment))
        self.segment_table = QTableWidget(0,3)
        self.segment_table.setHorizontalHeaderLabels(['Start (s)','End (s)','Style'])
        self.segment_table.horizontalHeader().setSectionResizeMode(2,QHeaderView.ResizeMode.Stretch)
        self.segment_table.setMinimumHeight(135)
        self.segment_table.itemChanged.connect(self.update_lyric_segment)
        seg_layout.addWidget(self.segment_table)
        opt = QGroupBox('Lyric animation'); layout.addWidget(opt)
        form = QFormLayout(opt)
        self.lyric_style = fixed_combo(['Off','Karaoke line','Word pop','Beat bounce','Type-on','Word Explosion','Ransom Note','Word Avalanche','Comic Impact'],'Karaoke line')
        self.lyric_position = spin_int(5,95,78)
        self.lyric_size = spin_int(20,180,72)
        self.lyric_color = ColorPickerField('#F8F9FF')
        self.highlight_color = ColorPickerField('#D9FA37')
        self.lyric_font = fixed_combo(['Anton','Inter','Arial'],'Anton')
        for name, ctl in [('Style',self.lyric_style),('Vertical position (%)',self.lyric_position),
                          ('Font size (base pixels)',self.lyric_size),('Normal color',self.lyric_color),
                          ('Active-word color',self.highlight_color),
                          ('Font',self.lyric_font)]:
            form.addRow(name,ctl)
        for ctl, key, typ in [(self.lyric_style,'lyric_style','combo'),(self.lyric_position,'lyric_position','int'),
                              (self.lyric_size,'lyric_size','int'),(self.lyric_color,'lyric_color','text'),
                              (self.highlight_color,'highlight_color','text'),
                              (self.lyric_font,'lyric_font','combo')]:
            self._bind_opt(ctl,key,typ)

        self.source_toggle = button('▶  Show source lyrics / Whisper transcription tools', self.toggle_source_tools)
        layout.addWidget(self.source_toggle)
        self.source_panel = QWidget()
        source_layout = QVBoxLayout(self.source_panel)
        source_layout.addWidget(QLabel('SOURCE / WHISPER — importing or regenerating timings replaces the editor table; open only when needed.'))
        self.lyrics_editor = QTextEdit()
        self.lyrics_editor.setPlaceholderText('First lyric line\nSecond lyric line ...')
        self.lyrics_editor.setMaximumHeight(120)
        self.lyrics_editor.textChanged.connect(self.on_lyrics_edit)
        source_layout.addWidget(self.lyrics_editor)
        r1 = QHBoxLayout(); source_layout.addLayout(r1)
        r1.addWidget(button('REPLACE all timings (rough)', self.make_word_times))
        r1.addWidget(button('Start tapping lyrics', self.start_tapping))
        r1.addWidget(button('Tap NEXT word (Ctrl+Space)', self.tap_next))
        self.tap_label = QLabel('Tap: inactive'); source_layout.addWidget(self.tap_label)
        r2 = QHBoxLayout(); source_layout.addLayout(r2)
        r2.addWidget(button('Import LRC / SRT / JSON', self.import_timing))
        r2.addWidget(button('Align supplied lyrics to Whisper times', self.align_lyrics))
        model_group = QGroupBox('Local Whisper transcription (whisper.cpp or Faster-Whisper)')
        source_layout.addWidget(model_group)
        model_layout = QVBoxLayout(model_group)
        model_layout.addWidget(button('Setup Whisper — install Small / Medium / Large', self.setup_whisper))
        model_layout.addWidget(button('Select installed Faster-Whisper model', self.select_installed_whisper))
        self.whisper_backend = fixed_combo(['whisper.cpp (GGML / GGUF)', 'Faster-Whisper (CTranslate2)'], self.settings.get('whisper_backend', 'whisper.cpp (GGML / GGUF)'))
        self.whisper_backend.currentTextChanged.connect(self.save_whisper_folder)
        model_layout.addWidget(self.whisper_backend)
        model_row = QHBoxLayout()
        self.whisper_model_folder = QLineEdit(str(self.settings.get('whisper_model_folder', '')))
        self.whisper_model_folder.setPlaceholderText('Select existing GGML/GGUF model file or CTranslate2 folder')
        self.whisper_model_folder.editingFinished.connect(self.save_whisper_folder)
        model_row.addWidget(self.whisper_model_folder, 1)
        model_row.addWidget(button('Browse / scan...', self.browse_whisper_folder))
        model_row.addWidget(button('Clear', self.clear_whisper_folder))
        model_layout.addLayout(model_row)
        cpp_row = QHBoxLayout()
        self.whisper_cpp_exe = QLineEdit(str(self.settings.get('whisper_cpp_exe', '')))
        self.whisper_cpp_exe.setPlaceholderText('whisper-cli.exe path (whisper.cpp only)')
        self.whisper_cpp_exe.editingFinished.connect(self.save_whisper_folder)
        cpp_row.addWidget(self.whisper_cpp_exe, 1)
        cpp_row.addWidget(button('Browse .exe', self.browse_cpp_exe))
        cpp_row.addWidget(button('Find .exe', self.find_cpp_exe))
        cpp_row.addWidget(button('Clear', lambda: (self.whisper_cpp_exe.clear(), self.save_whisper_folder())))
        model_layout.addLayout(cpp_row)
        self.whisper_device = fixed_combo(['CPU (INT8)', 'NVIDIA CUDA (float16)'], self.settings.get('whisper_device', 'CPU (INT8)'))
        self.whisper_device.currentTextChanged.connect(self.save_whisper_folder)
        model_layout.addWidget(self.whisper_device)
        model_help = QLabel('whisper.cpp accepts existing GGML .bin and .gguf files with whisper-cli.exe (or a compatible main.exe). Faster-Whisper needs model.bin + config.json. No model download or online service is required.')
        model_help.setWordWrap(True)
        model_layout.addWidget(model_help)
        self.source_panel.setVisible(False)
        layout.addWidget(self.source_panel)
        self.tabs.addTab(scroll_pane(panel),'Lyric sync')

    def _build_events_tab(self):
        panel = QWidget(); layout = QVBoxLayout(panel)
        explanation = QLabel('Timed text/graphic hits. Add an event at the current playback position; when Snap is enabled, the start lands on the closest beat.')
        explanation.setWordWrap(True); layout.addWidget(explanation)
        self.snap = QCheckBox('Snap new events to the nearest beat')
        self.snap.setChecked(True)
        self.snap.toggled.connect(lambda b: self.change_option('snap_events',bool(b)))
        layout.addWidget(self.snap)
        # Single selector for all ordinary text and graphic effect events.
        # Countdown and imported image assets need file/target dialogs and keep dedicated buttons.
        effect_bar = QHBoxLayout(); layout.addLayout(effect_bar)
        effect_bar.addWidget(QLabel('Effect:'))
        self.effect_style = fixed_combo(
            ['Text Overlay', 'Pop Open', 'Side Slam', 'Stamp', 'Slide in', 'Beat punch',
             'Wobble', 'Glitch', 'Neon tag', *MOTION_STUDIO_STYLES], 'Echo Typography')
        effect_bar.addWidget(self.effect_style, 1)
        effect_bar.addWidget(button('Add selected effect', self.add_selected_effect))
        actions = QHBoxLayout(); layout.addLayout(actions)
        actions.addWidget(button('Add countdown', self.add_countdown))
        actions.addWidget(button('Add PNG / GIF', self.add_image_overlay))
        self.composite_edit_button = button('Edit selected effect', self.edit_composite_effect)
        actions.addWidget(self.composite_edit_button)
        actions.addWidget(button('Delete selected', self.delete_event))
        self.typography_panel = QWidget(); layout.addWidget(self.typography_panel)
        typography_box = QVBoxLayout(self.typography_panel)
        typography_box.setContentsMargins(0,0,0,0)
        typography_row = QHBoxLayout(); typography_box.addLayout(typography_row)
        typography_row.addWidget(QLabel('Selected effect font:'))
        font_families = sorted(set(QFontDatabase.families()) | {'Anton', 'Inter', 'Arial'})
        self.effect_font = fixed_combo(font_families, 'Anton')
        typography_row.addWidget(self.effect_font, 2)
        typography_row.addWidget(QLabel('Font size:'))
        self.effect_font_size = spin_int(8, 700, 110)
        typography_row.addWidget(self.effect_font_size)
        typography_row.addWidget(button('Apply typography', self.apply_effect_typography))
        type_extra=QHBoxLayout();typography_box.addLayout(type_extra)
        type_extra.addWidget(QLabel('Width %:'))
        self.text_width=spin_int(20,300,100)
        type_extra.addWidget(self.text_width)
        type_extra.addWidget(QLabel('Letter spacing:'))
        self.text_tracking=spin_double(-10,40,0,1,.5)
        type_extra.addWidget(self.text_tracking)
        self.text_outline=QCheckBox('Outline only')
        type_extra.addWidget(self.text_outline)
        type_extra.addWidget(QLabel('Preview: drag text / corner resize / top rotate'))
        typography_row.addWidget(button('Add decorative text', self.add_decorative_text))
        self.frame_panel = QWidget(); layout.addWidget(self.frame_panel)
        frame_box = QVBoxLayout(self.frame_panel);frame_box.setContentsMargins(0,0,0,0)
        frame_row=QHBoxLayout();frame_box.addLayout(frame_row)
        frame_row.addWidget(QLabel('Frame width %:'))
        self.frame_width=spin_int(1,100,90);frame_row.addWidget(self.frame_width)
        frame_row.addWidget(QLabel('Height %:'))
        self.frame_height=spin_int(1,100,88);frame_row.addWidget(self.frame_height)
        frame_row.addWidget(QLabel('Line px:'))
        self.frame_thickness=spin_double(.5,30,2.5,1,.5);frame_row.addWidget(self.frame_thickness)
        frame_row.addWidget(button('Apply frame geometry',self.apply_frame_geometry))
        self.line_panel = QWidget();layout.addWidget(self.line_panel)
        line_props = QHBoxLayout(self.line_panel);line_props.setContentsMargins(0,0,0,0)
        self.line_start_x=spin_double(-100,200,50,2,1)
        self.line_start_y=spin_double(-100,200,50,2,1)
        self.line_end_x=spin_double(-100,200,70,2,1)
        self.line_end_y=spin_double(-100,200,70,2,1)
        self.line_thickness=spin_double(.5,30,2.5,1,.5)
        for name,control in [('Start X%',self.line_start_x),('Start Y%',self.line_start_y),
                             ('End X%',self.line_end_x),('End Y%',self.line_end_y),('Thickness',self.line_thickness)]:
            line_props.addWidget(QLabel(name));line_props.addWidget(control)
        line_props.addWidget(button('Apply line',self.apply_line_geometry))
        self.motion_panel=QWidget();layout.addWidget(self.motion_panel)
        motion_box=QVBoxLayout(self.motion_panel);motion_box.setContentsMargins(0,0,0,0)
        motion_row = QHBoxLayout(); motion_box.addLayout(motion_row)
        motion_row.addWidget(QLabel('Selected effect entry:'))
        self.entry_motion = fixed_combo(list(ENTRIES), 'Default')
        motion_row.addWidget(self.entry_motion, 1)
        motion_row.addWidget(QLabel('Exit:'))
        self.exit_motion = fixed_combo(list(EXITS), 'Default Fade')
        motion_row.addWidget(self.exit_motion, 1)
        motion_row.addWidget(button('Apply motion', self.apply_event_motion))
        motion_timing = QHBoxLayout(); motion_box.addLayout(motion_timing)
        motion_timing.addWidget(QLabel('Entry duration (ms):'))
        self.entry_duration = spin_int(50, 2500, 400)
        motion_timing.addWidget(self.entry_duration)
        motion_timing.addWidget(QLabel('Exit duration (ms):'))
        self.exit_duration = spin_int(50, 2500, 280)
        motion_timing.addWidget(self.exit_duration)
        motion_timing.addStretch()
        self.audio_panel=QWidget();layout.addWidget(self.audio_panel)
        audio_box=QVBoxLayout(self.audio_panel);audio_box.setContentsMargins(0,0,0,0)
        audio_bar = QHBoxLayout(); audio_box.addLayout(audio_bar)
        audio_bar.addWidget(button('Analyze audio bands', self.analyze_bands))
        audio_bar.addWidget(QLabel('React to:'))
        self.audio_react=fixed_combo(['Off','Bass','Midrange','Treble','Overall Energy'],'Off')
        audio_bar.addWidget(self.audio_react,1)
        audio_bar.addWidget(QLabel('Strength %:'))
        self.audio_strength=spin_int(0,200,65)
        audio_bar.addWidget(self.audio_strength)
        audio_bar.addWidget(QLabel('Smooth ms:'))
        self.audio_smooth=spin_int(0,1000,120)
        audio_bar.addWidget(self.audio_smooth)
        audio_bar.addWidget(button('Apply reaction',self.apply_audio_reaction))
        layout.addWidget(QLabel('Analyze music once, then set Bass / Midrange / Treble / Overall Energy per selected effect. Preview and export use saved analysis data.'))
        layout.addWidget(QLabel('Motion settings apply to selected timeline row(s); they never change Start/End timestamps.'))
        layout.addWidget(QLabel('Visual timeline: drag an effect to move it; drag its right edge to resize. Double-click table cells for exact values.'))
        self.effects_timeline=EffectsTimeline(self)
        layout.addWidget(self.effects_timeline)
        self.event_table = QTableWidget(0,8)
        self.event_table.setHorizontalHeaderLabels(['Start','End','Effect','Text','X%','Y%','Size','Color'])
        self.event_table.horizontalHeader().setSectionResizeMode(3,QHeaderView.ResizeMode.Stretch)
        self.event_table.setMinimumHeight(230)
        self.event_table.itemChanged.connect(self.update_event)
        self.event_table.cellDoubleClicked.connect(self.event_color_double_click)
        self.event_table.setToolTip('Double-click a Color cell to choose a color visually')
        self.event_table.itemSelectionChanged.connect(self.on_event_selection_changed)
        layout.addWidget(self.event_table,1)
        notes = QLabel('Countdown: Start is first visible frame, End is the TARGET (zero) timestamp. Text is an optional label.\nImage / GIF: Text stores the selected local image path; Size controls width. All effects stop exactly at End.')
        notes.setWordWrap(True); layout.addWidget(notes)
        layout.addStretch()
        self._update_properties_visibility()
        self.tabs.addTab(scroll_pane(panel),'Effects timeline')

    def _build_export_tab(self):
        panel = QWidget(); layout=QVBoxLayout(panel)
        text = QLabel('Rendering uses local FFmpeg and the same Qt painter as the preview. Video resolution remains identical to the input; source audio is preserved unless replacement music is selected. No online processing.')
        text.setWordWrap(True); layout.addWidget(text)
        form = QFormLayout(); layout.addLayout(form)
        self.fps= fixed_combo(['24','25','30','50','60'],'24')
        self.encoder=fixed_combo(['libx264','h264_nvenc'],'libx264')
        self.crf=spin_int(12,32,18)
        form.addRow('Export FPS',self.fps)
        form.addRow('H.264 encoder',self.encoder)
        form.addRow('Quality (lower = higher)',self.crf)
        self._bind_opt(self.fps,'fps','combo_int')
        self._bind_opt(self.encoder,'encoder','combo')
        self._bind_opt(self.crf,'crf','int')
        warnings = QLabel('NVENC requires an FFmpeg build compiled with h264_nvenc and a working Nvidia driver. If it fails, select libx264.\n\nTranscription is optional. Select a compatible Faster-Whisper CTranslate2 model folder in the Lyrics tab. Models can live anywhere on a local disk, and no downloads are needed when reusing one.\n\nFor manual sync, import Slopcore words.json or tap each lyric word yourself. A plain LRC or SRT contains line-level timings, so its per-word timing is initially approximate.')
        warnings.setWordWrap(True); layout.addWidget(warnings)
        layout.addWidget(button('Render MP4...',self.export))
        layout.addStretch()
        self.tabs.addTab(scroll_pane(panel),'Export / help')

    def _create_player(self):
        if not HAVE_MEDIA:
            self.status.setText('QtMultimedia unavailable: exported video works, but animated footage preview is unavailable.')
            self.player = None
            return
        self.player = QMediaPlayer(self)
        self.audio_output=QAudioOutput(self)
        self.player.setAudioOutput(self.audio_output)
        self.sink = QVideoSink(self)
        self.player.setVideoSink(self.sink)
        self.sink.videoFrameChanged.connect(self.video_frame)
        self.player.positionChanged.connect(self.player_position)
        self.player.playbackStateChanged.connect(self.play_state)
        self.player.errorOccurred.connect(lambda e, desc: self.status.setText('Preview media: '+desc))
        self.music_player=QMediaPlayer(self)
        self.music_output=QAudioOutput(self)
        self.music_player.setAudioOutput(self.music_output)
        self.music_output.setVolume(.8)

    def _bind_opt(self, ctl, key, typ):
        if typ in ('combo','combo_int'):
            ctl.currentTextChanged.connect(lambda value, k=key, tt=typ: self.change_option(k, int(value) if tt=='combo_int' else value))
        elif typ == 'text':
            if isinstance(ctl, ColorPickerField):
                ctl.value_edit.editingFinished.connect(lambda w=ctl, k=key: self.change_option(k, w.value_edit.text()))
            else:
                ctl.editingFinished.connect(lambda w=ctl, k=key: self.change_option(k, w.text().strip()))
        else:
            ctl.valueChanged.connect(lambda val, k=key: self.change_option(k,val))

    def change_value(self,key,value):
        self.project[key]=value
        self.preview.update()

    def change_option(self,key,value):
        self.project['options'][key]=value
        self.preview.update()

    def _refresh_controls(self):
        p=self.project; o=p['options']
        for ctl, key in [(self.bpm,'bpm'),(self.t0,'t0')]:
            ctl.blockSignals(True); ctl.setValue(float(p[key])); ctl.blockSignals(False)
        for ctl, key, is_combo in [(self.beat_style,'beat_style',True),(self.beat_every,'beat_every',False),
                                  (self.beat_intensity,'beat_intensity',False),(self.beat_color,'beat_color',False),
                                  (self.beat_decay,'beat_decay',False),(self.lyric_style,'lyric_style',True),
                                  (self.lyric_position,'lyric_position',False),(self.lyric_size,'lyric_size',False),
                                  (self.lyric_color,'lyric_color',False),(self.highlight_color,'highlight_color',False),
                                  (self.lyric_font,'lyric_font',True),
                                  (self.fps,'fps',True),(self.encoder,'encoder',True),(self.crf,'crf',False)]:
            ctl.blockSignals(True)
            if isinstance(ctl,QComboBox):
                ctl.setCurrentText(str(o[key]))
            elif isinstance(ctl,ColorPickerField):
                ctl.set_color(o[key])
            elif isinstance(ctl,QLineEdit):
                ctl.setText(str(o[key]))
            else:
                ctl.setValue(o[key])
            ctl.blockSignals(False)
        self.snap.blockSignals(True); self.snap.setChecked(bool(o['snap_events'])); self.snap.blockSignals(False)
        self.lyrics_editor.blockSignals(True); self.lyrics_editor.setPlainText(p.get('lyrics_text',''))
        self.lyrics_editor.blockSignals(False)
        self.refresh_word_table(); self.refresh_event_table(); self.refresh_lyric_segments()
        self.path_label.setText(Path(p['video']).name if p.get('video') else 'No video selected')
        self.preview.update()

    def current_time(self):
        return (self.player.position()/1000.0) if self.player and self.project.get('video') else self._manual_time

    def timestamp(self,t):
        t=max(0.0,t)
        return f'{int(t//60):02}:{t%60:06.3f}'

    def _scrub_release(self):
        self._seeking=False
        self.set_time(self.scrub.value()/1000.0*self.project.get('duration',0))

    def _scrub_move(self,v):
        self._manual_time=v/1000.0*self.project.get('duration',0)
        self._time_update(self._manual_time)

    def _time_update(self,t):
        dur=self.project.get('duration',0)
        self.time_label.setText(f'{self.timestamp(t)} / {self.timestamp(dur)}')
        if not self._seeking:
            self.scrub.blockSignals(True)
            self.scrub.setValue(int(1000*t/dur) if dur>0 else 0)
            self.scrub.blockSignals(False)
        self.preview.update()

    def set_time(self,t):
        t=max(0,min(float(t),float(self.project.get('duration',0))))
        self._manual_time=t
        if self.player and self.project.get('video'):
            self.player.setPosition(int(t*1000))
            if self.project.get('audio'):
                self.music_player.setPosition(int(t*1000))
        self._time_update(t)

    def player_position(self,ms):
        if not self._seeking:
            self._manual_time=ms/1000
            self._time_update(self._manual_time)

    def video_frame(self, frame):
        if frame.isValid():
            image=frame.toImage()
            if not image.isNull():
                self.preview.set_frame(image)

    def play_state(self,state):
        playing=state==QMediaPlayer.PlaybackState.PlayingState
        self.play_button.setText('Ⅱ Pause' if playing else '▶ Play')
        if self.project.get('audio'):
            if playing:
                self.music_player.setPosition(self.player.position())
                self.music_player.play()
            else:
                self.music_player.pause()

    def toggle_play(self):
        if not self.player or not self.project.get('video'):
            self.message('Preview needs PySide6 QtMultimedia and a selected video.')
            return
        if self.player.playbackState()==QMediaPlayer.PlaybackState.PlayingState:
            self.player.pause()
        else:
            self.player.play()

    def jump_beat(self,direction):
        period=60/max(1,self.project.get('bpm',120))
        t=self.current_time()
        base=float(self.project.get('t0',0))
        from math import floor,ceil
        k=floor((t-base)/period + (1e-5 if direction<0 else 0))
        if direction>0:
            k+=1
        elif abs(t-(base+k*period))<.01:
            k-=1
        self.set_time(base+k*period)

    def set_first_beat(self):
        self.t0.setValue(self.current_time())

    def _video_loaded(self,path):
        if not self.ffprobe:
            self.message('Select an FFmpeg distribution with both ffmpeg.exe and ffprobe.exe first.')
            return False
        try:
            info=probe_video(self.ffprobe,path)
        except Exception as exc:
            self.message(f'Cannot open video:\n{exc}')
            return False
        self.project.update(info)
        if self.project.get('video') != path and not self.project.get('audio'):
            self.project.pop('audio_envelopes', None)
            self.project.pop('audio_envelopes_source', None)
        self.project['video']=path
        if self.player:
            self.player.stop()
            self.player.setSource(QUrl.fromLocalFile(path))
            if self.project.get('audio'):
                self.audio_output.setMuted(True)
        self.path_label.setText(Path(path).name)
        self.set_time(0)
        self.preview.update()
        return True

    def open_video(self):
        path,_=QFileDialog.getOpenFileName(self,'Choose source footage','',
                   'Video (*.mp4 *.mov *.mkv *.avi *.webm *.m4v);;All files (*)')
        if path:
            self._video_loaded(path)

    def open_audio(self):
        path,_=QFileDialog.getOpenFileName(self,'Optional music or vocal stem','',
                                          'Audio (*.wav *.mp3 *.flac *.m4a *.aac *.ogg);;All files (*)')
        if path:
            if self.project.get('audio') != path:
                self.project.pop('audio_envelopes', None)
                self.project.pop('audio_envelopes_source', None)
            self.project['audio']=path
            if self.player:
                self.music_player.setSource(QUrl.fromLocalFile(path))
                self.audio_output.setMuted(True)
            self.status.setText(f'Replacement audio: {Path(path).name} (used for analysis and export)')

    def choose_ffmpeg(self):
        path,_=QFileDialog.getOpenFileName(self,'Choose ffmpeg executable',str(ROOT/'presets'/'bin'),
                                           'FFmpeg (ffmpeg.exe ffmpeg);;All files (*)')
        if path:
            newexe,newprobe=locate_ffmpeg(path)
            if not newexe or not newprobe:
                self.message('Could not find BOTH ffmpeg and ffprobe. Place ffprobe.exe beside ffmpeg.exe.')
                return
            self.ffmpeg,self.ffprobe=newexe,newprobe
            self.settings['ffmpeg']=newexe; save_settings(self.settings)
            self.status.setText('FFmpeg configured: '+newexe)

    def load_project_dialog(self):
        path,_=QFileDialog.getOpenFileName(self,'Load BeatSync FX project','', 'BeatSync FX JSON (*.json)')
        if not path:
            return
        try:
            p=load_project(path)
        except Exception as exc:
            self.message(str(exc)); return
        self.project=p; self.project_file=path
        self._refresh_controls()
        if p.get('video'):
            self._video_loaded(p['video'])
        if p.get('audio') and self.player:
            self.music_player.setSource(QUrl.fromLocalFile(p['audio']))
            self.audio_output.setMuted(True)
        self.status.setText('Loaded project: '+Path(path).name)

    def load_template_dialog(self):
        path,_=QFileDialog.getOpenFileName(self, 'Load reusable effects template', '',
                                           'BeatSync FX template (*.json)')
        if not path:
            return
        try:
            p=load_effects_template(path, self.project)
        except Exception as exc:
            self.message('Cannot load effects template: '+str(exc)); return
        self.project=p
        # A template does not change the current full project's save destination.
        self._refresh_controls()
        self.status.setText('Applied effects template to current media: '+Path(path).name)

    def save_project_dialog(self):
        if not self.project.get('video'):
            self.message('Open source footage before saving a project.'); return
        if self.project_file:
            path=self.project_file
        else:
            path,_=QFileDialog.getSaveFileName(self,'Save project',
                                    str(ROOT/'output'/'new_project.json'), 'BeatSync FX JSON (*.json)')
        if not path: return
        if not path.lower().endswith('.json'):path+='.json'
        try:
            save_project(path,self.project)
            template_path = str(Path(path).with_name(Path(path).stem + '_effects_template.json'))
            save_effects_template(template_path, self.project)
        except Exception as exc:
            self.message(f'Cannot save project and template: {exc}'); return
        self.project_file=path
        self.status.setText('Saved full project and reusable effects template: '+template_path)

    def _save_recovery(self):
        # Never overwrite a meaningful previous recovery with an empty startup project.
        if self.project.get('video') and self.project.get('duration',0)>0:
            try:
                save_project(str(ROOT/'presets'/'setsave'/'last_autosave.json'),self.project)
            except OSError:
                pass

    def analyze(self):
        audio=self.project.get('audio') or self.project.get('video')
        if not audio or not self.ffmpeg:
            self.message('Open an audio or video file and select FFmpeg first.'); return
        if self._worker and self._worker.isRunning():
            self.message('An analysis is already running.'); return
        self.status.setText('Analyzing rhythm locally...')
        self._worker=BeatWorker(self.ffmpeg,audio)
        self._worker.ready.connect(self.beats_ready)
        self._worker.failed.connect(lambda e:self.message('Beat analysis failed:\n'+e))
        self._worker.start()

    def import_beatmap(self):
        path,_=QFileDialog.getOpenFileName(self,'Import Slopcore beatmap.json','',
                                           'Beatmap JSON (*.json)')
        if not path:return
        try:
            data=import_beatmap(path)
            self.bpm.setValue(data['bpm']);self.t0.setValue(data['t0'])
            self.status.setText('Loaded beat grid from '+Path(path).name)
        except (OSError, ValueError, KeyError) as exc:
            self.message('Cannot import beatmap: '+str(exc))

    def beats_ready(self,result):
        self.bpm.setValue(result['bpm']); self.t0.setValue(result['t0'])
        self.status.setText(f"Detected {result['bpm']} BPM, first beat {result['t0']:.3f}s; rough grid match {result['confidence']:.2f}. Check by listening.")

    def on_lyrics_edit(self):
        self.project['lyrics_text']=self.lyrics_editor.toPlainText()

    def make_word_times(self):
        text=self.lyrics_editor.toPlainText()
        if self.project.get('words') and QMessageBox.question(self, 'Overwrite all word timings?', 'This will DESTROY all existing word start/end times and redistribute words across the video. Continue?') != QMessageBox.StandardButton.Yes:
            return
        self.project['words']=split_lyrics(text,self.project['duration'],self.current_time())
        self.refresh_word_table()
        self.status.setText('Rough timings generated. Use Tap NEXT word or edit start/end columns for exact sync.')
        self.preview.update()

    def align_lyrics(self):
        from .lyric_align import align_known_lyrics
        text = self.lyrics_editor.toPlainText().strip()
        try:
            aligned = align_known_lyrics(text, self.project.get('words', []),
                                         self.project.get('duration', 0))
        except ValueError as exc:
            self.message(str(exc)); return
        from PySide6.QtWidgets import QMessageBox
        if QMessageBox.question(self, 'Replace word timings?',
                'Align supplied lyrics against existing recognized word timestamps?\n'
                'This replaces the current timing table. Save your project first if you wish to keep it.') != QMessageBox.StandardButton.Yes:
            return
        self.project['words'] = aligned
        self.refresh_word_table()
        self.preview.update()
        estimated = sum(bool(x.get('estimated')) for x in aligned)
        self.status.setText(f'Aligned {len(aligned)} supplied words; {estimated} times estimated. Review timing before exporting.')

    def start_tapping(self):
        from .core import split_lyrics
        self._tap_words=split_lyrics(self.lyrics_editor.toPlainText(),
                                     max(10,self.project.get('duration',0)),0)
        if not self._tap_words:
            self.message('Enter lyrics first.');return
        self._tap_index=0
        self.project['words']=self._tap_words
        self.tap_label.setText(f'Tap next: {self._tap_words[0]["w"]} — press Ctrl+Space on each sung word')
        self.refresh_word_table()

    def tap_next(self):
        if not self._tap_words or self._tap_index>=len(self._tap_words):
            return
        now=self.current_time()
        i=self._tap_index
        if i and now <= self._tap_words[i-1]['t0']:
            self.message('Tap each word at a later timestamp.'); return
        self._tap_words[i]['t0']=round(now,3)
        self._tap_words[i]['t1']=round(now+.35,3)
        if i:
            self._tap_words[i-1]['t1']=round(max(self._tap_words[i-1]['t0']+.03,now),3)
        self._tap_index+=1
        if self._tap_index<len(self._tap_words):
            self.tap_label.setText(f'Tap {self._tap_index+1}/{len(self._tap_words)}: {self._tap_words[self._tap_index]["w"]}')
        else:
            self.tap_label.setText('Tapping complete. Refine end times in the word table.')
        self.refresh_word_table()
        self.preview.update()

    def import_timing(self):
        path,_=QFileDialog.getOpenFileName(self,'Load timed lyrics','',
                          'Timed lyrics (*.json *.lrc *.srt);;All files (*)')
        if not path:return
        try:
            if Path(path).suffix.lower()=='.json':
                obj=json.loads(Path(path).read_text(encoding='utf-8-sig'))
                words=import_slopcore_words(path) if 'lines' in obj else import_whisper_json(path)
            else:
                words=import_subtitles(path)
            if not words: raise ValueError('No words in this file')
        except Exception as exc:
            self.message(f'Cannot import timestamps:\n{exc}'); return
        self.set_words(words)
        self.status.setText(f'Imported {len(words)} timed words from {Path(path).name}')

    def set_words(self,words):
        from .lyric_cleanup import mute_sound_captions
        muted = mute_sound_captions(words)
        self.project['words']=words
        groups={}
        for item in words:
            if not item.get('hidden'):
                groups.setdefault(item['line'],[]).append(item['w'])
        self.project['lyrics_text']='\n'.join(' '.join(g) for g in groups.values())
        self.lyrics_editor.blockSignals(True)
        self.lyrics_editor.setPlainText(self.project['lyrics_text'])
        self.lyrics_editor.blockSignals(False)
        self._tap_words=[]; self._tap_index=0
        self.refresh_word_table()
        self.preview.update()
        if muted:
            self.status.setText(f'Suppressed {muted} Whisper sound captions without changing timings. Restore any false positives in the table.')

    def save_whisper_folder(self, *_args):
        folder = self.whisper_model_folder.text().strip().strip('"')
        self.settings['whisper_model_folder'] = folder
        self.settings['whisper_device'] = self.whisper_device.currentText()
        self.settings['whisper_backend'] = self.whisper_backend.currentText()
        self.settings['whisper_cpp_exe'] = self.whisper_cpp_exe.text().strip().strip('\"')
        save_settings(self.settings)

    def clear_whisper_folder(self):
        self.whisper_model_folder.clear()
        self.save_whisper_folder()
        self.status.setText('Whisper folder cleared. Select a compatible local model to transcribe.')

    def browse_cpp_exe(self):
        path, _ = QFileDialog.getOpenFileName(self, 'Select whisper.cpp executable',
            self.whisper_cpp_exe.text() or str(ROOT), 'Executable (*.exe);;All files (*)')
        if path:
            self.whisper_cpp_exe.setText(path)
            self.save_whisper_folder()

    def find_cpp_exe(self):
        folder = QFileDialog.getExistingDirectory(self, 'Scan folder for whisper-cli.exe', str(ROOT))
        if not folder:
            return
        from .whisper_cpp import find_cpp_executables
        matches = find_cpp_executables(folder)
        if not matches:
            self.message('No whisper-cli.exe or main.exe found under this folder.')
            return
        choices = [str(m) for m in matches]
        selected, ok = QInputDialog.getItem(self, 'Choose whisper.cpp executable', 'Executable:', choices, 0, False)
        if ok:
            self.whisper_cpp_exe.setText(selected)
            self.save_whisper_folder()

    def browse_whisper_folder(self):
        if self.whisper_backend.currentText().startswith('whisper.cpp'):
            selected, _ = QFileDialog.getOpenFileName(self, 'Choose whisper.cpp model (or Cancel to scan a folder)',
                self.whisper_model_folder.text() or str(ROOT / 'models'), 'Whisper models (*.bin *.gguf);;All files (*)')
            if not selected:
                parent = QFileDialog.getExistingDirectory(self, 'Scan folder for whisper.cpp models', str(ROOT / 'models'))
                if not parent:
                    return
                from .whisper_cpp import find_cpp_models
                matches = find_cpp_models(parent)
                if not matches:
                    self.message('No GGML/GGUF Whisper models recognized under ' + parent)
                    return
                options = [str(x) for x in matches]
                selected, ok = QInputDialog.getItem(self, 'Choose whisper.cpp model', 'Model:', options, 0, False)
                if not ok:
                    return
        else:
            current = self.whisper_model_folder.text().strip()
            initial = current if current and Path(current).is_dir() else str(ROOT / 'models')
            folder = QFileDialog.getExistingDirectory(self, 'Select Faster-Whisper model folder', initial)
            if not folder:
                return
            from .model_finder import find_ct2_models, diagnostic
            matches = find_ct2_models(Path(folder))
            if not matches:
                self.message('Whisper scan results:\n\n' + diagnostic(Path(folder)))
                return
            options = [str(x) for x in matches]
            selected, ok = QInputDialog.getItem(self, 'Choose Faster-Whisper model', 'Model:', options, 0, False)
            if not ok:
                return
        self.whisper_model_folder.setText(selected)
        self.save_whisper_folder()
        self.status.setText('Selected model: ' + selected)

    def select_installed_whisper(self):
        from .model_finder import find_ct2_models
        base = ROOT / 'models'
        options = [str(p) for p in find_ct2_models(base)]
        if not options:
            self.message('No locally installed Faster-Whisper models found in models/.\nUse Setup Whisper first, or Browse / scan for a model elsewhere.')
            return
        selected, ok = QInputDialog.getItem(self, 'Select installed Whisper model', 'Model:', options, 0, False)
        if ok:
            self.whisper_backend.setCurrentText('Faster-Whisper (CTranslate2)')
            self.whisper_model_folder.setText(selected)
            self.save_whisper_folder()
            self.status.setText('Selected local Whisper model: ' + Path(selected).name)

    def setup_whisper(self):
        dialog = QDialog(self)
        dialog.setWindowTitle('Setup Whisper — optional offline transcription')
        dialog.resize(550, 340)
        layout = QVBoxLayout(dialog)
        info = QLabel('Choose a model to download into the portable models/ folder.\n'
                      'Downloads require internet once; transcription then works offline.\n'
                      'Size and VRAM are approximate; actual requirements depend on device and settings.')
        info.setWordWrap(True)
        layout.addWidget(info)
        choices = QButtonGroup(dialog)
        model_info = [
            ('small', 'Small — ~460 MB download / ~1 GB VRAM — fastest, less accurate'),
            ('medium', 'Medium — ~1.5 GB download / ~2–3 GB VRAM — RECOMMENDED'),
            ('large-v3', 'Large-v3 — ~3.1 GB download / ~4–6 GB VRAM — best accuracy, slower'),
        ]
        for name, description in model_info:
            radio = QRadioButton(description)
            radio.setProperty('whisper_model', name)
            radio.setChecked(name == 'medium')
            choices.addButton(radio)
            layout.addWidget(radio)
        status = QLabel('Install another model later using this same dialog. Existing models are kept.')
        status.setWordWrap(True)
        layout.addWidget(status)
        install = QPushButton('Install selected model')
        layout.addWidget(install)
        close = QPushButton('Close')
        close.clicked.connect(dialog.reject)
        layout.addWidget(close)
        proc = QProcess(dialog)
        def install_selected():
            chosen = choices.checkedButton()
            if not chosen: return
            model = chosen.property('whisper_model')
            target = ROOT / 'models' / ('whisper-' + model)
            if (target / 'model.bin').is_file() and (target / 'config.json').is_file():
                self.whisper_backend.setCurrentText('Faster-Whisper (CTranslate2)')
                self.whisper_model_folder.setText(str(target))
                self.save_whisper_folder()
                status.setText('Model is installed and selected: ' + str(target))
                return
            python = ROOT / 'environments' / 'beatsyncfx' / 'Scripts' / 'python.exe'
            if not python.is_file():
                status.setText('Run setup_windows.bat first to create the portable Python environment.')
                return
            install.setEnabled(False)
            status.setText('Installing dependencies and downloading ' + model + '… This may take a while.')
            proc.setProgram(str(python))
            proc.setArguments(['-m', 'pip', 'install', '--disable-pip-version-check', '-r', str(ROOT / 'helpers' / 'requirements_whisper.txt')])
            proc.setWorkingDirectory(str(ROOT))
            def dependencies_done(code, _state):
                try: proc.finished.disconnect(dependencies_done)
                except (RuntimeError, TypeError): pass
                if code:
                    status.setText('Dependency installation failed. Check network access and Python environment.')
                    install.setEnabled(True)
                    return
                proc.setArguments(['-m', 'helpers.download_whisper', model])
                def download_done(exitcode, _exitstatus):
                    try: proc.finished.disconnect(download_done)
                    except (RuntimeError, TypeError): pass
                    install.setEnabled(True)
                    if exitcode == 0 and (target / 'model.bin').is_file():
                        self.whisper_backend.setCurrentText('Faster-Whisper (CTranslate2)')
                        self.whisper_model_folder.setText(str(target))
                        self.save_whisper_folder()
                        status.setText('Installed and selected ' + model + '. You can install others later.')
                    else:
                        status.setText('Download failed. Check internet and disk space; retry to resume.')
                proc.finished.connect(download_done)
                proc.start()
            proc.finished.connect(dependencies_done)
            proc.start()
        install.clicked.connect(install_selected)
        dialog.exec()

    def transcribe(self):
        source = self.project.get('audio') or self.project.get('video')
        if not source:
            self.message('Load an audio track or video first.')
            return
        if self.project.get('words'):
            answer = QMessageBox.question(self, 'Replace existing lyrics?',
                'Using Whisper will remove earlier lyric edits and timings. Continue?',
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No)
            if answer != QMessageBox.StandardButton.Yes: return
        self.save_whisper_folder()
        folder = self.settings.get('whisper_model_folder', '').strip()
        if not folder:
            self.message('No Whisper model selected. Use Setup Whisper in the source tools section, or browse for an existing model.')
            return
        model = Path(folder).expanduser()
        if self.whisper_backend.currentText().startswith('whisper.cpp'):
            from .whisper_cpp import is_cpp_model, is_cpp_executable, find_cpp_models
            if not is_cpp_model(model):
                candidates = find_cpp_models(model)
                if not candidates:
                    self.message('No whisper.cpp GGML/GGUF model file found at: ' + str(model))
                    return
                choices = [str(x) for x in candidates]
                selected, ok = QInputDialog.getItem(self, 'Choose whisper.cpp model', 'Model:', choices, 0, False)
                if not ok:
                    return
                model = Path(selected)
                self.whisper_model_folder.setText(str(model))
                self.save_whisper_folder()
            exe = Path(self.whisper_cpp_exe.text().strip())
            if not is_cpp_executable(exe):
                self.message('Choose your existing whisper-cli.exe (or main.exe) first.')
                return
            if self._worker and self._worker.isRunning():
                self.message('Another processing task is running.')
                return
            self._start_cpp_transcription(source, str(model), str(exe))
            return
        from .model_finder import find_ct2_models
        if not (model / 'model.bin').is_file() or not (model / 'config.json').is_file():
            matches = find_ct2_models(model)
            if len(matches) == 1:
                model = matches[0]
                self.whisper_model_folder.setText(str(model))
                self.save_whisper_folder()
            elif len(matches) > 1:
                selected, ok = QInputDialog.getItem(self, 'Choose Whisper model',
                    f'Found {len(matches)} models:', [str(m) for m in matches], 0, False)
                if not ok:
                    return
                model = Path(selected)
                self.whisper_model_folder.setText(str(model))
                self.save_whisper_folder()
        if not (model / 'model.bin').is_file() or not (model / 'config.json').is_file():
            from .model_finder import diagnostic
            self.message('Whisper scan results:\n\n' + diagnostic(model))
            return
        if self._worker and self._worker.isRunning():
            self.message('Another processing task is running.')
            return
        separate = QMessageBox.question(self, 'Vocal source',
            'Transcribe from a separate vocals-only audio file?\n\n'
            'Choose Yes for a vocal stem (best results).\n'
            'Choose No to transcribe the selected song/video.',
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.No)
        if separate == QMessageBox.StandardButton.Cancel:
            return
        if separate == QMessageBox.StandardButton.Yes:
            candidate, _ = QFileDialog.getOpenFileName(self, 'Choose vocal stem', '',
                'Audio (*.wav *.flac *.mp3 *.m4a *.ogg);;All files (*)')
            if not candidate:
                return
            source = candidate
        use_cuda = self.whisper_device.currentText().startswith('NVIDIA')
        self.status.setText(f'Offline Whisper transcription running ({"CUDA float16" if use_cuda else "CPU INT8"})...')
        self._worker = WhisperWorker(source, str(model), device='cuda' if use_cuda else 'cpu',
                                     compute_type='float16' if use_cuda else 'int8')
        self._worker.ready.connect(self.transcribed)
        self._worker.failed.connect(lambda e: self.message('Offline transcription failed:\n' + e))
        self._worker.start()

    def _start_cpp_transcription(self, source, model, executable):
        separate = QMessageBox.question(self, 'Vocal source',
            'Use a separate vocals-only audio file? (Recommended for songs)',
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.No)
        if separate == QMessageBox.StandardButton.Cancel:
            return
        if separate == QMessageBox.StandardButton.Yes:
            source, _ = QFileDialog.getOpenFileName(self, 'Choose vocal audio', '', 'Audio files (*.wav *.flac *.mp3 *.m4a *.ogg);;All files (*)')
            if not source:
                return
        ffmpeg, _ffprobe = locate_ffmpeg(str(self.settings.get('ffmpeg', '')))
        if not ffmpeg:
            self.message('FFmpeg executable not configured.')
            return
        self._worker = WhisperCppWorker(source, model, executable, ffmpeg)
        self._worker.ready.connect(self.transcribed)
        self._worker.failed.connect(lambda e: self.message('whisper.cpp transcription failed:\n' + e))
        self.status.setText('Offline whisper.cpp transcription running...')
        self._worker.start()

    def transcribed(self, words):
        self.set_words(words)
        self.status.setText(f'Transcribed {len(words)} words locally. Verify every word timestamp before export.')

    def toggle_source_tools(self):
        visible = not self.source_panel.isVisible()
        self.source_panel.setVisible(visible)
        self.source_toggle.setText(('▼  Hide' if visible else '▶  Show') + ' source lyrics / Whisper transcription tools')

    def add_word(self):
        word, ok = QInputDialog.getText(self, 'Add timed word', 'Word to display:')
        if not ok or not word.strip(): return
        default_start = self.current_time()
        selected = sorted({item.row() for item in self.word_table.selectedItems()})
        words = self.project.setdefault('words', [])
        if selected and selected[-1] < len(words):
            default_start = float(words[selected[-1]]['t1'])
        start, ok = QInputDialog.getDouble(self, 'Word start time', 'Start (seconds):', default_start, 0, 100000, 3)
        if not ok: return
        end, ok = QInputDialog.getDouble(self, 'Word end time', 'End (seconds):', start + .35, start + .001, 100000, 3)
        if not ok: return
        line = int(words[selected[-1]].get('line', 0)) if selected else (int(words[-1].get('line', 0)) if words else 0)
        cue = {'w': word.strip(), 't0': start, 't1': end, 'line': line, 'hidden': False}
        words.append(cue)
        words.sort(key=lambda x: (float(x['t0']), float(x['t1'])))
        self.refresh_word_table()
        self.word_table.selectRow(words.index(cue))
        self.preview.update()
        self.status.setText(f'Added {word.strip()} at {start:.3f}–{end:.3f}s; existing timestamps preserved.')

    def duplicate_word(self):
        import copy
        rows = sorted({x.row() for x in self.word_table.selectedItems()})
        words = self.project.setdefault('words', [])
        if not rows: self.message('Select a word to duplicate.'); return
        for i in reversed(rows):
            if i < len(words): words.insert(i+1, copy.deepcopy(words[i]))
        self.refresh_word_table(); self.preview.update()
        self.status.setText('Duplicated selected words; double-click to edit their time/text.')

    def remove_words(self):
        rows = sorted({x.row() for x in self.word_table.selectedItems()}, reverse=True)
        if not rows: return
        if QMessageBox.question(self, 'Remove timed words?',
                'Permanently remove selected word cues? Other word timestamps will NOT change.') != QMessageBox.StandardButton.Yes:
            return
        for i in rows:
            if i < len(self.project['words']): self.project['words'].pop(i)
        self.refresh_word_table(); self.preview.update()
        self.status.setText(f'Removed {len(rows)} cues without retiming the rest.')

    def merge_words(self):
        indices=sorted({x.row() for x in self.word_table.selectedItems()})
        words=self.project.get('words',[])
        if len(indices)<2 or indices != list(range(indices[0],indices[-1]+1)):
            self.message('Select at least two adjacent word rows to merge.')
            return
        selected=[words[i] for i in indices]
        # Only merge words from the same line; leave all following timestamps intact.
        if len({w.get('line',0) for w in selected})!=1:
            self.message('Merged words must belong to the same lyric line.')
            return
        label,ok=QInputDialog.getText(self,'Merge words','Correct combined word:',text=''.join(str(w['w']) for w in selected))
        if not ok or not label.strip():return
        first=words[indices[0]]
        first['w']=label.strip();first['t0']=min(float(w['t0']) for w in selected)
        first['t1']=max(float(w['t1']) for w in selected)
        first['hidden']=False
        for idx in reversed(indices[1:]): words.pop(idx)
        self.refresh_word_table();self.preview.update()
        self.status.setText('Merged selected words; all other timestamps preserved.')

    def words_to_source(self):
        # One-way copy; never retime the existing words.
        groups = {}
        for word in self.project.get('words', []):
            if not word.get('hidden'):
                groups.setdefault(int(word.get('line',0)),[]).append(str(word.get('w','')))
        text = '\n'.join(' '.join(parts) for _,parts in sorted(groups.items()))
        self.lyrics_editor.setPlainText(text)
        self.status.setText('Updated source lyrics; all word timestamps preserved.')

    def add_lyric_segment(self):
        indices = sorted({x.row() for x in self.word_table.selectedItems()})
        words = self.project.get('words',[])
        if not indices:
            self.message('Select word rows in the timed word table first (Shift-click to select a range).')
            return
        chosen = [words[i] for i in indices]
        start = min(float(x['t0']) for x in chosen)
        end = max(float(x['t1']) for x in chosen)
        self.project.setdefault('lyric_segments', []).append({'start':round(start,3),'end':round(end,3),'style':self.segment_style.currentText()})
        self.refresh_lyric_segments(); self.preview.update()
        self.status.setText(f'Assigned {self.segment_style.currentText()} to {start:.2f}–{end:.2f}s.')

    def remove_lyric_segment(self):
        segs=self.project.setdefault('lyric_segments',[])
        for i in sorted({x.row() for x in self.segment_table.selectedItems()},reverse=True):
            if i < len(segs): segs.pop(i)
        self.refresh_lyric_segments(); self.preview.update()

    def refresh_lyric_segments(self):
        if not hasattr(self,'segment_table'): return
        table=self.segment_table; table.blockSignals(True)
        segs=self.project.setdefault('lyric_segments',[])
        table.setRowCount(len(segs))
        for i,seg in enumerate(segs):
            for j,v in enumerate((seg['start'],seg['end'],seg['style'])):
                table.setItem(i,j,QTableWidgetItem(str(v)))
        table.blockSignals(False)

    def update_lyric_segment(self,item):
        segs=self.project.setdefault('lyric_segments',[])
        i,j=item.row(),item.column()
        if i>=len(segs): return
        key=['start','end','style'][j]
        try:
            value=float(item.text()) if j<2 else item.text().strip()
            if j==0 and value>=float(segs[i]['end']): raise ValueError()
            if j==1 and value<=float(segs[i]['start']): raise ValueError()
            if j==2 and value not in [self.segment_style.itemText(k) for k in range(self.segment_style.count())]: raise ValueError()
            segs[i][key]=value
        except ValueError: self.refresh_lyric_segments()
        self.preview.update()

    def refresh_word_table(self):
        self.word_table.blockSignals(True)
        words=self.project.get('words',[])
        self.word_table.setRowCount(len(words))
        for i,w in enumerate(words):
            for j,v in enumerate((f'{float(w["t0"]):.3f}',f'{float(w["t1"]):.3f}',w['w'],str(w.get('line',0)), 'No' if w.get('hidden') else 'Yes')):
                item = QTableWidgetItem(str(v))
                if w.get('hidden'):
                    from PySide6.QtGui import QColor
                    item.setForeground(QColor('#8c929b'))
                self.word_table.setItem(i,j,item)
        self.word_table.blockSignals(False)

    def update_word(self,item):
        i,j=item.row(),item.column()
        if i>=len(self.project['words']):return
        word=self.project['words'][i]
        try:
            name=('t0','t1','w','line','hidden')[j]
            v=(float(item.text()) if j in (0,1) else int(item.text()) if j==3 else item.text() if j==2 else item.text().strip().lower() in ('no','false','0','hidden'))
            if j==1 and v<=word['t0']: raise ValueError('End must be after start')
            if j==0 and (v<0 or v>=word['t1']): raise ValueError('Start must be nonnegative and before end')
            if j == 2 and not v.strip(): raise ValueError('Word must not be empty')
            word[name]=v
        except (ValueError, TypeError):
            self.refresh_word_table();return
        if j == 4: self.refresh_word_table()
        self.preview.update()

    def delete_words(self):
        from .lyric_cleanup import hide_words_at_indices
        rows = {v.row() for v in self.word_table.selectedItems()}
        count = hide_words_at_indices(self.project['words'], rows)
        self.refresh_word_table(); self.preview.update()
        self.status.setText(f'Hid {count} word(s). All original timestamps remain unchanged.')

    def restore_words(self):
        from .lyric_cleanup import restore_words_at_indices
        rows = {v.row() for v in self.word_table.selectedItems()}
        count = restore_words_at_indices(self.project['words'], rows)
        self.refresh_word_table(); self.preview.update()
        self.status.setText(f'Restored {count} word(s) with their original timestamps.')

    def clean_sound_captions(self):
        from .lyric_cleanup import mute_sound_captions
        count = mute_sound_captions(self.project['words'])
        self.refresh_word_table(); self.preview.update()
        self.status.setText(f'Hid {count} soundtrack captions; all timestamps preserved.')

    def begin_draw_shape(self, kind):
        """Create an immediately visible shape; mouse tools edit it afterward.

        Previously this button only armed an invisible drawing state, leaving users
        with no shape at all until a separate mouse drag succeeded.
        """
        if not self.project.get('video'):
            self.message('Load a video first to place a frame or line.')
            return
        if self.player and self.player.playbackState()==QMediaPlayer.PlaybackState.PlayingState:
            self.player.pause()
        now = float(self.current_time())
        duration = max(now + 0.10, float(self.project.get('duration', now + 15)))
        end = min(duration, now + 15.0)
        # A full two-second interval whenever possible, except near video end.
        if end <= now:
            self.message('Move the playhead to a position before the end of the video.')
            return
        event = {
            'style': kind, 'start': round(now, 3), 'end': round(end, 3),
            'x': 50.0, 'y': 50.0, 'color': '#00E5FF',
            'opacity': 100, 'line_thickness': 4.0, 'rotation': 0,
            'text': '', 'frame_width': 30.0, 'frame_height': 40.0,
            'end_x': 78.0, 'end_y': 60.0,
        }
        if kind in ('Free Line', 'Arrow Line'):
            event['x'], event['y'] = 25.0, 35.0
        self.project.setdefault('events', []).append(event)
        index = len(self.project['events']) - 1
        self.preview.draw_kind = None
        self.preview.drag_mode = None
        self.preview.selected = index
        self.refresh_event_table()
        self.event_table.selectRow(index)
        self.preview.update()
        self.effects_timeline.update()
        self.status.setText('%s added. Drag inside to move; drag any of the four cyan corner squares to resize. Start/End are editable in the effects table.' % kind)

    def keyframe_selected_shape(self):
        from .shape_keyframes import capture, geometry_at
        idx=getattr(self.preview,'selected',None)
        if idx is None or idx >= len(self.project.get('events',[])):
            self.status.setText('Select a frame or line first.')
            return
        ev=self.project['events'][idx]
        if ev.get('style') not in self.preview.SHAPES:
            self.status.setText('Keyframes are available for frames, lines and arrows.')
            return
        t=self.current_time()
        if not float(ev['start']) <= t < float(ev['end']):
            self.status.setText('Move the playhead inside this object’s Start/End range first.')
            return
        # Capture current editable geometry at this time; preserves geometry for earlier keys.
        count=capture(ev,t)
        self.status.setText(f'{count} keyframe(s) saved. Seek later, move/resize the shape; its new position will be keyed automatically.')
        self.preview.update()

    def track_selected_shape(self):
        """Populate ordinary editable shape keyframes with the offline tracker."""
        from .shape_keyframes import geometry_at
        index = getattr(self.preview, 'selected', None)
        events = self.project.get('events', [])
        if index is None or not (0 <= index < len(events)):
            self.status.setText('Select an object box or corner frame first.')
            return
        event = events[index]
        if event.get('style') not in ('Object Box', 'Object Corners', 'Object Brackets', 'Linked Callout'):
            self.status.setText('Track forward is available for Box, Corners and Brackets.')
            return
        if self._shape_tracker and self._shape_tracker.is_alive():
            self.status.setText('Tracking is already in progress.')
            return
        video = self.project.get('video')
        if not video or not Path(video).is_file() or not self.ffmpeg:
            self.status.setText('Open a video and configure FFmpeg first.')
            return
        if self.player and self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.player.pause()
        start = self.current_time()
        end = min(float(event.get('end', 0)), float(self.project.get('duration', 0)), start+8.0)
        if start < float(event.get('start', 0)) or end-start < .25:
            self.status.setText('Seek inside the selected frame’s Start/End range, with at least 0.25s remaining.')
            return
        shape = geometry_at(event, start)
        # The tracker works in 640px decoded coordinates; shape geometry is normalized percentages.
        original_w, original_h = int(self.project['width']), int(self.project['height'])
        decoded_w = 640
        decoded_h = max(2, round(original_h*decoded_w/original_w)//2*2)
        cx, cy = decoded_w*float(shape.get('x',50))/100, decoded_h*float(shape.get('y',50))/100
        bw = max(8, round(decoded_w*float(shape.get('frame_width',20))/100))
        bh = max(8, round(decoded_h*float(shape.get('frame_height',30))/100))
        x = max(0, min(decoded_w-bw, round(cx-bw/2)))
        y = max(0, min(decoded_h-bh, round(cy-bh/2)))
        if bw >= decoded_w or bh >= decoded_h:
            self.status.setText('Choose a smaller frame before tracking.')
            return
        self._shape_track_index = index
        self._shape_track_start = start
        self._shape_tracker = ShapeTrackerWorker(self.ffmpeg, video, start, end-start,
                                                  (x,y,bw,bh))
        # Poll Python-thread progress and outcome from the GUI event loop.
        self.track_forward_button.setEnabled(False)
        self.track_forward_button.setText('Tracking…')
        self.status.setText(f'Tracking object from {start:.2f}s to {end:.2f}s ({end-start:.2f}s). Tracking only covers the time remaining in this object.')
        self._log_tracking_diagnostic('start', f'from {start:.3f} to {end:.3f} seconds', index)
        # Run directly on the GUI thread to avoid an unreliable background-worker
        # handoff.  For short clips this is preferable to silently dropping keys.
        # The operation is bounded by decode_segment's 45-second FFmpeg timeout.
        from PySide6.QtWidgets import QApplication
        QApplication.processEvents()
        try:
            self._log_tracking_diagnostic('direct run entered', f'index={index}', index)
            self._shape_tracker.run()
            self._log_tracking_diagnostic('direct run returned', repr(self._shape_tracker.outcome), index)
        except BaseException as exc:
            self._shape_tracker.outcome = ('error', f'{type(exc).__name__}: {exc}')
            self._log_tracking_diagnostic('direct run exception', repr(exc), index)
        finally:
            self._tracking_worker_exited(self._shape_tracker)

    def _check_tracking_completion(self):
        """Collect worker progress and completion on the GUI thread."""
        worker = self._shape_tracker
        if worker is None:
            self._track_completion_timer.stop()
            return
        if worker.is_alive():
            done, total = worker.progress
            if total:
                self._tracking_progress(done, total)
            return
        self._tracking_worker_exited(worker)

    def _reset_tracking_button(self):
        self.track_forward_button.setEnabled(True)
        self.track_forward_button.setText('Track forward (8s)')

    def _tracking_worker_exited(self, worker=None):
        """Apply the worker result before releasing the selected shape index.

        Worker results are collected only from the GUI thread after exit.
        """
        if worker is None:
            worker = self._shape_tracker
        if worker is None:
            self._reset_tracking_button()
            return
        if worker is not self._shape_tracker:
            return
        # Only the current worker may apply results.
        self._track_completion_timer.stop()
        self._shape_tracker = None
        try:
            outcome = worker.outcome
            self._log_tracking_diagnostic('worker finished', outcome, self._shape_track_index)
            if not outcome:
                self.status.setText('Tracking ended without a result. Check FFmpeg or retry.')
            elif outcome[0] == 'success':
                self._tracking_complete(outcome[1], outcome[2])
            else:
                self._tracking_failed(outcome[1])
        except Exception as exc:
            self.status.setText(f'Tracking result could not be applied: {exc}')
            self._log_tracking_diagnostic('callback exception', str(exc), self._shape_track_index)
        finally:
            self._reset_tracking_button()
            self._shape_track_index = None
            self._shape_track_start = None

    def _log_tracking_diagnostic(self, label, outcome, index):
        try:
            from datetime import datetime
            logfile = ROOT / 'logs' / 'shape_tracking.log'
            logfile.parent.mkdir(parents=True, exist_ok=True)
            with logfile.open('a', encoding='utf-8') as stream:
                stream.write(f'{datetime.now().isoformat(timespec="seconds")} | {label} | effect={index} | {repr(outcome)[:800]}\n')
        except Exception:
            pass

    def _tracking_progress(self, done, total):
        self.status.setText(f'Tracking object: {done}/{total} frames analyzed…')

    def _tracking_complete(self, keys, info):
        index = self._shape_track_index
        if index is None or index >= len(self.project.get('events', [])):
            self.status.setText('Tracking finished; the original effect was removed, so no keys were applied.')
            return
        event = self.project['events'][index]
        # Retain manual keys outside the tracked interval and replace only tracked positions.
        first, last = float(keys[0]['time']), float(keys[-1]['time'])
        original = [k for k in event.get('shape_keyframes', [])
                    if float(k.get('time',0)) < first-.001 or float(k.get('time',0)) > last+.001]
        event['shape_keyframes'] = sorted(original+keys, key=lambda k:float(k['time']))
        self._log_tracking_diagnostic('saved keyframes', f'{len(keys)} keys, interval {first:.3f}-{last:.3f}', index)
        self.preview.update()
        self.effects_timeline.update()
        # Display the first tracked position so playback covers the tracked interval,
        # rather than leaving the playhead at the video end.
        self.set_time(first)
        motion = (((float(keys[-1]['x'])-float(keys[0]['x']))**2 +
                   (float(keys[-1]['y'])-float(keys[0]['y']))**2)**0.5)
        if motion < .15:
            self.status.setText(f'Tracker returned {len(keys)} keys but almost no movement ({motion:.2f}%). Try selecting a more detailed moving object.')
            return
        self.status.setText(f'Tracked {info[0]}/{info[1]} samples up to {last:.2f}s; '
                            f'{len(keys)} editable position keys; total displacement {motion:.2f}% of frame. '
                            'Play the tracked interval; drag to correct any drift.')

    def _tracking_failed(self, error):
        self.status.setText(f'Tracking stopped: {error}')

    def keyframe_planar(self):
        rows=sorted({item.row() for item in self.event_table.selectedItems()})
        if not rows:return
        obj=self.project.get('events',[])[rows[0]]
        if obj.get('style')!='Planar Overlay':
            QMessageBox.information(self,'Select Planar Overlay','Select a Planar Overlay event first.')
            return
        from .planar import quad_at,set_quad_key
        count=set_quad_key(obj,self.current_time(),quad_at(obj,self.current_time()))
        self.statusBar().showMessage(f'Perspective corner keyframe saved ({count} keys).')
        self.preview.update()

    def clear_shape_keyframes(self):
        idx=getattr(self.preview,'selected',None)
        if idx is None or idx >= len(self.project.get('events',[])): return
        self.project['events'][idx].pop('shape_keyframes',None)
        self.preview.update()
        self.status.setText('Shape keyframes cleared; object is stationary again.')

    def duplicate_shape(self):
        import copy
        rows=sorted({cell.row() for cell in self.event_table.selectedItems()})
        if not rows:return
        ev=copy.deepcopy(self.project['events'][rows[0]])
        ev['x']=min(100,float(ev.get('x',50))+3)
        ev['y']=min(100,float(ev.get('y',50))+3)
        if ev.get('style') in ('Free Line','Arrow Line'):
            ev['end_x']=min(100,float(ev.get('end_x',50))+3)
            ev['end_y']=min(100,float(ev.get('end_y',50))+3)
        self.project['events'].append(ev)
        self.refresh_event_table();self.event_table.selectRow(len(self.project['events'])-1)
        self.preview.selected=len(self.project['events'])-1
        self.preview.update()

    def edit_composite_effect(self):
        rows=sorted({item.row() for item in self.event_table.selectedItems()})
        if not rows:
            QMessageBox.information(self,'Select an effect','Select a Callout, Card, Counter or Text Reveal row first.')
            return
        idx=rows[0]
        if idx>=len(self.project.get('events',[])):return
        ev=self.project['events'][idx]
        style=ev.get('style','')
        if style not in ('Linked Callout','Data Card','Analysis Panel / Counter','Text Reveal','Planar Overlay'):
            QMessageBox.information(self,'Select an effect','This effect does not have specialized settings.')
            return
        dlg=QDialog(self);dlg.setWindowTitle('Edit '+style);dlg.resize(460,310)
        outer=QVBoxLayout(dlg);form=QFormLayout();outer.addLayout(form)
        title=QLineEdit(str(ev.get('text','')));form.addRow('Label / heading:',title)
        if style=='Planar Overlay':
            font_size=spin_int(12,240,int(ev.get('planar_font_size',88)))
            image_path=QLineEdit(str(ev.get('planar_image','')))
            choose=button('Browse PNG',lambda: image_path.setText(QFileDialog.getOpenFileName(dlg,'Overlay image','','Images (*.png *.jpg *.jpeg *.webp)')[0] or image_path.text()))
            form.addRow('Font size:',font_size)
            form.addRow('Optional image path:',image_path)
            form.addRow('',choose)
            hint=QLabel('Drag the four cyan corners in the preview; use Set corner keyframe at different times. Text is used when no image is selected.')
        elif style=='Linked Callout':
            frame=fixed_combo(['Corners','Brackets','Box'],str(ev.get('callout_frame','Corners')))
            offset_x=spin_double(-70,70,float(ev.get('callout_offset_x',17)),1,1)
            offset_y=spin_double(-70,70,float(ev.get('callout_offset_y',-12)),1,1)
            fontsize=spin_int(8,150,int(ev.get('callout_font_size',22)))
            form.addRow('Target frame:',frame)
            form.addRow('Label offset X%:',offset_x)
            form.addRow('Label offset Y%:',offset_y)
            form.addRow('Font size:',fontsize)
            hint=QLabel('Move or resize the target on the preview. Existing tracking keyframes animate frame, connector and label together.')
        elif style=='Analysis Panel / Counter':
            start_value=spin_double(-1000000,1000000,float(ev.get('counter_from',0)),2,1)
            end_value=spin_double(-1000000,1000000,float(ev.get('counter_to',100)),2,1)
            duration=spin_double(.05,120,float(ev.get('counter_duration',1.5)),2,.1)
            decimals=spin_int(0,3,int(ev.get('counter_decimals',0)))
            suffix=QLineEdit(str(ev.get('counter_suffix','%')))
            progress=spin_int(0,100,int(ev.get('counter_progress',100)))
            width=spin_int(6,85,int(ev.get('panel_width',24)))
            fontsize=spin_int(8,120,int(ev.get('panel_font_size',22)))
            for label,widget in [('Starts at:',start_value),('Ends at:',end_value),('Animation duration (s):',duration),('Decimals:',decimals),('Suffix:',suffix),('Bar target %:',progress),('Panel width %:',width),('Font size:',fontsize)]:form.addRow(label,widget)
            hint=QLabel('The number and bar animate only during this event. Move, recolor, or change its Start/End in the normal effect timeline.')
        elif style=='Text Reveal':
            mode=fixed_combo(['Words','Characters','Wipe Left','Wipe Right','Wipe Up','Wipe Down'],str(ev.get('reveal_mode','Words')))
            duration=spin_double(.05,120,float(ev.get('reveal_duration',1)),2,.1)
            fontsize=spin_int(8,220,int(ev.get('reveal_font_size',54)))
            form.addRow('Reveal style:',mode)
            form.addRow('Reveal duration (s):',duration)
            form.addRow('Text size:',fontsize)
            hint=QLabel('One standalone text event, progressively revealed. Does not modify synchronized lyric words.')
        else:
            entries=QTextEdit();entries.setPlainText(str(ev.get('card_rows','')));entries.setMinimumHeight(120)
            width=spin_int(5,90,int(ev.get('card_width',24)))
            fontsize=spin_int(8,100,int(ev.get('card_font_size',21)))
            barcode=QCheckBox('Decorative barcode');barcode.setChecked(bool(ev.get('card_barcode',False)))
            form.addRow('Rows (one KEY | VALUE per line):',entries)
            form.addRow('Card width %:',width)
            form.addRow('Font size:',fontsize)
            form.addRow('',barcode)
            hint=QLabel('The barcode is decorative, not scannable. Position and color use the usual effect table.')
        hint.setWordWrap(True);outer.addWidget(hint)
        controls=QHBoxLayout();outer.addLayout(controls);controls.addStretch(1)
        cancel=button('Cancel',dlg.reject);save=button('Save',dlg.accept)
        controls.addWidget(cancel);controls.addWidget(save)
        if dlg.exec()!=QDialog.DialogCode.Accepted:return
        ev['text']=title.text()
        if style=='Planar Overlay':
            ev.update(planar_font_size=font_size.value(),planar_image=image_path.text())
        elif style=='Linked Callout':
            ev.update(callout_frame=frame.currentText(),callout_offset_x=offset_x.value(),
                      callout_offset_y=offset_y.value(),callout_font_size=fontsize.value())
        elif style=='Analysis Panel / Counter':
            ev.update(counter_from=start_value.value(),counter_to=end_value.value(),counter_duration=duration.value(),counter_decimals=decimals.value(),counter_suffix=suffix.text(),counter_progress=progress.value(),panel_width=width.value(),panel_font_size=fontsize.value())
        elif style=='Text Reveal':
            ev.update(reveal_mode=mode.currentText(),reveal_duration=duration.value(),reveal_font_size=fontsize.value())
        else:
            ev.update(card_rows=entries.toPlainText(),card_width=width.value(),
                      card_font_size=fontsize.value(),card_barcode=barcode.isChecked())
        self.refresh_event_table();self.event_table.selectRow(idx)
        self.preview.update();self.effects_timeline.update()

    def apply_frame_geometry(self):
        rows=sorted({item.row() for item in self.event_table.selectedItems()})
        for row in rows:
            if row < len(self.project.get('events',[])):
                ev=self.project['events'][row]
                ev['frame_width']=self.frame_width.value()
                ev['frame_height']=self.frame_height.value()
                ev['line_thickness']=self.frame_thickness.value()
        self.preview.update()
        self.effects_timeline.update()

    def apply_line_geometry(self):
        rows=sorted({item.row() for item in self.event_table.selectedItems()})
        for row in rows:
            if row >= len(self.project.get('events',[])):continue
            ev=self.project['events'][row]
            if ev.get('style') not in ('Free Line','Arrow Line'):continue
            ev.update(x=self.line_start_x.value(), y=self.line_start_y.value(),
                      end_x=self.line_end_x.value(),end_y=self.line_end_y.value(),
                      line_thickness=self.line_thickness.value())
        self.refresh_event_table();self.preview.update()

    def _update_properties_visibility(self, event=None):
        style=(event or {}).get('style','')
        is_line=style in ('Free Line','Arrow Line')
        is_frame=style in ('Object Box','Object Corners','Object Brackets','Linked Callout',
                          'Cinematic Frame','HUD Scanner Frame','Editorial Film Frame',
                          'Industrial Warning Frame','Horizontal Line','Vertical Line')
        is_text=style in ('Text Overlay','Pop Open','Side Slam','Stamp','Slide in',
                          'Beat punch','Wobble','Glitch','Neon tag','Echo Typography',
                          'Word Storm','Corner Label','Scrolling Ticker','Text Reveal')
        self.composite_edit_button.setVisible(style in ('Linked Callout','Data Card','Analysis Panel / Counter','Text Reveal','Planar Overlay'))
        self.typography_panel.setVisible(is_text)
        self.frame_panel.setVisible(is_frame)
        self.line_panel.setVisible(is_line)
        self.motion_panel.setVisible(bool(event) and not (is_frame or is_line or style=='Data Card'))
        self.audio_panel.setVisible(bool(event) and not (is_frame or is_line or style=='Data Card'))

    def add_selected_effect(self):
        """Create either a typography hit or procedural FX from the same picker."""
        style = self.effect_style.currentText()
        start = round(self.current_time(), 3)
        if self.project['options'].get('snap_events', True):
            start = round(snap_beat(start, self.project['bpm'], self.project['t0']), 3)
        is_motion = style in MOTION_STUDIO_STYLES
        default_text = {
            'Text Overlay': 'YOUR TEXT', 'Pop Open': 'POP!', 'Side Slam': 'SLAM!', 'Slide in': 'GO!',
            'Beat punch': 'HIT!', 'Neon tag': 'ALERT', 'Comic Burst': 'BOOM!',
            'Echo Typography': 'ECHO', 'Word Storm': 'BREAK FREE',
            'Radar Sweep': 'TARGET', 'Animated Arrows': 'GO!',
            'Speedometer HUD': 'SPEED', 'Warning Meter': 'WARNING',
            'Scanning HUD': 'SCANNING', 'Arrow Impact': 'IMPACT!',
            'Heartbeat': 'HEARTBEAT', 'Rotary Mechanism': 'ROTARY',
            'Equalizer 3-Band': '', 'Equalizer 12-Band': '', 'Beat Heart': '',
            'Object Box': '', 'Object Corners': '', 'Object Brackets': '',
            'Linked Callout': 'TARGET IDENTIFIED', 'Data Card': 'DATA RECORD',
            'Analysis Panel / Counter': 'SYSTEM ANALYSIS', 'Text Reveal': 'FUTURE LOADING',
            'Planar Overlay':'ACCESS GRANTED',
            'Free Line': '', 'Arrow Line': '',
            'Corner Label': 'FOREST - EMPTY 0.87', 'Scrolling Ticker': 'SYSTEM READY  •  AUDIO ACTIVE',
        }.get(style, '' if is_motion else 'HIT!')
        length = 2.0 if is_motion else 2 * 60 / max(1, float(self.project['bpm']))
        ev = {'start': start, 'end': round(start + length, 3), 'style': style,
              'text': default_text, 'x': 50, 'y': 50 if is_motion else 45,
              'size': 150 if is_motion else 95,
              'color': '#F6DD36' if is_motion else '#E9FC4A',
              'opacity': 100, 'rotation': 0, 'seed': 1234,
              'font_family': self.effect_font.currentText() if hasattr(self,'effect_font') else 'Anton',
              'frame_width':90, 'frame_height':88, 'line_thickness':2.5}
        if style == 'Planar Overlay':
            from .planar import DEFAULT_QUAD
            ev.update(quad=[list(v) for v in DEFAULT_QUAD],planar_font_size=88,color='#DCE5F0',fade_in=0,fade_out=0,end=min(start+5,float(self.project.get('duration') or start+5)))
        if style == 'Analysis Panel / Counter':
            ev.update(x=76,y=24,size=160,color='#9FB6CC',counter_from=0,counter_to=100,counter_duration=1.5,counter_suffix='%',counter_decimals=0,counter_progress=100,panel_width=24,panel_font_size=22,fade_in=0,fade_out=0,end=min(start+4,float(self.project.get('duration') or start+4)))
        elif style == 'Text Reveal':
            ev.update(x=50,y=70,color='#DAE5EE',reveal_mode='Words',reveal_duration=1.2,reveal_font_size=54,fade_in=0,fade_out=0,end=min(start+3,float(self.project.get('duration') or start+3)))
        elif style == 'Linked Callout':
            ev.update(frame_width=18,frame_height=26,callout_offset_x=17,callout_offset_y=-12,callout_font_size=22,callout_frame='Corners',color='#9FB6CC',end=min(start+5,float(self.project.get('duration') or start+5)))
        elif style == 'Data Card':
            ev.update(x=72,y=27,card_width=24,card_font_size=21,card_rows='TYPE | SYNTHETIC\nSTATUS | ACTIVE\nID | 001',card_barcode=True,color='#A1B7CE',end=min(start+5,float(self.project.get('duration') or start+5)))
        if style in ('Equalizer 3-Band','Equalizer 12-Band','Beat Heart'):
            ev['color'] = '#00E5FF' if 'Equalizer' in style else '#FF4C84'
            ev['size'] = 110
            ev['end'] = round(min(float(self.project.get('duration') or (start+8)), start+8),3)
            ev['fade_in'] = 0.0
            ev['fade_out'] = 0.0
            if ev['end'] <= start: ev['end'] = round(start+2.,3)
        self.project.setdefault('events', []).append(ev)
        if style in ('Equalizer 3-Band','Equalizer 12-Band','Beat Heart') and not self._audio_bands_current():
            self.analyze_bands()
        self.refresh_event_table()
        self.preview.update()
        self.effects_timeline.update()

    # Compatibility for callers using the previous action names.
    def add_event(self):
        self.add_selected_effect()

    def add_motion_effect(self):
        self.add_selected_effect()

    def add_decorative_text(self):
        self.effect_style.setCurrentText('Text Overlay')
        self.add_selected_effect()

    def apply_effect_typography(self):
        rows = sorted({cell.row() for cell in self.event_table.selectedItems()})
        if not rows:
            self.status.setText('Select an effect row before applying typography.')
            return
        for row in rows:
            if row < len(self.project.get('events',[])):
                ev = self.project['events'][row]
                ev['font_family'] = self.effect_font.currentText()
                ev['size'] = self.effect_font_size.value()
                ev['text_width'] = self.text_width.value()
                ev['letter_spacing'] = self.text_tracking.value()
                ev['outline_only'] = self.text_outline.isChecked()
        self.refresh_event_table()
        self.preview.update()
        self.status.setText('Font and size applied to selected effects; timing unchanged.')

    def on_event_selection_changed(self):
        self.effects_timeline.update()
        rows = sorted({item.row() for item in self.event_table.selectedItems()})
        if not rows or rows[0] >= len(self.project.get('events', [])):
            self.preview.selected=None
            self.preview.update()
            self._update_properties_visibility()
            return
        ev = self.project['events'][rows[0]]
        if ev.get('style') in self.preview.SHAPES:
            self.preview.selected = rows[0]
            start, end = float(ev.get('start',0)), float(ev.get('end',0))
            if end > start and not start <= self.current_time() < end:
                # Selecting a timed object seeks back to a visible/editable moment.
                self.set_time(start + min(0.10, (end-start)*0.5))
        self._update_properties_visibility(ev)
        for control,key in ((self.line_start_x,'x'),(self.line_start_y,'y'),
                            (self.line_end_x,'end_x'),(self.line_end_y,'end_y'),
                            (self.line_thickness,'line_thickness')):
            control.blockSignals(True)
            control.setValue(float(ev.get(key,2.5 if key=='line_thickness' else 50)))
            control.blockSignals(False)
        for ctl,key,default in ((self.frame_width,'frame_width',90),(self.frame_height,'frame_height',88),(self.frame_thickness,'line_thickness',2.5)):
            ctl.blockSignals(True);ctl.setValue(float(ev.get(key,default)));ctl.blockSignals(False)
        for combo, key, default in ((self.entry_motion, 'entry_motion', 'Default'),
                                    (self.exit_motion, 'exit_motion', 'Default Fade')):
            combo.blockSignals(True)
            combo.setCurrentText(ev.get(key, default))
            combo.blockSignals(False)
        self.effect_font.blockSignals(True)
        name = str(ev.get('font_family') or self.project.get('options',{}).get('lyric_font','Anton'))
        if self.effect_font.findText(name) == -1:
            self.effect_font.addItem(name)
        self.effect_font.setCurrentText(name)
        self.effect_font.blockSignals(False)
        self.effect_font_size.blockSignals(True)
        self.effect_font_size.setValue(max(8,min(700,round(float(ev.get('size',110))))))
        self.effect_font_size.blockSignals(False)
        self.text_width.setValue(int(ev.get('text_width',100)))
        self.text_tracking.setValue(float(ev.get('letter_spacing',0)))
        self.text_outline.setChecked(bool(ev.get('outline_only',False)))
        self.audio_react.blockSignals(True)
        self.audio_react.setCurrentText(ev.get('audio_react','Off'))
        self.audio_react.blockSignals(False)
        self.audio_strength.blockSignals(True)
        self.audio_strength.setValue(int(ev.get('audio_strength',65)))
        self.audio_strength.blockSignals(False)
        self.audio_smooth.blockSignals(True)
        self.audio_smooth.setValue(round(float(ev.get('audio_smooth',.12))*1000))
        self.audio_smooth.blockSignals(False)
        for ctrl,key,default in ((self.entry_duration,'entry_duration',.4),
                                 (self.exit_duration,'exit_duration',.28)):
            ctrl.blockSignals(True)
            ctrl.setValue(round(float(ev.get(key, default))*1000))
            ctrl.blockSignals(False)

    def apply_event_motion(self):
        rows = sorted({item.row() for item in self.event_table.selectedItems()})
        if not rows:
            self.status.setText('Select an effect row first, then click Apply motion.')
            return
        for row in rows:
            if row >= len(self.project.get('events', [])):
                continue
            event = self.project['events'][row]
            if event.get('style') in ('Countdown', 'Image / GIF'):
                continue
            event['entry_motion'] = self.entry_motion.currentText()
            event['exit_motion'] = self.exit_motion.currentText()
            event['entry_duration'] = self.entry_duration.value() / 1000.0
            event['exit_duration'] = self.exit_duration.value() / 1000.0
        self.preview.update()
        self.status.setText('Entry/exit motion applied. Original start/end timestamps preserved.')

    def _band_audio_signature(self):
        """Identify the analyzed source, including in-place audio replacement."""
        source = self.project.get('audio') or self.project.get('video')
        if not source:
            return None
        try:
            stat = Path(source).resolve().stat()
            return {'path': str(Path(source).resolve()),
                    'size': stat.st_size, 'mtime_ns': stat.st_mtime_ns}
        except OSError:
            return None

    def _audio_bands_current(self):
        envelope = self.project.get('audio_envelopes')
        return (isinstance(envelope, dict) and bool(envelope.get('values'))
                and self._band_audio_signature() is not None
                and self.project.get('audio_envelopes_source') == self._band_audio_signature())

    def analyze_bands(self):
        audio=self.project.get('audio') or self.project.get('video')
        if not audio or not self.ffmpeg:
            self.message('Load music or video and configure FFmpeg first.'); return
        if getattr(self,'_bands_worker',None) and self._bands_worker.isRunning():
            return
        self.status.setText('Analyzing bass, midrange, treble and energy offline...')
        self._bands_worker=MultibandWorker(self.ffmpeg,audio)
        self._bands_worker.ready.connect(self.bands_ready)
        self._bands_worker.failed.connect(lambda error:self.message('Audio analysis failed: '+error))
        self._bands_worker.start()

    def bands_ready(self, result):
        self.project['audio_envelopes']=result
        self.project['audio_envelopes_source']=self._band_audio_signature()
        from .audio_reactivity import energy_at
        values = result.get('values', [])
        import numpy as np
        peaks = np.percentile(np.asarray(values, dtype=float), 90, axis=0) if values else [0]*4
        self.status.setText('Audio ready: %s frames; 90th percentile Bass %.2f / Mid %.2f / Treble %.2f / Overall %.2f' %
                            (len(values), *peaks))
        self.preview.update()

    def apply_audio_reaction(self):
        rows=sorted({item.row() for item in self.event_table.selectedItems()})
        if not rows:
            self.status.setText('Select one or more effects first.');return
        for row in rows:
            if row>=len(self.project.get('events',[])):continue
            ev=self.project['events'][row]
            ev['audio_react']=self.audio_react.currentText()
            ev['audio_strength']=self.audio_strength.value()
            ev['audio_smooth']=self.audio_smooth.value()/1000.0
        self.preview.update()
        if self.audio_react.currentText() != 'Off' and not self._audio_bands_current():
            # Changes are already saved; the existing worker signals refresh the preview.
            if getattr(self, '_bands_worker', None) and self._bands_worker.isRunning():
                self.status.setText('Reaction saved. Audio analysis already running...')
            else:
                self.analyze_bands()
        else:
            self.status.setText('Audio reaction saved to selected effect(s). Start/End times preserved.')

    def add_countdown(self):
        start = round(self.current_time(), 3)
        target, ok = QInputDialog.getDouble(self, 'Countdown target',
            'Countdown finishes at timestamp (seconds):', start + 5.0, start + .001,
            999999, 3)
        if not ok:
            return
        self.project['events'].append({'start':start, 'end':round(target,3),
            'style':'Countdown', 'text':'TO IMPACT', 'x':50, 'y':46,
            'size':130, 'color':'#F7E43E'})
        self.refresh_event_table(); self.preview.update()

    def add_image_overlay(self):
        filepath, _ = QFileDialog.getOpenFileName(self, 'Choose transparent image or animated GIF', '',
            'Overlay images (*.png *.gif *.webp *.jpg *.jpeg *.bmp);;All files (*)')
        if not filepath:
            return
        start = round(self.current_time(),3)
        self.project['events'].append({'start':start,'end':round(start+3,3),
            'style':'Image / GIF','text':filepath,'x':50,'y':50,
            'size':300,'color':'#FFFFFF','opacity':100,'rotation':0,'speed':1})
        self.refresh_event_table();self.preview.update()

    def delete_event(self):
        rows=sorted({v.row() for v in self.event_table.selectedItems()},reverse=True)
        for i in rows:self.project['events'].pop(i)
        self.refresh_event_table();self.preview.update()

    def refresh_event_table(self):
        table=self.event_table
        table.blockSignals(True);table.setRowCount(len(self.project.get('events',[])))
        for i,ev in enumerate(self.project['events']):
            for j,key in enumerate(['start','end','style','text','x','y','size','color']):
                is_shape = ev.get('style') in ('Object Box','Object Corners','Object Brackets','Free Line','Arrow Line')
                item = QTableWidgetItem('—' if key=='size' and is_shape else str(ev.get(key,'')))
                if key=='size' and is_shape:
                    item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                    item.setToolTip('Shapes use Width/Height or line endpoints, not Size')
                if key == 'color':
                    color = QColor(str(ev.get(key, '#FFFFFF')))
                    if color.isValid():
                        item.setBackground(color)
                        item.setForeground(QColor('#111111' if color.lightness() > 135 else '#FFFFFF'))
                        item.setToolTip('Double-click to open the visual color picker')
                        item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                table.setItem(i,j,item)
        table.blockSignals(False)
        if hasattr(self,'effects_timeline'):self.effects_timeline.update()
        if hasattr(self,'beat_timed_table'):self.refresh_timed_beat_table()

    def event_color_double_click(self, row, column):
        if column != 7 or row >= len(self.project.get('events', [])):
            return
        event = self.project['events'][row]
        chosen = QColorDialog.getColor(QColor(valid_hex_color(event.get('color', '#FFFFFF'))),
                                       self, 'Choose effect color',
                                       QColorDialog.ColorDialogOption.DontUseNativeDialog)
        if chosen.isValid():
            event['color'] = chosen.name().upper()
            self.refresh_event_table()
            self.event_table.selectRow(row)
            self.preview.update()

    def update_event(self,item):
        i,j=item.row(),item.column()
        if i>=len(self.project['events']):return
        ev=self.project['events'][i]
        key=['start','end','style','text','x','y','size','color'][j]
        try:
            value=float(item.text()) if key in ('start','end','x','y','size') else item.text().strip()
            if key=='end' and value<=ev['start']:raise ValueError()
            if key=='start' and value>=ev['end']:raise ValueError()
            if key=='style' and value not in ('Text Overlay','Stamp','Slide in','Side Slam','Pop Open','Beat punch','Wobble','Glitch','Neon tag','Countdown','Image / GIF',*MOTION_STUDIO_STYLES):
                raise ValueError()
            ev[key]=value
        except ValueError:
            self.refresh_event_table();return
        self.preview.update()
        if hasattr(self,'effects_timeline'):self.effects_timeline.update()

    def export(self):
        if self._render and self._render.isRunning():
            return
        if not self.ffmpeg or not Path(self.ffmpeg).is_file():
            self.message('Choose the FFmpeg executable before rendering.');return
        if not self.project.get('video'):
            self.message('Choose a source video before rendering.');return
        default=str(ROOT/'output'/ (Path(self.project['video']).stem+'_beatsync.mp4'))
        path,_=QFileDialog.getSaveFileName(self,'Render effect overlay into MP4',default,
                                           'MP4 videos (*.mp4)')
        if not path:return
        if not path.lower().endswith('.mp4'):path+='.mp4'
        if Path(path).resolve()==Path(self.project['video']).resolve():
            self.message('Choose a DIFFERENT output path. Source video must not be overwritten.');return
        self._render=RenderWorker(copy.deepcopy(self.project),self.ffmpeg,path)
        self._render.progress.connect(self.render_progress)
        self._render.failed.connect(self.render_failed)
        self._render.done.connect(self.render_done)
        self._render.cancelled.connect(self.render_cancelled)
        self.export_button.setEnabled(False);self.cancel_button.setEnabled(True)
        self.progress.setValue(0);self.status.setText('Rendering entirely offline...')
        self._render.start()

    def render_progress(self,i,total):
        self.progress.setValue(int(100*i/total))
        self.status.setText(f'Rendering overlay frames: {i} / {total}')

    def cancel_export(self):
        if self._render:self._render.cancel();self.status.setText('Stopping render...')

    def _render_ui_reset(self):
        self.export_button.setEnabled(True);self.cancel_button.setEnabled(False)

    def render_failed(self,error):
        self._render_ui_reset();self.message(error)

    def render_done(self,path):
        self._render_ui_reset();self.progress.setValue(100)
        self.status.setText('Complete: '+path)
        QMessageBox.information(self,'Rendered offline','Video saved:\n'+path)

    def render_cancelled(self):
        self._render_ui_reset();self.status.setText('Render cancelled; incomplete output discarded.')

    def message(self, txt):
        self.status.setText(txt.splitlines()[0])
        QMessageBox.warning(self,'BeatSync FX',txt)

    def closeEvent(self,event):
        if self._worker and self._worker.isRunning():
            self.message('Audio analysis / transcription is still running. Close once it finishes.')
            event.ignore();return
        if self._render and self._render.isRunning():
            self.message('Cancel the render and let FFmpeg shut down before closing the editor.')
            event.ignore();return
        # Capture any text-field edits even if the user closes without pressing Enter.
        self.save_whisper_folder()
        if self.ffmpeg:
            self.settings['ffmpeg'] = self.ffmpeg
        save_settings(self.settings)
        self._save_recovery()
        event.accept()


def main():
    app=QApplication(sys.argv)
    app.setStyle('Fusion')
    app.setStyleSheet('''
    QWidget { background: #111821; color: #e6ecf4; font-family: Arial; font-size: 12px; }
    QMainWindow, QSplitter, QScrollArea { background: #0a111b; }
    QPushButton { background: #263847; border: 1px solid #395366; border-radius: 5px;
                  padding: 8px 10px; }
    QPushButton:hover { background: #305269; border-color: #54c4d5; }
    QPushButton:disabled { color: #758898; background: #1a2530; }
    QPushButton#primary { background: #0e697a; border: 1px solid #24d5db; font-weight: bold; }
    QTabWidget::pane { border: 1px solid #263847; }
    QTabBar::tab { background: #1a2835; padding: 9px; margin-right: 2px; }
    QTabBar::tab:selected { background: #147a86; }
    QLineEdit,QTextEdit,QComboBox,QSpinBox,QDoubleSpinBox,QTableWidget { background:#0b131c;
                  color: #ecf1f5; border: 1px solid #314554; padding: 4px; }
    QHeaderView::section { background:#263847; color:#e4eef5; padding:6px; }
    QGroupBox { border: 1px solid #374956; border-radius: 5px; margin-top: 12px; padding: 12px 8px 6px; }
    QGroupBox::title { subcontrol-origin: margin; left: 10px; color: #8be5e7; }
    QProgressBar { border: 1px solid #314554; border-radius:5px; background:#192733; text-align:center; }
    QProgressBar::chunk { background:#1ea4af; }
    QSlider::groove:horizontal { height:5px; background:#304854; border-radius:2px; }
    QSlider::handle:horizontal { background:#48e5dd; width:14px; margin:-5px 0; border-radius:7px; }
    QScrollBar:vertical { background:#0e1a25; width:12px; }
    QScrollBar::handle:vertical { background:#3b5863; min-height:25px; border-radius:4px; }
    ''')
    w=MainWindow();w.show()
    return app.exec()
