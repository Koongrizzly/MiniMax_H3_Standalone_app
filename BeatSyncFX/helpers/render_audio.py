"""Prepare source-specific audio envelopes before any render frames are drawn."""
from pathlib import Path

from .core import decode_mono, analyze_audio
from .audio_reactivity import extract_envelopes, layer_reaction_mode


def audio_signature(project):
    source = project.get('audio') or project.get('video')
    if not source:
        return None
    try:
        path = Path(source).resolve()
        stat = path.stat()
        return dict(path=str(path), size=stat.st_size, mtime_ns=stat.st_mtime_ns)
    except OSError:
        return None


def needs_beat_grid(project):
    if project.get('options',{}).get('beat_style','Off')!='Off':return True
    if any((str(e.get('style','')).startswith('Beat: ') and e.get('beat_trigger')!='Continuous')
           or e.get('style')=='Beat punch' for e in project.get('events',[])):return True
    return any(fx.get('enabled',True) and (fx.get('source')=='Beat' or layer_reaction_mode(fx)=='Beat Punch')
               for layer in project.get('media_layers',[]) for fx in layer.get('media_reactions',[]))


def prepare_render_tempo(project,ffmpeg,status=None):
    """Queue snapshots can be taken while GUI analysis is still pending.
    Respect older projects' saved grids; resolve source-specific pending/new grids.
    """
    signature=audio_signature(project)
    if signature is None or 'beat_analysis_source' not in project:return
    if project.get('beat_analysis_source')==signature:return
    if status:status('Detecting current soundtrack tempo before rendering...')
    try:
        result=analyze_audio(ffmpeg,signature['path'])
    except Exception:
        if needs_beat_grid(project):raise
        return
    if audio_signature(project)!=signature:raise ValueError('The audio source changed during tempo analysis.')
    project['bpm']=result['bpm'];project['t0']=round(result['t0'],3)
    project['beat_confidence']=result.get('confidence',0)
    project['beat_analysis_source']=signature;project['beat_grid_manual']=False


def needs_audio_analysis(project):
    return any(e.get('style') in ('Equalizer 3-Band', 'Equalizer 12-Band', 'Equalizer LED', 'Equalizer Radial', 'Equalizer Mirror', 'Beat Heart')
               or e.get('audio_react', 'Off') != 'Off'
               for e in project.get('events', [])) or any(
                   fx.get('enabled', True) and fx.get('source', 'Bass') != 'Beat'
                   for layer in project.get('media_layers', [])
                   for fx in layer.get('media_reactions', []))


def analysis_current(project, signature):
    data = project.get('audio_envelopes')
    return (signature is not None and isinstance(data, dict)
            and bool(data.get('values')) and bool(data.get('bands12'))
            and data.get('hop_seconds', 0) > 0
            and project.get('audio_envelopes_source') == signature)


def prepare_render_audio(project, ffmpeg, status=None):
    """Update source-specific rhythm and frequency caches; preserve verified/manual grids, lyrics and cues.

    Runs in the render thread. Fail before creating an output if the required
    analysis cannot be produced, rather than silently exporting inactive effects.
    """
    prepare_render_tempo(project,ffmpeg,status)
    if not needs_audio_analysis(project):
        return None
    signature = audio_signature(project)
    if analysis_current(project, signature):
        return None
    if signature is None:
        raise ValueError('The audio source is missing or unavailable.')
    if status:
        status('Analyzing current audio for bass-reactive effects before rendering...')
    samples = decode_mono(ffmpeg, signature['path'], sr=11025)
    analysis = extract_envelopes(samples, sample_rate=11025)
    if audio_signature(project) != signature:
        raise ValueError('The audio source changed during analysis. Start the render again.')
    project['audio_envelopes'] = analysis
    project['audio_envelopes_source'] = signature
    return analysis, signature
