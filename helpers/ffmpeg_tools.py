from __future__ import annotations

import math
import os
import shutil
import subprocess
import threading
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import requests

from runtime.paths import ROOT

BIN_DIR = ROOT / "presets" / "bin"
TOOLS = ("ffmpeg.exe", "ffprobe.exe", "ffplay.exe")
DOWNLOAD_URL = "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip"
DOWNLOAD_NAME = ".ffmpeg-release-essentials.zip"
USER_AGENT = "MiniMax-H3-Standalone/1.0"


def tool_path(name: str) -> Path:
    name = Path(name).name.lower()
    if name not in TOOLS:
        raise ValueError(f"Unsupported FFmpeg tool: {name}")
    return BIN_DIR / name


def missing_tools() -> list[str]:
    return [name for name in TOOLS if not (BIN_DIR / name).is_file()]


def tools_ready() -> bool:
    return not missing_tools()


def verify_tools(run_version_check: bool = True) -> tuple[bool, list[str]]:
    missing = missing_tools()
    if missing:
        return False, [f"Missing {name}" for name in missing]
    if not run_version_check:
        return True, []
    errors: list[str] = []
    for name in TOOLS:
        exe = BIN_DIR / name
        try:
            p = subprocess.run(
                [str(exe), "-version"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=8,
                check=False,
            )
            if p.returncode != 0:
                errors.append(f"{name} returned exit code {p.returncode}")
        except Exception as exc:
            errors.append(f"{name}: {exc}")
    return not errors, errors


def _worker_count() -> int:
    try:
        value = int(os.environ.get("MINIMAX_H3_DOWNLOAD_WORKERS", "8"))
    except Exception:
        value = 8
    return max(2, min(16, value))


def _probe(url: str) -> tuple[int | None, bool]:
    headers = {"User-Agent": USER_AGENT}
    total = None
    ranges = False
    try:
        r = requests.head(url, allow_redirects=True, headers=headers, timeout=30)
        r.raise_for_status()
        if r.headers.get("Content-Length"):
            total = int(r.headers["Content-Length"])
        ranges = "bytes" in (r.headers.get("Accept-Ranges") or "").lower()
    except Exception:
        pass

    if total is None or not ranges:
        try:
            with requests.get(
                url,
                headers={**headers, "Range": "bytes=0-0"},
                stream=True,
                allow_redirects=True,
                timeout=(30, 120),
            ) as r:
                r.raise_for_status()
                if r.status_code == 206:
                    ranges = True
                    content_range = r.headers.get("Content-Range") or ""
                    if "/" in content_range:
                        total = int(content_range.rsplit("/", 1)[1])
                elif total is None and r.headers.get("Content-Length"):
                    total = int(r.headers["Content-Length"])
        except Exception:
            pass
    return total, ranges


def _single_download(url: str, archive: Path, progress, total: int | None = None) -> None:
    part = archive.with_suffix(archive.suffix + ".part")
    headers = {"User-Agent": USER_AGENT}
    start = part.stat().st_size if part.exists() else 0
    if start:
        headers["Range"] = f"bytes={start}-"

    with requests.get(url, headers=headers, stream=True, allow_redirects=True, timeout=(30, 120)) as r:
        if start and r.status_code == 200:
            start = 0
            part.unlink(missing_ok=True)
        r.raise_for_status()

        mode = "ab" if start and r.status_code == 206 else "wb"
        if r.headers.get("Content-Range"):
            try:
                total = int(r.headers["Content-Range"].rsplit("/", 1)[1])
            except Exception:
                pass
        elif total is None and r.headers.get("Content-Length"):
            total = int(r.headers["Content-Length"]) + (start if mode == "ab" else 0)

        done = start if mode == "ab" else 0
        started = last = time.time()
        with open(part, mode) as dst:
            for block in r.iter_content(chunk_size=2 * 1024 * 1024):
                if not block:
                    continue
                dst.write(block)
                done += len(block)
                now = time.time()
                if now - last >= 1:
                    speed = done / max(0.001, now - started) / (1024 ** 2)
                    if total:
                        progress(f"Downloading FFmpeg: {done * 100 / total:.1f}% | {speed:.1f} MiB/s")
                    else:
                        progress(f"Downloading FFmpeg: {done / (1024 ** 2):.0f} MiB | {speed:.1f} MiB/s")
                    last = now

    os.replace(part, archive)


def _parallel_download(url: str, archive: Path, total: int, progress) -> None:
    workers = _worker_count()
    part_dir = BIN_DIR / ".ffmpeg-download-parts"
    shutil.rmtree(part_dir, ignore_errors=True)
    part_dir.mkdir(parents=True, exist_ok=True)

    chunk_size = int(math.ceil(total / workers))
    spans: list[tuple[int, int, int]] = []
    for i in range(workers):
        start = i * chunk_size
        end = min(total - 1, start + chunk_size - 1)
        if start <= end:
            spans.append((i, start, end))
    workers = len(spans)

    state = {
        "bytes": [0] * workers,
        "done": [False] * workers,
    }
    lock = threading.Lock()

    def fetch(slot: int, start: int, end: int) -> None:
        target = part_dir / f"{slot:02d}.part"
        expected = end - start + 1
        downloaded = 0

        for attempt in range(1, 6):
            try:
                existing = target.stat().st_size if target.exists() else 0
                if existing > expected:
                    target.unlink(missing_ok=True)
                    existing = 0
                downloaded = existing
                with lock:
                    state["bytes"][slot] = existing
                if existing >= expected:
                    with lock:
                        state["done"][slot] = True
                    return

                headers = {
                    "User-Agent": USER_AGENT,
                    "Range": f"bytes={start + existing}-{end}",
                }
                with requests.get(
                    url,
                    headers=headers,
                    stream=True,
                    allow_redirects=True,
                    timeout=(30, 120),
                ) as r:
                    r.raise_for_status()
                    if r.status_code != 206:
                        raise RuntimeError(f"range request returned HTTP {r.status_code}")
                    with open(target, "ab") as dst:
                        for block in r.iter_content(chunk_size=2 * 1024 * 1024):
                            if not block:
                                continue
                            dst.write(block)
                            downloaded += len(block)
                            with lock:
                                state["bytes"][slot] = downloaded

                if downloaded != expected:
                    raise RuntimeError(f"incomplete range {slot + 1}")

                with lock:
                    state["done"][slot] = True
                return
            except Exception:
                if attempt >= 5:
                    raise
                time.sleep(attempt)

    started = last = time.time()
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(fetch, slot, start, end) for slot, start, end in spans]
        while True:
            if all(f.done() for f in futures):
                break
            now = time.time()
            if now - last >= 1:
                with lock:
                    done_bytes = sum(state["bytes"])
                    complete = sum(1 for x in state["done"] if x)
                speed = done_bytes / max(0.001, now - started) / (1024 ** 2)
                active = workers - complete
                progress(
                    f"Downloading FFmpeg: {done_bytes * 100 / total:.1f}% | "
                    f"{speed:.1f} MiB/s | {active} active"
                )
                last = now
            time.sleep(0.2)

        for future in futures:
            future.result()

    with open(archive, "wb") as dst:
        for slot, _start, _end in spans:
            with open(part_dir / f"{slot:02d}.part", "rb") as src:
                shutil.copyfileobj(src, dst, length=8 * 1024 * 1024)

    if archive.stat().st_size != total:
        raise RuntimeError(
            f"FFmpeg download size mismatch: {archive.stat().st_size} bytes, expected {total}"
        )

    shutil.rmtree(part_dir, ignore_errors=True)


