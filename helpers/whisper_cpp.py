"""Recognize local whisper.cpp files and parse timestamped JSON output."""
from pathlib import Path
import re


def find_cpp_models(folder, max_depth=6):
    root = Path(folder)
    if root.is_file():
        return [root] if is_cpp_model(root) else []
    if not root.is_dir():
        return []
    found = []
    def walk(current, depth):
        if depth > max_depth or len(found) >= 100:
            return
        try:
            for entry in current.iterdir():
                if entry.is_file() and is_cpp_model(entry):
                    found.append(entry)
                elif entry.is_dir() and not entry.is_symlink() and entry.name.lower() not in ('venv', '.git', 'node_modules'):
                    walk(entry, depth + 1)
        except OSError:
            pass
    walk(root, 0)
    return found


def is_cpp_model(path):
    p = Path(path)
    name = p.name.lower()
    return p.is_file() and (name.endswith('.gguf') or (name.startswith(('ggml-', 'gguf-')) and name.endswith('.bin')))


def find_cpp_executables(folder, max_depth=5):
    root = Path(folder)
    if root.is_file():
        return [root] if is_cpp_executable(root) else []
    if not root.is_dir():
        return []
    found = []
    def walk(current, depth):
        if depth > max_depth or len(found) >= 100:
            return
        try:
            for entry in current.iterdir():
                if entry.is_file() and is_cpp_executable(entry):
                    found.append(entry)
                elif entry.is_dir() and not entry.is_symlink() and entry.name.lower() not in ('venv', '.git', 'node_modules'):
                    walk(entry, depth + 1)
        except OSError:
            pass
    walk(root, 0)
    return found


def is_cpp_executable(path):
    p = Path(path)
    return p.is_file() and p.name.lower() in ('whisper-cli.exe', 'main.exe', 'whisper-cli', 'main')


def extract_words(data):
    """Convert whisper.cpp -ojf output to BeatSync words; prefer token word offsets."""
    out = []
    transcription = data.get('transcription', data.get('segments', []))
    for line_no, seg in enumerate(transcription):
        tokens = seg.get('tokens') or []
        if tokens:
            for token in tokens:
                word = (token.get('text') or '').strip()
                ts = token.get('offsets') or token.get('timestamps') or {}
                start, end = ts.get('from'), ts.get('to')
                if not word or start is None or end is None or word.startswith('[_'):
                    continue
                # whisper.cpp JSON full offsets are milliseconds.
                out.append({'w': word, 't0': round(float(start)/1000, 3),
                            't1': round(float(end)/1000, 3), 'line': line_no})
        else:
            # Older whisper.cpp: only segment boundaries, approximate evenly per word.
            text = (seg.get('text') or '').strip()
            ts = seg.get('offsets') or seg.get('timestamps') or {}
            start, end = ts.get('from'), ts.get('to')
            words = re.findall(r'\S+', text)
            if start is None or end is None or not words:
                continue
            duration = max(0, (float(end)-float(start))/1000)
            for index, word in enumerate(words):
                out.append({'w': word, 't0': round(float(start)/1000 + duration*index/len(words),3),
                            't1': round(float(start)/1000 + duration*(index+1)/len(words),3), 'line': line_no})
    return out
