from __future__ import annotations

import math
import os
import sys
import time
import shutil
import threading
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_EXCEPTION
from pathlib import Path
from urllib.parse import quote

import requests

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "models" / "minimax_h3"
DIFF = BASE / "diffusion_models"
TE = BASE / "text_encoders"
VVAE = BASE / "video_vae"
AVAE = BASE / "audio_vae"
LORAS = BASE / "loras"
for d in (DIFF, TE, VVAE, AVAE, LORAS):
    d.mkdir(parents=True, exist_ok=True)

HF = "https://huggingface.co"
REPO = "koongrizzly/MiniMax_H3_int4_W4A8_ConvRot_Pruned"
HYBRID_REPO = "berryber09/MiniMax-H3-ref2va-fl2va-hybrid-w4a8"

REMOTE_FL2VA = "diffusion_models/minimax_h3_fl2va_pruned-w4a8_convrot_pruned.safetensors"
REMOTE_REF2VA = "diffusion_models/minimax_h3_ref2va_pruned-w4a8_convrot_pruned.safetensors"
REMOTE_TE = "text_encoders/qwen3vl_32b_minimax_h3-w4a8_convrot.safetensors"
REMOTE_VVAE = "video_vae/Minimax_h3_video_vae_fp16.safetensors"
REMOTE_AVAE = "audio_vae/Minimax_H3_fp32_audio_vae.safetensors"

LOCAL_FL2VA = "minimax_h3_fl2va_pruned-w4a8_convrot_pruned.safetensors"
LOCAL_REF2VA = "minimax_h3_ref2va_pruned-w4a8_convrot_pruned.safetensors"
LOCAL_TE = "qwen3vl_32b_minimax_h3-w4a8_convrot.safetensors"
LOCAL_VVAE = "MiniMax-H3-video_vae_fp16.safetensors"
LOCAL_AVAE = "MiniMax-H3-audio_vae_fp32.safetensors"

USER_AGENT = "MiniMax-H3-standalone-downloader/3.2"
S = requests.Session()
S.headers.update({"User-Agent": USER_AGENT})


def gb(n: int | None) -> str:
    if n is None:
        return "?"
    return f"{n / (1024 ** 3):.2f} GiB"


def mibps(n: float) -> str:
    return f"{n / (1024 ** 2):6.1f} MiB/s"


def env_int(name: str, default: int, lo: int, hi: int) -> int:
    try:
        v = int(os.environ.get(name, str(default)).strip())
    except Exception:
        v = default
    return max(lo, min(hi, v))


def repo_files(repo: str = REPO) -> set[str]:
    r = S.get(f"{HF}/api/models/{repo}", timeout=30)
    r.raise_for_status()
    return {x.get("rfilename") for x in r.json().get("siblings", []) if x.get("rfilename")}


def live_lora_files() -> list[dict]:
    url = f"{HF}/api/models/{REPO}/tree/main/loras"
    try:
        r = S.get(url, params={"recursive": "false", "expand": "false"}, timeout=30)
        r.raise_for_status()
        items = []
        for x in r.json():
            path = x.get("path") or ""
            if x.get("type") == "file" and path.lower().endswith(".safetensors"):
                items.append({"path": path, "size": x.get("size")})
        if items:
            return sorted(items, key=lambda x: x["path"].lower())
    except Exception:
        pass
    return [
        {"path": p, "size": None}
        for p in sorted(repo_files())
        if p.startswith("loras/") and p.lower().endswith(".safetensors")
    ]


def remote_size(remote_path: str, repo: str = REPO) -> int | None:
    url = f"{HF}/{repo}/resolve/main/{quote(remote_path, safe='/')}?download=true"
    try:
        r = S.head(url, allow_redirects=True, timeout=30)
        if r.ok and r.headers.get("Content-Length"):
            return int(r.headers["Content-Length"])
    except Exception:
        pass
    return None


