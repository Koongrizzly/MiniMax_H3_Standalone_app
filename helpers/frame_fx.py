"""Deterministic vector frames/lines/tickers for the BeatSync FX effects renderer."""
from __future__ import annotations
import math
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QPainterPath, QPen, QFont

FRAME_STYLES = (
    'Cinematic Frame', 'HUD Scanner Frame', 'Editorial Film Frame',
    'Industrial Warning Frame', 'Horizontal Line', 'Vertical Line',
    'Corner Label', 'Scrolling Ticker',
    'Object Box', 'Object Corners', 'Object Brackets', 'Free Line', 'Arrow Line',
)

def _clamp(x):
    return max(0., min(1., float(x)))

def draw_frame(p, w, h, t, event, kind):
    """Draws inside painter's current X/Y anchor transform; all times absolute."""
    start=float(event.get('start',0)); end=float(event.get('end',start))
    if not start <= t < end: return
    dur=max(.001,end-start)
    elapsed=t-start
    # Drawing animation lasts at most 450ms; never continues beyond the event.
    draw=_clamp(elapsed/min(.45,dur))
    col=QColor(str(event.get('color','#FFFFFF')))
    if not col.isValid(): col=QColor('#FFFFFF')
    s=min(w/1280,h/720)
    thickness=max(1., float(event.get('line_thickness',2.5))*s)
    ww=w*_clamp(float(event.get('frame_width',90))/100)
    hh=h*_clamp(float(event.get('frame_height',88))/100)
    if kind not in ('Free Line','Arrow Line') and (ww<2 or hh<2): return
    a=ww/2; b=hh/2
    p.save()
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.setPen(QPen(col,thickness,Qt.PenStyle.SolidLine,Qt.PenCapStyle.SquareCap))
    def line(x1,y1,x2,y2,f=1.):
        fraction=_clamp(draw*f)
        p.drawLine(QPointF(x1,y1),QPointF(x1+(x2-x1)*fraction,y1+(y2-y1)*fraction))
    if kind in ('Object Box', 'Object Corners', 'Object Brackets'):
        # Unlike screen borders, these are true local objects, positioned at X/Y.
        if kind == 'Object Box':
            line(-a,-b,a,-b); line(a,-b,a,b); line(a,b,-a,b); line(-a,b,-a,-b)
        else:
            reach = .23 if kind == 'Object Corners' else .32
            for sx in (-1,1):
                for sy in (-1,1):
                    xx=sx*a; yy=sy*b
                    line(xx, yy, xx-sx*ww*reach, yy)
                    line(xx, yy, xx, yy-sy*hh*reach)
        p.restore()
        return
    if kind in ('Free Line','Arrow Line'):
        # End coordinates are stored independently, allowing arbitrary diagonal lines.
        x2=w*float(event.get('end_x', event.get('x',50)))/100
        y2=h*float(event.get('end_y', event.get('y',50)))/100
        x1=w*float(event.get('x',50))/100
        y1=h*float(event.get('y',50))/100
        dx=x2-x1; dy=y2-y1
        line(0,0,dx,dy)
        if kind=='Arrow Line':
            angle=math.atan2(dy,dx); sz=max(7.,thickness*5)
            # Draw arrowhead at animated tip.
            tx=dx*draw; ty=dy*draw
            if draw>0:
                p.drawLine(QPointF(tx,ty),QPointF(tx-sz*math.cos(angle-.48),ty-sz*math.sin(angle-.48)))
                p.drawLine(QPointF(tx,ty),QPointF(tx-sz*math.cos(angle+.48),ty-sz*math.sin(angle+.48)))
        p.restore()
        return
    if kind in ('Horizontal Line','Vertical Line'):
        if kind=='Horizontal Line':line(-a,0,a,0)
        else:line(0,-b,0,b)
    elif kind=='Scrolling Ticker':
        # Clip ticker to the selected width; local elapsed controls phase regardless of preview order.
        p.setClipRect(QRectF(-a,-max(15,hh*.07),ww,max(30,hh*.14)))
        message=str(event.get('text','SYSTEM READY    •    STATUS ACTIVE'))
        font=QFont(str(event.get('font_family','Arial')))
        font.setPixelSize(max(10,int(float(event.get('font_size',24))*s)))
        p.setFont(font); fm=p.fontMetrics()
        full='   ✦   '.join([message]*4)+'   ✦   '
        span=max(1,fm.horizontalAdvance(full))
        x=a-(elapsed*max(20,120*s))%span
        p.drawText(QPointF(x, fm.ascent()/2),full)
        p.drawText(QPointF(x+span, fm.ascent()/2),full)
    elif kind=='Corner Label':
        line(-a,-b,-a+ww*.16,-b);line(-a,-b,-a,-b+hh*.12)
        font=QFont(str(event.get('font_family','Arial')))
        font.setPixelSize(max(10,int(float(event.get('font_size',24))*s)))
        p.setFont(font)
        p.drawText(QPointF(-a+10*s,-b-9*s),str(event.get('text','LOCATION 01')))
    else:
        corner=.16
        if kind=='Cinematic Frame':
            for xsign in (-1,1):
                for ysign in (-1,1):
                    x=xsign*a;y=ysign*b
                    line(x,y,x-xsign*ww*corner,y)
                    line(x,y,x,y-ysign*hh*corner)
            line(-a+ww*.30,-b,a-ww*.30,-b)
            line(-a+ww*.30,b,a-ww*.30,b)
        elif kind=='HUD Scanner Frame':
            for xsign in (-1,1):
                for ysign in (-1,1):
                    x=xsign*a;y=ysign*b
                    line(x,y,x-xsign*ww*.16,y)
                    line(x,y,x,y-ysign*hh*.17)
            p.setPen(QPen(col,thickness*.65))
            sweep_y=-b+hh*((elapsed*.35)%1)
            line(-a,sweep_y,a,sweep_y,1)
            line(-a*.10,0,a*.10,0);line(0,-b*.13,0,b*.13)
        elif kind=='Editorial Film Frame':
            # Full perimeter revealed progressively as separate corner strokes.
            line(-a,-b,a,-b);line(a,-b,a,b);line(a,b,-a,b);line(-a,b,-a,-b)
            p.setPen(QPen(col,thickness*.45,Qt.PenStyle.DashLine))
            p.drawRect(QRectF(-a+9*s,-b+9*s,ww-18*s,hh-18*s))
            line(-a-12*s,-b,-a+ww*.12,-b)
            line(-a,-b-12*s,-a,-b+hh*.12)
        elif kind=='Industrial Warning Frame':
            path=QPainterPath()
            cut=min(ww,hh)*.08
            pts=[(-a+cut,-b),(a-cut,-b),(a,-b+cut),(a,b-cut),(a-cut,b),(-a+cut,b),(-a,b-cut),(-a,-b+cut)]
            for i,(x,y) in enumerate(pts):
                if i==0:path.moveTo(x,y)
                else:path.lineTo(x,y)
            path.closeSubpath()
            p.drawPath(path)
            for i in range(9):
                x=-a+i*ww/8
                line(x,-b,x+ww*.018,-b+hh*.035)
                line(x,b,x+ww*.018,b-hh*.035)
    p.restore()
