from __future__ import annotations

import os
import threading
import time
from pathlib import Path
from typing import Callable

from huggingface_hub import snapshot_download

REPO_ID = "MiniMaxAI/MiniMax-Music3"
ALLOW_PATTERNS = [
    "condition_encoder/*",
    "language_model/*",
    "rvq_depth_decoder/*",
    "scheduler/*",
    "tokenizer/*",
    "transformer/*",
    "vocoder/*",
    "modular_model_index.json",
    "config.json",
    "LICENSE",
]


def _folder_stats(path: Path) -> tuple[int, int]:
    files = 0
    total = 0
    try:
        for item in path.rglob("*"):
            if item.is_file():
                try:
                    files += 1
                    total += item.stat().st_size
                except OSError:
                    pass
    except OSError:
        pass
    return files, total


def _format_bytes(value: int) -> str:
    gib = value / (1024 ** 3)
    if gib >= 1.0:
        return f"{gib:.2f} GiB"
    mib = value / (1024 ** 2)
    return f"{mib:.1f} MiB"


def download_model(destination: Path, log: Callable[[str], None] = print) -> Path:
    destination.mkdir(parents=True, exist_ok=True)
    os.environ["HF_XET_HIGH_PERFORMANCE"] = "1"
    os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
    log(f"Repository: {REPO_ID}")
    log(f"Destination: {destination}")
    log("High-performance hf-xet downloads enabled.")
    log("Selective model download enabled; unused repository trees are excluded.")
    before_files, before_bytes = _folder_stats(destination)
    log(f"Existing local data: {before_files} files, {_format_bytes(before_bytes)}")

    stop_event = threading.Event()

    def monitor():
        last_files = -1
        last_bytes = -1
        while not stop_event.wait(3.0):
            files, total = _folder_stats(destination)
            if files != last_files or total != last_bytes:
                delta = max(0, total - before_bytes)
                log(f"Download activity: {files} local files, {_format_bytes(total)} present (+{_format_bytes(delta)} this run)")
                last_files, last_bytes = files, total

    watcher = threading.Thread(target=monitor, name="MiniMaxM3DownloadMonitor", daemon=True)
    watcher.start()
    try:
        log("Connecting to Hugging Face and resolving the required MiniMax Music 3 files...")
        path = snapshot_download(
            repo_id=REPO_ID,
            local_dir=str(destination),
            allow_patterns=ALLOW_PATTERNS,
        )
    finally:
        stop_event.set()
        watcher.join(timeout=1.0)

    after_files, after_bytes = _folder_stats(destination)
    log(f"Model download complete: {after_files} files, {_format_bytes(after_bytes)} present locally.")
    log("Validating required model files...")
    return Path(path)


def validate_model(destination: Path) -> tuple[bool, list[str]]:
    required = [
        destination / "modular_model_index.json",
        destination / "condition_encoder" / "config.json",
        destination / "language_model" / "config.json",
        destination / "language_model" / "model.safetensors.index.json",
        destination / "rvq_depth_decoder" / "config.json",
        destination / "scheduler" / "scheduler_config.json",
        destination / "tokenizer" / "tokenizer.json",
        destination / "transformer" / "config.json",
        destination / "vocoder" / "config.json",
    ]
    missing = [str(p) for p in required if not p.exists()]
    for folder in ("condition_encoder", "rvq_depth_decoder", "transformer", "vocoder"):
        if not list((destination / folder).glob("*.safetensors")):
            missing.append(str(destination / folder / "*.safetensors"))
    if len(list((destination / "language_model").glob("model-*-of-*.safetensors"))) < 4:
        missing.append(str(destination / "language_model" / "model-*-of-*.safetensors (4 shards expected)"))
    return not missing, missing
