from __future__ import annotations

from runtime.ffmpeg_tools import ensure_ffmpeg_tools


def main() -> int:
    ok, message = ensure_ffmpeg_tools(lambda s: print(s, flush=True))
    print(("FFMPEG_SETUP_OK: " if ok else "FFMPEG_SETUP_FAILED: ") + message, flush=True)
    return 0 if ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