def _probe_download(url: str) -> tuple[int | None, bool]:
    total = None
    supports_ranges = False
    try:
        r = S.head(url, allow_redirects=True, timeout=30)
        r.raise_for_status()
        if r.headers.get("Content-Length"):
            total = int(r.headers["Content-Length"])
        ar = (r.headers.get("Accept-Ranges") or "").lower()
        supports_ranges = "bytes" in ar
    except Exception:
        pass
    if total is None or not supports_ranges:
        try:
            r = S.get(url, headers={"Range": "bytes=0-0"}, stream=True, allow_redirects=True, timeout=(30, 120))
            r.raise_for_status()
            if r.status_code == 206:
                supports_ranges = True
                if r.headers.get("Content-Range"):
                    total = int(r.headers["Content-Range"].split("/")[-1])
            elif r.headers.get("Content-Length") and total is None:
                total = int(r.headers["Content-Length"])
            r.close()
        except Exception:
            pass
    return total, supports_ranges


def _single_stream_download(url: str, out: Path, known_total: int | None = None) -> Path:
    part = out.with_suffix(out.suffix + ".part")
    start = part.stat().st_size if part.exists() else 0
    headers = {"Range": f"bytes={start}-"} if start else {}
    with S.get(url, stream=True, allow_redirects=True, headers=headers, timeout=(30, 120)) as r:
        if start and r.status_code == 200:
            start = 0
            try:
                part.unlink()
            except FileNotFoundError:
                pass
        r.raise_for_status()
        mode = "ab" if start and r.status_code == 206 else "wb"
        total = known_total
        if r.headers.get("Content-Range"):
            try:
                total = int(r.headers["Content-Range"].split("/")[-1])
            except Exception:
                pass
        elif r.headers.get("Content-Length") and total is None:
            total = int(r.headers["Content-Length"]) + (start if mode == "ab" else 0)
        done = start if mode == "ab" else 0
        started = last = time.time()
        with open(part, mode) as f:
            for chunk in r.iter_content(chunk_size=8 * 1024 * 1024):
                if not chunk:
                    continue
                f.write(chunk)
                done += len(chunk)
                now = time.time()
                if now - last >= 1.0:
                    speed = done / max(0.001, now - started)
                    if total:
                        print(f"      {gb(done)}/{gb(total)} ({done * 100 / total:5.1f}%) | {mibps(speed)} | single stream", flush=True)
                    else:
                        print(f"      {gb(done)} | {mibps(speed)} | single stream", flush=True)
                    last = now
    os.replace(part, out)
    return out


