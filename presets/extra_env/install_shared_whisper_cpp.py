"""Install shared whisper.cpp and report real errors to a persistent log."""
from pathlib import Path
import sys
import traceback
from datetime import datetime

ROOT = Path(__file__).resolve().parents[2]
LOG = ROOT / 'logs' / 'install_shared_whisper_cpp.log'


def log(message):
    line = str(message)
    print(line, flush=True)
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open('a', encoding='utf-8') as stream:
        stream.write(line + '\n')


def main():
    log('\n=== shared whisper.cpp installer: ' + datetime.now().isoformat(timespec='seconds') + ' ===')
    log('Root: ' + str(ROOT))
    sys.path.insert(0, str(ROOT))
    try:
        from helpers.minimax_music_clip import _download_whisper_cpp_task, _whisper_cli_path, WHISPER_MODEL
        log('Using existing Music Clip Creator downloader')
        _download_whisper_cpp_task(log)
        exe = _whisper_cli_path()
        if exe is None or not exe.is_file():
            raise RuntimeError('whisper-cli.exe is missing after installation')
        if not WHISPER_MODEL.is_file() or WHISPER_MODEL.stat().st_size < 100_000_000:
            raise RuntimeError('Whisper GGML model is missing or incomplete: ' + str(WHISPER_MODEL))
        log('SUCCESS: ' + str(exe))
        log('MODEL: ' + str(WHISPER_MODEL))
        return 0
    except BaseException:
        log('INSTALL FAILED:')
        for line in traceback.format_exc().rstrip().splitlines():
            log(line)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
