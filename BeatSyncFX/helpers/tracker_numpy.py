"""Experimental local template tracker; NumPy only (no cv2, no AI weights)."""
from __future__ import annotations
import json
import math
import subprocess
from pathlib import Path
import numpy as np


def gray(frame):
    a=np.asarray(frame,dtype=np.float32)
    if a.ndim==2:return a
    return a[...,:3] @ np.array([.299,.587,.114],np.float32)


def locate(previous, current, box, search=65, min_confidence=.48):
    """Match a previous-frame template near the last center; return (box, confidence).

    box is (x,y,w,h) in *decoded frame* pixels. Returns None if uncertain.
    A coarse 12x12 patch signature narrows candidates; full-res MAD refines top matches.
    """
    a=gray(previous); b=gray(current)
    x,y,w,h=map(int,box)
    H,W=b.shape
    if w<8 or h<8 or x<0 or y<0 or x+w>a.shape[1] or y+h>a.shape[0]:return None,0.0
    old=a[y:y+h,x:x+w]
    # Avoid matching near-uniform patches, where every flat surface looks alike.
    contrast=float(old.std())
    if contrast<5:return None,0.0
    step=max(2,min(w,h)//12)
    yy=np.arange(step//2,h,step,dtype=np.int32)
    xx=np.arange(step//2,w,step,dtype=np.int32)
    if len(xx)<4 or len(yy)<4:return None,0.0
    ref=old[np.ix_(yy,xx)]
    reach=max(5,int(search))
    # Evaluate the complete search grid in NumPy rather than hundreds of
    # thousands of Python-level loops (the old version looked frozen in the GUI).
    from numpy.lib.stride_tricks import sliding_window_view
    xmin=max(0,x-reach); xmax=min(W-w,x+reach)
    ymin=max(0,y-reach); ymax=min(H-h,y+reach)
    # Sparse signature sampling: one small vectorized array per reference pixel.
    scores=np.zeros((ymax-ymin+1,xmax-xmin+1),dtype=np.float32)
    means=np.zeros_like(scores)
    for iy in yy:
        for ix in xx:
            means += b[ymin+iy:ymax+iy+1,xmin+ix:xmax+ix+1]
    count=len(yy)*len(xx)
    means /= count
    ref_centered=ref-ref.mean()
    for i,iy in enumerate(yy):
        for j,ix in enumerate(xx):
            scores += np.abs(b[ymin+iy:ymax+iy+1,xmin+ix:xmax+ix+1]-means-ref_centered[i,j])
    scores /= count
    best_count=min(24,scores.size)
    flat=np.argpartition(scores.ravel(),best_count-1)[:best_count]
    ys,xs=np.unravel_index(flat,scores.shape)
    candidates=[(float(scores[ry,rx]),int(xmin+rx),int(ymin+ry)) for ry,rx in zip(ys,xs)]
    candidates.sort(key=lambda v:v[0])
    fine=[]
    for _,cx,cy in candidates[:24]:
        for ny in range(max(0,cy-2),min(H-h,cy+2)+1):
            for nx in range(max(0,cx-2),min(W-w,cx+2)+1):
                part=b[ny:ny+h,nx:nx+w]
                err=float(np.mean(np.abs((part-part.mean())-(old-old.mean()))))
                fine.append((err,nx,ny))
    if not fine:return None,0.0
    fine.sort(key=lambda v:v[0])
    error,nx,ny=fine[0]
    confidence=max(0.0,1.0-error/max(18.0,contrast*1.6))
    if confidence<min_confidence:return None,confidence
    return (int(nx),int(ny),w,h),confidence


def track_frames(frames, initial_box, times=None, search=40, threshold=.48, progress=None):
    """Return list of (time, box, confidence), stopping at first uncertain frame."""
    frames=list(frames)
    if not frames:return []
    if times is None:times=[float(i) for i in range(len(frames))]
    box=tuple(map(int,initial_box))
    result=[(float(times[0]),box,1.0)]
    for i in range(1,len(frames)):
        found,confidence=locate(frames[i-1],frames[i],box,search,threshold)
        if found is None:break
        box=found
        result.append((float(times[i]),box,confidence))
        if progress: progress(i+1,len(frames))
    return result


def to_shape_keyframes(result, video_width, video_height, stride=2):
    """Convert decoded-frame rectangles to V3.7.6 centered normalized shape keys."""
    keys=[]
    for index,(t,(x,y,w,h),conf) in enumerate(result):
        if index % max(1,stride) and index != len(result)-1:continue
        keys.append({'time':round(float(t),3), 'x':round(100*(x+w/2)/video_width,5),
                     'y':round(100*(y+h/2)/video_height,5),
                     'frame_width':round(100*w/video_width,5),
                     'frame_height':round(100*h/video_height,5), 'rotation':0.0})
    return keys


def video_info(ffprobe, path):
    cmd=[ffprobe,'-v','error','-select_streams','v:0','-show_entries','stream=width,height:format=duration','-of','json',str(path)]
    p=subprocess.run(cmd,capture_output=True,text=True,check=True)
    data=json.loads(p.stdout)
    s=data['streams'][0]
    return int(s['width']),int(s['height']),float(data.get('format',{}).get('duration',0))


def decode_segment(ffmpeg, path, start, duration=8, fps=6, width=640):
    """Decode a bounded segment into RGB array frames for repeatable offline testing."""
    original_w,original_h=video_info(str(Path(ffmpeg).with_name('ffprobe.exe' if Path(ffmpeg).suffix.lower()=='.exe' else 'ffprobe')),path)[:2]
    height=max(2,round(original_h*width/original_w)//2*2)
    cmd=[str(ffmpeg),'-hide_banner','-loglevel','error','-ss',str(float(start)),'-i',str(path),
         '-t',str(float(duration)),'-vf',f'fps={fps},scale={width}:{height}',
         '-an','-f','rawvideo','-pix_fmt','rgb24','-']
    proc=subprocess.run(cmd,capture_output=True,timeout=45)
    if proc.returncode:raise RuntimeError(proc.stderr.decode(errors='replace')[-1500:])
    frame_bytes=width*height*3
    count=len(proc.stdout)//frame_bytes
    if count==0:raise RuntimeError('No video frames decoded')
    frames=np.frombuffer(proc.stdout[:count*frame_bytes],dtype=np.uint8).reshape(count,height,width,3).copy()
    return frames,[float(start)+i/fps for i in range(count)]