def _parallel_download(url: str, out: Path, total: int, workers: int) -> Path:
    part_dir = out.with_suffix(out.suffix + ".parts")
    if out.exists():
        out.unlink()
    part_dir.mkdir(parents=True, exist_ok=True)
    spans = []
    chunk = int(math.ceil(total / workers))
    for i in range(workers):
        start = i * chunk
        end = min(total - 1, start + chunk - 1)
        if start > end:
            break
        spans.append((i, start, end))
    workers = len(spans)

    progress = {
        "bytes": [0] * workers,
        "done": [False] * workers,
        "failed": False,
        "errors": [],
    }
    lock = threading.Lock()

    def worker(slot: int, start: int, end: int):
        part_path = part_dir / f"part_{slot:02d}.bin"
        expected = end - start + 1
        existing = part_path.stat().st_size if part_path.exists() else 0
        if existing > expected:
            part_path.unlink(missing_ok=True)
            existing = 0
        with lock:
            progress["bytes"][slot] = existing
            progress["done"][slot] = existing >= expected
        if existing >= expected:
            return
        attempt = 0
        while attempt < 6:
            attempt += 1
            try:
                cur = start + existing
                headers = {"Range": f"bytes={cur}-{end}", "User-Agent": USER_AGENT}
                with requests.get(url, headers=headers, stream=True, allow_redirects=True, timeout=(30, 120)) as r:
                    r.raise_for_status()
                    if r.status_code != 206:
                        raise RuntimeError(f"Server returned {r.status_code} instead of 206 for ranged request")
                    with open(part_path, "ab") as f:
                        for chunk_bytes in r.iter_content(chunk_size=2 * 1024 * 1024):
                            if not chunk_bytes:
                                continue
                            f.write(chunk_bytes)
                            existing += len(chunk_bytes)
                            with lock:
                                progress["bytes"][slot] = existing
                if existing != expected:
                    raise RuntimeError(f"Range {slot} incomplete ({existing}/{expected} bytes)")
                with lock:
                    progress["done"][slot] = True
                return
            except Exception as exc:
                if attempt >= 6:
                    with lock:
                        progress["failed"] = True
                        progress["errors"].append(f"range {slot}: {exc}")
                    raise
                time.sleep(min(5, attempt))

    print(f"      Parallel HTTP ranges: {workers} connections (no Xet/cache)")
    started = last = time.time()
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futures = [ex.submit(worker, slot, start, end) for slot, start, end in spans]
        while True:
            done_count = sum(1 for f in futures if f.done())
            with lock:
                done_ranges = sum(1 for x in progress["done"] if x)
                active_ranges = max(0, workers - done_ranges)
                downloaded = sum(progress["bytes"])
                failed = progress["failed"]
            now = time.time()
            if now - last >= 1.0 or done_count == workers:
                speed = downloaded / max(0.001, now - started)
                print(
                    f"      {gb(downloaded)}/{gb(total)} ({downloaded * 100 / total:5.1f}%) | {mibps(speed)} | "
                    f"ranges complete {done_ranges}/{workers} | active {active_ranges}",
                    flush=True,
                )
                last = now
            if done_count == workers:
                break
            if failed:
                wait(futures, return_when=FIRST_EXCEPTION)
                break
            time.sleep(0.2)
        for f in futures:
            f.result()

    part = out.with_suffix(out.suffix + ".part")
    with open(part, "wb") as merged:
        for slot, _start, _end in spans:
            p = part_dir / f"part_{slot:02d}.bin"
            with open(p, "rb") as src:
                shutil.copyfileobj(src, merged, length=8 * 1024 * 1024)
    if part.stat().st_size != total:
        raise RuntimeError(f"Merged download size mismatch for {out.name}: {part.stat().st_size} vs {total}")
    os.replace(part, out)
    shutil.rmtree(part_dir, ignore_errors=True)
    return out


def download(remote_path: str, dest_dir: Path, local_name: str | None = None, repo: str = REPO) -> Path:
    local_name = local_name or Path(remote_path).name
    out = dest_dir / local_name
    url = f"{HF}/{repo}/resolve/main/{quote(remote_path, safe='/')}?download=true"

    total = remote_size(remote_path, repo=repo)
    if out.exists() and out.stat().st_size > 1024 * 1024:
        if total and out.stat().st_size == total:
            print(f"[SKIP] {local_name} already complete ({gb(total)})")
            return out
        if total is None:
            print(f"[SKIP] {local_name} already present ({gb(out.stat().st_size)}); runtime validation will inspect it")
            return out

    print(f"[GET ] {repo}/{remote_path}\n      -> {out}")
    workers = env_int("MINIMAX_H3_DOWNLOAD_WORKERS", 8, 2, 16)
    min_parallel_size = 512 * 1024 * 1024
    if total is None:
        total, supports_ranges = _probe_download(url)
    else:
        _, supports_ranges = _probe_download(url)

    try:
        if total and supports_ranges and total >= min_parallel_size:
            _parallel_download(url, out, total, workers)
        else:
            reason = []
            if not total:
                reason.append("unknown size")
            if total and total < min_parallel_size:
                reason.append(f"file below {gb(min_parallel_size)} threshold")
            if not supports_ranges:
                reason.append("server did not confirm byte ranges")
            why = "; ".join(reason) if reason else "standard path"
            print(f"      Falling back to single stream ({why})")
            _single_stream_download(url, out, known_total=total)
    finally:
        # Clean stale range temp dir from interrupted prior runs after a successful fallback.
        if out.exists():
            shutil.rmtree(out.with_suffix(out.suffix + ".parts"), ignore_errors=True)

    print(f"[DONE] {local_name} ({gb(out.stat().st_size)})")
    return out


