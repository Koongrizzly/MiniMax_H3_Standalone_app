"""Detect local Whisper installations; only CT2 models are loadable here."""
from pathlib import Path
import os

SKIP_DIRS = {'.git', 'node_modules', '__pycache__', '.venv', 'venv', 'site-packages', '.cache_tmp'}

def scan_whisper(root: Path, max_dirs: int = 15000):
    root = Path(root).expanduser()
    if not root.is_dir():
        return [], [], False
    compatible, other = [], []
    visited = 0
    for current, subdirs, filenames in os.walk(root, followlinks=False):
        visited += 1
        if visited > max_dirs:
            return compatible, other, True
        subdirs[:] = [d for d in subdirs if d not in SKIP_DIRS]
        files = set(filenames)
        folder = Path(current)
        if {'model.bin', 'config.json'}.issubset(files):
            compatible.append(folder)
            subdirs[:] = []
            continue
        for name in filenames:
            lower = name.lower()
            if (lower.endswith(('.pt','.gguf')) or
                (lower.startswith('ggml-') and lower.endswith('.bin')) or
                lower in ('whisper-cli.exe','main.exe','whisper.exe','whisperx.exe')):
                kind = ('PyTorch model' if lower.endswith('.pt') else
                        'whisper.cpp model' if lower.endswith(('.gguf','.bin')) else
                        'Whisper executable (model type unknown)')
                other.append((kind, folder / name))
    return sorted(compatible), sorted(other, key=lambda x: str(x[1]).casefold()), False

def find_ct2_models(root: Path, max_dirs: int = 15000):
    compatible, _, _ = scan_whisper(root, max_dirs)
    return compatible

def diagnostic(root: Path):
    compatible, other, truncated = scan_whisper(root)
    lines = [f'Folder: {root}', f'CTranslate2 models (usable now): {len(compatible)}',
             f'Other Whisper files found: {len(other)}']
    for kind, path in other[:20]:
        lines.append(f'  {kind}: {path}')
    if len(other) > 20: lines.append(f'  ...and {len(other)-20} more')
    if truncated: lines.append('Search limit reached; choose a narrower folder.')
    if not other and not compatible:
        lines.append('No recognizable model files in this folder. It may contain only binaries or package files.')
    lines.append('Only CTranslate2 models can be loaded by the current transcription backend.')
    return '\n'.join(lines)
