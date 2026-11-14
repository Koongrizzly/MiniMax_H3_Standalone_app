"""Deterministic beat-triggered VIDEO effects, shared by preview and export.

Unlike the existing transparent overlays these transforms require source pixels.
Frames are BGRA QImages; no model/GPU or system installation is required.
"""
from __future__ import annotations
import math
import numpy as np
from PySide6.QtGui import QImage
from .audio_reactivity import screen_beat_pulse

VIDEO_EFFECTS = ('Horizontal Tearing', 'RGB Split', 'Frame Stutter',
                 'Signal Dropout', 'Block Corruption', 'Impact Zoom')

def hits(project, t):
    active = []
    for i,event in enumerate(project.get('events', [])):
        kind=str(event.get('style','')).removeprefix('Beat: ')
        if kind not in VIDEO_EFFECTS or not float(event.get('start',0))<=t<float(event.get('end',0)):
            continue
        power,beat,beat_time=screen_beat_pulse(project,t,event)
        if power<=0 or beat_time<float(event.get('start',0))-.001:continue
        level=max(0.,min(1.,float(event.get('beat_intensity',60))/100))
        active.append((kind,level*power,beat,i,event,beat_time))
    return active

def needs_video(project):
    return any(str(e.get('style','')).removeprefix('Beat: ') in VIDEO_EFFECTS for e in project.get('events',[]))

def stutter_target(project,t,fps):
    """Source-frame timestamp to hold on the hit; other audio/video times keep moving."""
    for kind,power,beat,index,event,beat_time in reversed(hits(project,t)):
        if kind == 'Frame Stutter' and power > 0:
            return max(0,math.ceil(beat_time*fps-1e-7))/fps
    return None

def _shift(arr, delta, axis):
    # Clip rather than wrap: displaced image strips end in black, not opposite-edge pixels.
    out = np.zeros_like(arr)
    if not delta:return arr.copy()
    n = arr.shape[axis]
    delta = max(-n+1,min(n-1,int(delta)))
    if axis==1:
        if delta>0:out[:,delta:,:]=arr[:,:-delta,:]
        else:out[:,:delta,:]=arr[:,-delta:,:]
    return out

def apply_video_effects(image:QImage, project:dict, t:float) -> QImage:
    active = [h for h in hits(project,t) if h[0]!='Frame Stutter']
    if not active:return image
    im=image.convertToFormat(QImage.Format.Format_ARGB32)
    h,w=im.height(),im.width()
    a=np.frombuffer(im.bits(),dtype=np.uint8).reshape(h,im.bytesPerLine())[:,:w*4].reshape(h,w,4).copy()
    for kind,power,beat,index,event,beat_time in active:
        if power <= .001:continue
        rng=np.random.default_rng((beat*104729+index*7919+17)&0xffffffff)
        amount=max(1,int(w*.045*power))
        if kind=='Horizontal Tearing':
            pieces=3+int(9*power)
            for _ in range(pieces):
                y=int(rng.integers(0,h)); thick=int(rng.integers(max(2,h//90),max(3,h//14)))
                delta=int(rng.integers(-amount*2,amount*2+1))
                a[y:min(h,y+thick)] = _shift(a[y:min(h,y+thick)],delta,1)
        elif kind=='RGB Split':
            offs=max(1,int(w*.018*power))
            original=a.copy()
            a[:,:,2]=_shift(original,offs,1)[:,:,2]
            a[:,:,0]=_shift(original,-offs,1)[:,:,0]
        elif kind=='Signal Dropout':
            pieces=2+int(5*power)
            for _ in range(pieces):
                y=int(rng.integers(0,h)); thick=int(rng.integers(max(2,h//65),max(3,h//11)))
                y2=min(h,y+thick)
                if rng.integers(0,3)==0:
                    noise=rng.integers(0,95,size=(y2-y,w,1),dtype=np.uint8)
                    a[y:y2,:,:3]=noise
                else:a[y:y2,:,:3]=0
        elif kind=='Block Corruption':
            pieces=3+int(14*power)
            original=a.copy()
            for _ in range(pieces):
                bh=int(rng.integers(max(4,h//45),max(5,h//8)))
                bw=int(rng.integers(max(8,w//30),max(9,w//7)))
                y=int(rng.integers(0,max(1,h-bh)));x=int(rng.integers(0,max(1,w-bw)))
                srcx=max(0,min(w-bw,x+int(rng.integers(-w//5,w//5+1))))
                block=original[y:y+bh,srcx:srcx+bw]
                if rng.integers(0,2):
                    factor=max(2,int(3+power*12))
                    small=block[::factor,::factor]
                    block=np.repeat(np.repeat(small,factor,axis=0),factor,axis=1)[:bh,:bw]
                a[y:y+bh,x:x+bw]=block
        elif kind=='Impact Zoom':
            factor=1.0+(0.60 if event.get('beat_trigger') else 0.085)*power
            sh=max(2,int(h/factor));sw=max(2,int(w/factor))
            y=(h-sh)//2;x=(w-sw)//2
            # nearest-neighbour indices eliminate per-frame QPainter image allocation
            ys=np.linspace(y,y+sh-1,h).astype(np.intp)
            xs=np.linspace(x,x+sw-1,w).astype(np.intp)
            a=a[ys[:,None],xs[None,:],:]
    out=QImage(a.tobytes(),w,h,w*4,QImage.Format.Format_ARGB32)
    return out.copy() # own buffer after numpy temporary goes out of scope

from functools import lru_cache
import subprocess

@lru_cache(maxsize=24)
def _frozen_preview_frame(ffmpeg, video, target_frame, fps, width, height):
    """Decode the hit frame for seeking/scrubbing: no history dependency."""
    if not ffmpeg or not video:
        return QImage()
    seek = max(0.,target_frame / fps)
    cmd = [ffmpeg, '-hide_banner', '-nostdin', '-loglevel','error',
           '-ss',f'{seek:.8f}', '-i',video, '-an', '-frames:v','1',
           '-vf',f'scale={width}:{height}:flags=lanczos,setsar=1',
           '-f','rawvideo','-pix_fmt','bgra','pipe:1']
    try:
        opts = {}
        if hasattr(subprocess, 'CREATE_NO_WINDOW'):
            opts['creationflags'] = subprocess.CREATE_NO_WINDOW
        result = subprocess.run(cmd,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,
                                timeout=12,**opts)
        expected=width*height*4
        if result.returncode==0 and len(result.stdout)==expected:
            return QImage(result.stdout,width,height,width*4,QImage.Format.Format_ARGB32).copy()
    except (OSError,subprocess.TimeoutExpired):
        pass
    return QImage()

def preview_stutter(image, project, t, ffmpeg):
    fps = max(1, int(project.get('options',{}).get('fps',24)))
    target = stutter_target(project,t,fps)
    if target is None:
        return image
    width,height=image.width(),image.height()
    video=str(project.get('video',''))
    result=_frozen_preview_frame(ffmpeg,video,int(round(target*fps)),fps,width,height)
    return image if result.isNull() else result