def live_hybrid_files() -> list[dict]:
    """Return all root-level hybrid .safetensors checkpoints from the dedicated W4A8 repo."""
    try:
        r = S.get(f"{HF}/api/models/{HYBRID_REPO}/tree/main", params={"recursive": "false", "expand": "false"}, timeout=30)
        r.raise_for_status()
        items = []
        for x in r.json():
            path = x.get("path") or ""
            if x.get("type") == "file" and path.lower().endswith(".safetensors"):
                items.append({"path": path, "size": x.get("size")})
        if items:
            return sorted(items, key=lambda x: x["path"].lower())
    except Exception:
        pass

    try:
        return [
            {"path": p, "size": None}
            for p in sorted(repo_files(HYBRID_REPO))
            if "/" not in p and p.lower().endswith(".safetensors")
        ]
    except Exception as exc:
        print(f"WARNING: Could not read hybrid model repository: {exc}")
        return []


def diffusion_model_choices(files: set[str]) -> list[dict]:
    """Build one model-selection list from the normal W4A8 repo plus the dedicated hybrid W4A8 repo."""
    choices = []
    if REMOTE_FL2VA in files:
        choices.append({
            "repo": REPO,
            "path": REMOTE_FL2VA,
            "label": "FL2VA",
            "note": "Text-to-video and first/last-frame image-to-video",
            "kind": "fl2va",
        })
    if REMOTE_REF2VA in files:
        choices.append({
            "repo": REPO,
            "path": REMOTE_REF2VA,
            "label": "Ref2VA",
            "note": "Omni-reference generation using reference images/videos/audio",
            "kind": "ref2va",
        })

    for item in live_hybrid_files():
        name = Path(item["path"]).name
        size = f" - {gb(item.get('size'))}" if item.get("size") else ""
        choices.append({
            "repo": HYBRID_REPO,
            "path": item["path"],
            "label": f"Hybrid W4A8 - {name}{size}",
            "note": "Combined FL2VA/Ref2VA hybrid checkpoint; select it in the GUI with Use hybrid model",
            "kind": "hybrid",
        })
    return choices


def choose_diffusion_models(files: set[str]) -> list[dict]:
    """Return selected diffusion entries. An empty list means skip diffusion checkpoints only."""
    choices = diffusion_model_choices(files)
    if not choices:
        raise RuntimeError("No supported MiniMax H3 diffusion .safetensors files were found.")

    print("Choose which MiniMax H3 diffusion model(s) to download:\n")
    for i, item in enumerate(choices, 1):
        print(f"  {i}. {item['label']}")
        print(f"     {item['note']}")
    print("\n  A. All listed diffusion models")
    print("  S. Skip diffusion models - still install/verify text encoder + VAEs and still offer LoRAs")
    print(f"\nOriginal models: {HF}/{REPO}")
    print(f"Hybrid W4A8 models: {HF}/{HYBRID_REPO}")
    print("Hybrid entries are discovered live, so newly published .safetensors checkpoints appear automatically.\n")

    while True:
        ans = input("Selection [numbers separated by commas / A / S, default 1]: ").strip() or "1"
        low = ans.lower()
        if low == "s":
            return []
        if low == "a":
            return choices

        selected = []
        seen = set()
        valid = True
        for piece in ans.replace(";", ",").split(","):
            piece = piece.strip()
            if not piece:
                continue
            try:
                idx = int(piece)
            except ValueError:
                valid = False
                break
            if not 1 <= idx <= len(choices):
                valid = False
                break
            item = choices[idx - 1]
            key = (item["repo"], item["path"])
            if key not in seen:
                seen.add(key)
                selected.append(item)
        if valid and selected:
            return selected
        print(f"Please enter one or more numbers from 1 to {len(choices)}, A, or S.")


