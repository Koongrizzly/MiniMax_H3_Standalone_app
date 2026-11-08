"""Prepare source-specific audio envelopes before any render frames are drawn."""
from pathlib import Path

from .core import decode_mono
from .audio_reactivity import extract_envelopes


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


def needs_audio_analysis(project):
    return any(e.get('style') in ('Equalizer 3-Band', 'Equalizer 12-Band', 'Beat Heart')
               or e.get('audio_react', 'Off') != 'Off'
               for e in project.get('events', []))


def analysis_current(project, signature):
    data = project.get('audio_envelopes')
    return (signature is not None and isinstance(data, dict)
            and bool(data.get('values')) and bool(data.get('bands12'))
            and data.get('hop_seconds', 0) > 0
            and project.get('audio_envelopes_source') == signature)


def prepare_render_audio(project, ffmpeg, status=None):
    """Update only the audio cache; preserve BPM, beat offset, lyrics and cues.

    Runs in the render thread. Fail before creating an output if the required
    analysis cannot be produced, rather than silently exporting inactive effects.
    """
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
