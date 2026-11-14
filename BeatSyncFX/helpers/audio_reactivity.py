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


LAYER_REACTIONS = ('Bass Speaker', 'Beat Bounce', 'Bass Shake', 'Reactive Rotation',
                   'Beat Flash', 'Reactive Glow', 'RGB Split', 'Elastic Pulse',
                   'Beat Zoom', 'Reactive Opacity', 'Color Shift', 'Reactive Ripple',
                   'Horizontal Stretch', 'Vertical Stretch')
LAYER_SOURCES = (*BANDS, 'Beat')


def default_layer_reaction(kind):
    return dict(kind=kind, enabled=True, mode='Beat Punch', source='Beat',
                intensity=100, sensitivity=100, smooth=.02, pulse=.10, color='#22DDEE')


def beat_phase(project, t):
    bpm=float(project.get('bpm',120)); t0=float(project.get('t0',0))
    if bpm<=0 or t<t0:return -1,0.,t0
    period=60./bpm
    n=math.floor((t-t0)/period+1e-8)
    hit=t0+n*period
    return n,max(0.,t-hit),hit


def screen_beat_pulse(project, t, event):
    """Shared deterministic trigger for both vector and source-pixel effects."""
    mode=event.get('beat_trigger','Every beat')
    n,elapsed,hit=beat_phase(project,t)
    if mode=='Continuous':
        return 1., max(0,n), float(event.get('start',0))
    if n<0 or n%max(1,int(event.get('beat_every',1))):return 0.,n,hit
    if event.get('beat_trigger') and hit<float(event.get('start',0))-.001:return 0.,n,hit
    if mode=='Random beats':
        # Stable across seeking, preview and export; no global random state.
        import hashlib
        key=f"{event.get('style',event.get('beat_style',''))}|{event.get('start',0)}|{n}"
        roll=int.from_bytes(hashlib.blake2s(key.encode(),digest_size=4).digest(),'big')/2**32
        if roll>=max(0.,min(1.,float(event.get('beat_chance',50))/100)):return 0.,n,hit
    length=max(.03,float(event.get('beat_decay',.24)))
    strength=punch_envelope(elapsed,length) if event.get('beat_trigger') else max(0.,1.-elapsed/length)**2
    return strength,n,hit


def punch_envelope(elapsed, duration=.10):
    """Immediate peak, short hold and quick recovery; frame-rate independent."""
    if elapsed<0 or elapsed>=duration:return 0.
    hold=min(.035,duration*.35)
    if elapsed<=hold:return 1.
    return max(0.,1.-(elapsed-hold)/max(.001,duration-hold))


def layer_reaction_mode(fx):
    return fx.get('mode','Beat Punch' if fx.get('source')=='Beat' or fx.get('kind') in ('Bass Speaker','Beat Bounce','Beat Zoom') else 'Continuous')


def layer_reaction_values(layer, project, t):
    """Stateless, timeline-synchronized strengths for each enabled stack entry."""
    master=max(0.,min(3.,float(layer.get('media_reaction_strength',100))/100))
    for fx in layer.get('media_reactions', []):
        if not fx.get('enabled', True) or fx.get('kind') not in LAYER_REACTIONS:
            continue
        source=fx.get('source','Bass')
        smooth=max(0.,min(1.,float(fx.get('smooth',.06))))
        mode=layer_reaction_mode(fx)
        n,elapsed,hit=beat_phase(project,t)
        if mode=='Beat Punch':
            duration=max(.03,min(1.,float(fx.get('pulse',.10))))
            value=punch_envelope(elapsed,duration) if n>=0 else 0.
            # Band-selected punches are weighted by energy sampled at the beat.
            if source!='Beat' and value>0:
                energy=energy_at(project.get('audio_envelopes'),hit,source,smooth)
                value*=min(1.,energy*1.5)
        elif source=='Beat':
            value=max(0.,1.-elapsed/max(.04,.12+smooth))**2 if n>=0 else 0.
        else:
            value=energy_at(project.get('audio_envelopes'),t,source,smooth)
        value=min(1.,max(0.,value*float(fx.get('sensitivity',100))/100))
        strength=min(3.,max(0.,float(fx.get('intensity',35))/100))
        yield fx,min(3.,value*strength*master)
