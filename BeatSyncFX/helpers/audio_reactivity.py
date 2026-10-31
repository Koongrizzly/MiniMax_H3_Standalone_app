"""Offline multiband analysis and deterministic timeline lookup."""
from __future__ import annotations
import math
import numpy as np

BANDS = ('Bass', 'Midrange', 'Treble', 'Overall Energy')

def extract_envelopes(samples, sample_rate=11025, hop=256, nfft=2048):
    signal = np.asarray(samples, dtype=np.float32)
    if len(signal) < nfft:
        raise ValueError('Audio too short for multiband analysis')
    freqs = np.fft.rfftfreq(nfft, 1 / sample_rate)
    ranges = ((35, 180), (180, 2500), (2500, min(5200, sample_rate/2-1)), (35, min(5200, sample_rate/2-1)))
    masks = [(freqs >= low) & (freqs < high) for low, high in ranges]
    window = np.hanning(nfft).astype(np.float32)
    # Log-spaced bins provide a real 12-band spectrum, not duplicated 3-band values.
    edges = np.geomspace(35, min(5200, sample_rate/2-1), 13)
    fine_masks = [(freqs >= lo) & (freqs < hi) for lo, hi in zip(edges[:-1], edges[1:])]
    fine_frames=[]
    frames=[]
    for offset in range(0, len(signal)-nfft+1, hop):
        power = np.abs(np.fft.rfft(signal[offset:offset+nfft]*window))**2
        frames.append([float(np.sqrt(np.mean(power[m]))) if np.any(m) else 0.0 for m in masks])
        fine_frames.append([float(np.sqrt(np.mean(power[m]))) if np.any(m) else 0.0 for m in fine_masks])
    data=np.asarray(frames,dtype=np.float32)
    # Dynamic compression and robust normalization, independent for each frequency band.
    data=np.log1p(data)
    output=np.zeros_like(data)
    for col in range(4):
        x=data[:,col]
        floor=float(np.percentile(x,12))
        ceiling=float(np.percentile(x,96))
        output[:,col]=np.clip((x-floor)/max(.0001,ceiling-floor),0,1)
    fine = np.log1p(np.asarray(fine_frames, dtype=np.float32))
    fine_out = np.zeros_like(fine)
    for col in range(12):
        x = fine[:, col]
        lo, hi = float(np.percentile(x,12)), float(np.percentile(x,96))
        fine_out[:,col] = np.clip((x-lo)/max(.0001,hi-lo),0,1)
    return {'hop_seconds':round(hop/sample_rate,9), 'values':np.round(output,3).tolist(),
            'bands12':np.round(fine_out,3).tolist(), 'version':2}

def energy_at(analysis, t, band='Bass', smooth=0.12):
    if not isinstance(analysis,dict) or not analysis.get('values'):
        return 0.0
    values=analysis['values']
    step=float(analysis.get('hop_seconds',0))
    if step<=0: return 0.0
    col={'Bass':0,'Midrange':1,'Treble':2,'Overall Energy':3}.get(band,0)
    pos=max(0.,float(t))/step
    def at(index):
        idx=min(max(0,int(index)),len(values)-1)
        return float(values[idx][col])
    if smooth<=0:
        left=math.floor(pos)
        return at(left)*(1-(pos-left))+at(left+1)*(pos-left)
    half=max(1,round(float(smooth)/step/2))
    center=round(pos)
    return sum(at(i) for i in range(center-half,center+half+1))/(2*half+1)

def reaction_transform(event, project, t):
    band=event.get('audio_react','Off')
    if band=='Off':return 1.0,0.0
    value=energy_at(project.get('audio_envelopes'),t,band,float(event.get('audio_smooth',0.12)))
    strength=max(0,min(2,float(event.get('audio_strength',65))/100))
    return 1.0+value*strength*1.0, value*strength


def spectrum_at(analysis, t, smooth=0.09):
    """Twelve real frequency bands; interpolate old projects' 3-band envelopes."""
    if not isinstance(analysis, dict): return [0.0]*12
    values = analysis.get('bands12')
    step = float(analysis.get('hop_seconds',0))
    if values and step>0:
        i = max(0, min(len(values)-1, round(float(t)/step)))
        half = max(0, round(max(0.,smooth)/step/2))
        subset = values[max(0,i-half):min(len(values),i+half+1)]
        return [sum(float(row[k]) for row in subset)/len(subset) for k in range(12)]
    bass = energy_at(analysis,t,'Bass',smooth)
    mid = energy_at(analysis,t,'Midrange',smooth)
    treb = energy_at(analysis,t,'Treble',smooth)
    return [bass*(1-v)+mid*v for v in (0,.25,.5,.75)] + [mid*(1-v)+treb*v for v in (0,.14,.28,.42,.57,.71,.85,1)]