def lora_note(name: str) -> str:
    low = name.lower()
    if "_ema" in low or "ema_" in low:
        return "EMA - averaged weights; generally the recommended/stabler inference checkpoint"
    return "non-EMA - raw training checkpoint; mainly useful for comparison/testing"


def choose_loras() -> list[dict]:
    print("\nChecking the repository's loras folder live...")
    loras = live_lora_files()
    if not loras:
        print("No downloadable .safetensors LoRAs are currently present in loras/.")
        return []

    print(f"Found {len(loras)} LoRA file(s) currently on Hugging Face:\n")
    for i, item in enumerate(loras, 1):
        name = Path(item["path"]).name
        size = f" - {gb(item.get('size'))}" if item.get("size") else ""
        print(f"  {i}. {name}{size}")
        print(f"     {lora_note(name)}")

    print("\nEMA means Exponential Moving Average weights. For the current Turbo LoRA release, EMA is the recommended choice.")
    print("The non-EMA file is retained primarily as a comparison/raw-training checkpoint.")
    print("Because this list is read live from the repository, future LoRAs added to loras/ will appear here automatically.\n")

    ans = input("Download LoRA(s)? Enter numbers separated by commas, A for all, or press Enter for none: ").strip()
    if not ans:
        return []
    if ans.lower() == "a":
        return loras

    chosen = []
    seen = set()
    for piece in ans.replace(";", ",").split(","):
        piece = piece.strip()
        if not piece:
            continue
        try:
            idx = int(piece)
        except ValueError:
            print(f"Ignoring invalid selection: {piece}")
            continue
        if not 1 <= idx <= len(loras):
            print(f"Ignoring out-of-range selection: {idx}")
            continue
        if idx not in seen:
            seen.add(idx)
            chosen.append(loras[idx - 1])
    return chosen


def main() -> int:
    print("=" * 68)
    print("MiniMax-H3 INT4 W4A8 ConvRot model downloader v3.2")
    print(f"Source: {HF}/{REPO}")
    print("=" * 68)
    print("Downloads selected files only - never a full repository snapshot.\n")

    files = repo_files()
    selected_diffusion = choose_diffusion_models(files)

    required_common = [REMOTE_TE, REMOTE_VVAE, REMOTE_AVAE]
    missing = [p for p in required_common if p not in files]
    if missing:
        raise RuntimeError("Required common file(s) missing from repository: " + ", ".join(missing))

    chosen_loras = choose_loras()

    print("\nDownload plan:")
    if selected_diffusion:
        for item in selected_diffusion:
            name = Path(item["path"]).name
            if item["kind"] == "fl2va":
                print("  - FL2VA diffusion model")
            elif item["kind"] == "ref2va":
                print("  - Ref2VA diffusion model")
            else:
                print(f"  - Hybrid W4A8 diffusion model: {name}")
    else:
        print("  - Diffusion models: skipped by user")
    print("  - W4A8 ConvRot text encoder")
    print("  - Mixed FP16/FP32 video VAE")
    print("  - FP32 audio VAE")
    if chosen_loras:
        for item in chosen_loras:
            print(f"  - LoRA: {Path(item['path']).name}")
    else:
        print("  - LoRAs: none")
    print()

    for item in selected_diffusion:
        remote_path = item["path"]
        repo = item["repo"]
        if item["kind"] == "fl2va":
            download(remote_path, DIFF, LOCAL_FL2VA, repo=repo)
        elif item["kind"] == "ref2va":
            download(remote_path, DIFF, LOCAL_REF2VA, repo=repo)
        else:
            download(remote_path, DIFF, Path(remote_path).name, repo=repo)
    download(REMOTE_TE, TE, LOCAL_TE)
    download(REMOTE_VVAE, VVAE, LOCAL_VVAE)
    download(REMOTE_AVAE, AVAE, LOCAL_AVAE)
    for item in chosen_loras:
        download(item["path"], LORAS, Path(item["path"]).name)

    print("\nSelected MiniMax-H3 components are present.")
    print("The downloader can be run again later to add the other model or newly published LoRAs.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
