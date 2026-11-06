"""Install or verify FFmpeg through GrizzlyMax's existing downloader."""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from runtime.ffmpeg_tools import ensure_ffmpeg_tools

if __name__ == '__main__':
    ok, message = ensure_ffmpeg_tools()
    print(message, flush=True)
    raise SystemExit(0 if ok else 1)