def _download_archive(archive: Path, progress) -> None:
    total, ranges = _probe(DOWNLOAD_URL)
    try:
        if total and ranges and total >= 32 * 1024 * 1024:
            _parallel_download(DOWNLOAD_URL, archive, total, progress)
        else:
            _single_download(DOWNLOAD_URL, archive, progress, total)
    except Exception as exc:
        # A mirror/CDN can occasionally advertise range support but reject
        # parallel requests. Retry cleanly with one stream rather than failing
        # the first-start setup.
        progress(f"Retrying FFmpeg download...")
        shutil.rmtree(BIN_DIR / ".ffmpeg-download-parts", ignore_errors=True)
        archive.unlink(missing_ok=True)
        _single_download(DOWNLOAD_URL, archive, progress, total)


def ensure_ffmpeg_tools(progress=print) -> tuple[bool, str]:
    """Ensure ffmpeg/ffprobe/ffplay live directly in ROOT/presets/bin."""
    BIN_DIR.mkdir(parents=True, exist_ok=True)
    missing = missing_tools()
    if not missing:
        ok, errors = verify_tools(True)
        if ok:
            return True, f"FFmpeg tools ready in {BIN_DIR}"
        progress("Existing FFmpeg tools failed verification; reinstalling them.")
        missing = list(TOOLS)

    archive = BIN_DIR / DOWNLOAD_NAME
    part_dir = BIN_DIR / ".ffmpeg-download-parts"
    try:
        progress("FFmpeg tools are missing.")
        progress("Downloading FFmpeg...")
        _download_archive(archive, progress)

        progress("Extracting ffmpeg.exe, ffprobe.exe and ffplay.exe ...")
        found: dict[str, str] = {}
        with zipfile.ZipFile(archive, "r") as zf:
            for member in zf.namelist():
                low = member.replace("\\", "/").lower()
                for name in TOOLS:
                    if low.endswith("/bin/" + name) or low == name:
                        found[name] = member
            absent = [n for n in TOOLS if n not in found]
            if absent:
                raise RuntimeError("Downloaded archive does not contain: " + ", ".join(absent))
            for name in TOOLS:
                target = BIN_DIR / name
                tmp = BIN_DIR / (name + ".new")
                with zf.open(found[name], "r") as src, open(tmp, "wb") as dst:
                    shutil.copyfileobj(src, dst)
                os.replace(tmp, target)

        ok, errors = verify_tools(True)
        if not ok:
            raise RuntimeError("FFmpeg verification failed: " + "; ".join(errors))
        progress(f"FFmpeg tools verified in {BIN_DIR}")
        return True, f"FFmpeg tools installed and verified in {BIN_DIR}"
    except Exception as exc:
        return False, str(exc)
    finally:
        archive.unlink(missing_ok=True)
        shutil.rmtree(part_dir, ignore_errors=True)
        part = archive.with_suffix(archive.suffix + ".part")
        part.unlink(missing_ok=True)
        for name in TOOLS:
            try:
                tmp = BIN_DIR / (name + ".new")
                if tmp.exists():
                    tmp.unlink()
            except Exception:
                pass
