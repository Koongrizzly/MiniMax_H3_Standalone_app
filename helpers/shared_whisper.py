"""Shared GrizzlyMax Whisper path discovery. No model installation on import."""
from pathlib import Path
import json
ROOT = Path(__file__).resolve().parents[1]
CFG = ROOT/'presets/setsave/whisper_shared.json'
def settings():
    try:
        d=json.loads(CFG.read_text(encoding='utf-8'))
        return d if isinstance(d,dict) else {}
    except (OSError,ValueError): return {}
def shared_python():
    for p in (ROOT/'environments/.whisper/Scripts/python.exe',ROOT/'environments/.whisper/python.exe'):
        if p.is_file(): return p
    return None
def ct2_medium():
    d=settings()
    paths=[d.get('faster_medium',''),ROOT/'models/whisper/faster-whisper-medium',ROOT/'BeatSyncFX/models/whisper-medium']
    try:
        choices=json.loads((ROOT/'presets/setsave/install_preflight_paths.json').read_text(encoding='utf-8'))
        paths.append(choices.get('beat_whisper_medium',''))
    except (OSError,ValueError,AttributeError): pass
    for raw in paths:
        if not raw: continue
        x=Path(raw)
        if (x/'model.bin').is_file() and (x/'config.json').is_file(): return x
    return None
def cpp_exe():
    d=settings()
    for raw in [d.get('cpp_exe',''), ROOT/'presets/bin/whisper/whisper-cli.exe']:
        if raw and Path(raw).is_file(): return Path(raw)
    return None
def cpp_medium():
    d=settings()
    for raw in [d.get('cpp_medium',''),ROOT/'models/whisper/ggml-medium.bin',ROOT/'presets/bin/whisper/ggml-medium.bin']:
        if raw and Path(raw).is_file(): return Path(raw)
    return None
