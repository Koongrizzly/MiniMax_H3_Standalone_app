"""Download an optional Faster-Whisper model once into the portable models directory."""
from pathlib import Path
import sys
from huggingface_hub import snapshot_download

MODELS = {'small': 'Systran/faster-whisper-small',
          'medium': 'Systran/faster-whisper-medium',
          'large-v3': 'Systran/faster-whisper-large-v3'}

def main():
    model = sys.argv[1] if len(sys.argv)>1 else 'medium'
    if model not in MODELS:
        raise SystemExit('Model must be small, medium, or large-v3')
    root = Path(__file__).resolve().parent.parent
    folder = root / 'models' / ('whisper-' + model)
    folder.mkdir(parents=True, exist_ok=True)
    print('Downloading', MODELS[model], 'to', folder, flush=True)
    snapshot_download(repo_id=MODELS[model], local_dir=str(folder))
    if not (folder / 'model.bin').is_file() or not (folder / 'config.json').is_file():
        raise RuntimeError('Incomplete model download')
    print('Whisper', model, 'ready for offline use.', flush=True)

if __name__ == '__main__':
    main()
