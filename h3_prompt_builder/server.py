#!/usr/bin/env python3
"""Local server and Ollama bridge for the Hailuo H3 Prompt Builder."""
from __future__ import annotations

import argparse
import http.client
import ipaddress
import json
import os
import re
import signal
import shutil
import socket
import subprocess
import time
import sys
import threading
import urllib.error
import urllib.parse
import urllib.request
import zipfile
import tempfile
from datetime import datetime, timezone
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from uuid import uuid4

APP_ROOT = Path(__file__).resolve().parent
WEB_ROOT = APP_ROOT / "public" / "local"
RUNTIME_DIR = APP_ROOT / ".runtime"
PID_FILE = RUNTIME_DIR / "hailuo-h3-server.pid"
PRESETS_DIR = APP_ROOT.parent / "presets" / "setsave"
LLM_PREFS_FILE = PRESETS_DIR / "minimax_h3_prompt_builder_llm.json"
DEFAULT_PORT = 8785
APP_VERSION = "2.9.1-beta.2"
LLAMA_BUNDLE_DIR = APP_ROOT.parent / "presets" / "bin" / "llama"
LLAMA_RELEASE_API = "https://api.github.com/repos/ggml-org/llama.cpp/releases/latest"
LLAMA_DOWNLOAD_LOCK = threading.Lock()
LLAMA_DOWNLOAD_STATE = {"active": False, "stage": "idle", "message": "", "percent": 0.0, "downloaded": 0, "total": 0, "error": "", "runner": ""}
HISTORY_LOCK = threading.Lock()
HISTORY_LIMIT = 100
MODEL_LOADS_LOCK = threading.Lock()
ACTIVE_MODEL_LOADS: dict[str, http.client.HTTPConnection] = {}
CANCELLED_MODEL_LOADS: set[str] = set()


GGUF_LOCK = threading.Lock()
GGUF_PROCESS: subprocess.Popen | None = None
GGUF_PORT: int = 0
GGUF_MODEL: str = ""
GGUF_RUNNER: str = ""
GGUF_CTX: int = 16384


def _load_llm_preferences() -> dict:
    try:
        if LLM_PREFS_FILE.is_file():
            data = json.loads(LLM_PREFS_FILE.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
    except Exception:
        pass
    return {}


def _save_llm_preferences(data: dict) -> dict:
    allowed = {
        "backend", "ollama_url", "ollama_model", "gguf_folder", "gguf_model",
        "llama_runner", "gguf_context", "timeout"
    }
    current = _load_llm_preferences()
    for key, value in (data or {}).items():
        if key in allowed:
            current[key] = value
    PRESETS_DIR.mkdir(parents=True, exist_ok=True)
    tmp = LLM_PREFS_FILE.with_suffix(LLM_PREFS_FILE.suffix + ".tmp")
    tmp.write_text(json.dumps(current, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, LLM_PREFS_FILE)
    return current



def _find_bundled_llama_runner() -> str:
    try:
        if not LLAMA_BUNDLE_DIR.is_dir():
            return ""
        direct = LLAMA_BUNDLE_DIR / "llama-server.exe"
        if direct.is_file():
            return str(direct.resolve())
        for p in LLAMA_BUNDLE_DIR.rglob("llama-server.exe"):
            if p.is_file():
                return str(p.resolve())
    except Exception:
        pass
    return ""


def _llama_bundle_status() -> dict:
    runner = _find_bundled_llama_runner()
    with LLAMA_DOWNLOAD_LOCK:
        state = dict(LLAMA_DOWNLOAD_STATE)
    if runner:
        state.update({"installed": True, "runner": runner})
    else:
        state.update({"installed": False, "runner": state.get("runner", "") if state.get("active") else ""})
    state["folder"] = str(LLAMA_BUNDLE_DIR)
    return state


def _set_llama_download_state(**updates) -> None:
    with LLAMA_DOWNLOAD_LOCK:
        LLAMA_DOWNLOAD_STATE.update(updates)


def _github_latest_llama_cuda12_assets() -> tuple[list[dict], str]:
    req = urllib.request.Request(
        LLAMA_RELEASE_API,
        headers={"User-Agent": "MiniMax-H3-Prompt-Builder", "Accept": "application/vnd.github+json"},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        release = json.loads(resp.read().decode("utf-8", errors="replace") or "{}")
    tag = str(release.get("tag_name") or "latest")
    assets = release.get("assets") or []
    main = None
    cudart = None
    for a in assets:
        name = str(a.get("name") or "")
        low = name.lower()
        if re.fullmatch(r"llama-.*-bin-win-cuda-12\.4-x64\.zip", low):
            main = a
        elif low == "cudart-llama-bin-win-cuda-12.4-x64.zip":
            cudart = a
    if not main:
        raise RuntimeError("Latest llama.cpp release does not contain the Windows x64 CUDA 12.4 bundle")
    selected = [main]
    if cudart:
        selected.append(cudart)
    return selected, tag


def _download_file_with_progress(url: str, dest: Path, base_done: int, grand_total: int, label: str) -> int:
    req = urllib.request.Request(url, headers={"User-Agent": "MiniMax-H3-Prompt-Builder"})
    with urllib.request.urlopen(req, timeout=60) as resp, open(dest, "wb") as fh:
        local_total = int(resp.headers.get("Content-Length") or 0)
        done = 0
        while True:
            chunk = resp.read(1024 * 1024)
            if not chunk:
                break
            fh.write(chunk)
            done += len(chunk)
            overall = base_done + done
            pct = (overall / grand_total * 100.0) if grand_total > 0 else 0.0
            _set_llama_download_state(stage="downloading", message=f"Downloading {label}", percent=min(99.0, pct), downloaded=overall, total=grand_total)
    return done


def _safe_extract_zip(zpath: Path, dest: Path) -> None:
    with zipfile.ZipFile(zpath, "r") as zf:
        root = dest.resolve()
        for member in zf.infolist():
            target = (dest / member.filename).resolve()
            if root not in target.parents and target != root:
                raise RuntimeError(f"Unsafe path in archive: {member.filename}")
        zf.extractall(dest)


def _llama_bundle_download_worker() -> None:
    tmp_dir = None
    try:
        _set_llama_download_state(active=True, stage="checking", message="Checking latest llama.cpp Windows CUDA bundle…", percent=0.0, downloaded=0, total=0, error="", runner="")
        assets, tag = _github_latest_llama_cuda12_assets()
        grand_total = sum(int(a.get("size") or 0) for a in assets)
        LLAMA_BUNDLE_DIR.mkdir(parents=True, exist_ok=True)
        tmp_dir = Path(tempfile.mkdtemp(prefix="llama_bundle_", dir=str(LLAMA_BUNDLE_DIR.parent)))
        done_total = 0
        archives = []
        for index, asset in enumerate(assets, 1):
            name = str(asset.get("name") or f"bundle_{index}.zip")
            url = str(asset.get("browser_download_url") or "")
            if not url:
                raise RuntimeError(f"Missing download URL for {name}")
            archive = tmp_dir / name
            got = _download_file_with_progress(url, archive, done_total, grand_total, name)
            done_total += got
            archives.append(archive)
        _set_llama_download_state(stage="extracting", message=f"Extracting llama.cpp {tag}…", percent=99.0, downloaded=done_total, total=grand_total)
        # Remove old bundle contents only after the complete download succeeds.
        if LLAMA_BUNDLE_DIR.exists():
            for child in LLAMA_BUNDLE_DIR.iterdir():
                if child.resolve() == tmp_dir.resolve():
                    continue
                if child.is_dir():
                    shutil.rmtree(child, ignore_errors=True)
                else:
                    try: child.unlink()
                    except Exception: pass
        for archive in archives:
            _safe_extract_zip(archive, LLAMA_BUNDLE_DIR)
        runner = _find_bundled_llama_runner()
        if not runner:
            raise RuntimeError("Download completed, but llama-server.exe was not found in presets\\bin\\llama")
        try:
            subprocess.run([runner, "--version"], cwd=os.path.dirname(runner), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=20, check=False, creationflags=(subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0))
        except Exception:
            pass
        _save_llm_preferences({"llama_runner": runner})
        _set_llama_download_state(active=False, stage="done", message="llama.cpp server bundle is ready", percent=100.0, downloaded=done_total, total=grand_total, error="", runner=runner)
    except Exception as exc:
        _set_llama_download_state(active=False, stage="failed", message="llama.cpp bundle download failed", error=str(exc), percent=0.0)
    finally:
        if tmp_dir is not None:
            shutil.rmtree(tmp_dir, ignore_errors=True)


def _start_llama_bundle_download() -> dict:
    runner = _find_bundled_llama_runner()
    if runner:
        _save_llm_preferences({"llama_runner": runner})
        return {"ok": True, "already_installed": True, "runner": runner, "status": _llama_bundle_status()}
    with LLAMA_DOWNLOAD_LOCK:
        if LLAMA_DOWNLOAD_STATE.get("active"):
            return {"ok": True, "already_running": True, "status": dict(LLAMA_DOWNLOAD_STATE)}
        LLAMA_DOWNLOAD_STATE.update({"active": True, "stage": "starting", "message": "Starting llama.cpp bundle download…", "percent": 0.0, "downloaded": 0, "total": 0, "error": "", "runner": ""})
    threading.Thread(target=_llama_bundle_download_worker, daemon=True, name="llama-bundle-download").start()
    return {"ok": True, "started": True, "status": _llama_bundle_status()}


def _pick_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _gguf_base_url() -> str:
    return f"http://127.0.0.1:{GGUF_PORT}" if GGUF_PORT else ""


def _gguf_alive() -> bool:
    proc = GGUF_PROCESS
    return bool(proc is not None and proc.poll() is None and GGUF_PORT)


def _gguf_health(timeout: float = 2.0) -> tuple[bool, dict]:
    if not _gguf_alive():
        return False, {}
    try:
        with urllib.request.urlopen(f"{_gguf_base_url()}/health", timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            payload = json.loads(raw) if raw.strip() else {}
            return int(resp.getcode()) == 200, payload
    except urllib.error.HTTPError as exc:
        try:
            payload = json.loads(exc.read().decode("utf-8", errors="replace") or "{}")
        except Exception:
            payload = {}
        return False, payload
    except Exception:
        return False, {}


def _stop_gguf_server() -> None:
    global GGUF_PROCESS, GGUF_PORT, GGUF_MODEL, GGUF_RUNNER
    with GGUF_LOCK:
        proc = GGUF_PROCESS
        GGUF_PROCESS = None
        GGUF_PORT = 0
        GGUF_MODEL = ""
        GGUF_RUNNER = ""
    if proc is not None:
        try:
            proc.terminate()
            proc.wait(timeout=5)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass


def _browse_local_folder(initial: str = "") -> str:
    try:
        import tkinter as tk
        from tkinter import filedialog
        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        chosen = filedialog.askdirectory(initialdir=initial or os.getcwd(), title="Select GGUF model folder")
        root.destroy()
        return str(chosen or "")
    except Exception as exc:
        raise RuntimeError(f"Could not open folder picker: {exc}")


def _browse_local_file(initial: str = "") -> str:
    try:
        import tkinter as tk
        from tkinter import filedialog
        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        initial_dir = initial if os.path.isdir(initial) else os.path.dirname(initial) if initial else os.getcwd()
        chosen = filedialog.askopenfilename(initialdir=initial_dir, title="Select llama-server.exe", filetypes=[("llama-server", "llama-server.exe"), ("Executable", "*.exe"), ("All files", "*.*")])
        root.destroy()
        return str(chosen or "")
    except Exception as exc:
        raise RuntimeError(f"Could not open file picker: {exc}")


def _scan_gguf_folder(folder: str) -> list[dict]:
    folder = os.path.abspath(str(folder or "").strip().strip('"'))
    if not folder or not os.path.isdir(folder):
        raise ValueError("Select an existing GGUF folder first")
    items = []
    for root, dirs, files in os.walk(folder):
        dirs[:] = [d for d in dirs if not d.startswith('.')]
        for name in files:
            low = name.lower()
            if not low.endswith('.gguf') or 'mmproj' in low:
                continue
            path = os.path.join(root, name)
            try:
                size = os.path.getsize(path)
            except OSError:
                size = 0
            items.append({"name": name, "path": path, "size": size, "relative": os.path.relpath(path, folder)})
    items.sort(key=lambda x: x["relative"].lower())
    return items


def _start_gguf_server(runner: str, model: str, ctx: int = 16384, timeout: int = 240) -> dict:
    global GGUF_PROCESS, GGUF_PORT, GGUF_MODEL, GGUF_RUNNER, GGUF_CTX
    runner = os.path.abspath(str(runner or "").strip().strip('"'))
    model = os.path.abspath(str(model or "").strip().strip('"'))
    if not os.path.isfile(runner):
        raise ValueError(f"llama-server runner not found: {runner}")
    if not os.path.isfile(model) or not model.lower().endswith('.gguf'):
        raise ValueError(f"GGUF model not found: {model}")
    ctx = max(4096, min(131072, int(ctx or 16384)))
    _stop_gguf_server()
    port = _pick_free_port()
    args = [runner, "-m", model, "--host", "127.0.0.1", "--port", str(port), "-c", str(ctx), "--reasoning-budget", "0"]
    flags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
    proc = subprocess.Popen(args, cwd=os.path.dirname(runner) or None, stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT, creationflags=flags)
    with GGUF_LOCK:
        GGUF_PROCESS = proc
        GGUF_PORT = port
        GGUF_MODEL = model
        GGUF_RUNNER = runner
        GGUF_CTX = ctx
    started = time.time()
    last = {}
    while time.time() - started < max(15, min(300, int(timeout or 240))):
        if proc.poll() is not None:
            _stop_gguf_server()
            raise RuntimeError("llama-server stopped while loading the GGUF model")
        ready, payload = _gguf_health(timeout=2.0)
        last = payload or last
        if ready:
            return {"ok": True, "model": model, "runner": runner, "port": port, "base_url": _gguf_base_url(), "ctx": ctx, "load_seconds": round(time.time()-started, 2)}
        time.sleep(0.8)
    _stop_gguf_server()
    detail = last.get("error") if isinstance(last, dict) else None
    raise TimeoutError(detail or "Timed out while waiting for llama-server to load the GGUF model")


def _gguf_chat_request(prompt: str, temperature: float, max_tokens: int, timeout: int) -> dict:
    if not _gguf_alive():
        raise RuntimeError("No local GGUF model is loaded")
    ready, _ = _gguf_health(timeout=2.0)
    if not ready:
        raise RuntimeError("The local GGUF model is still loading")
    payload = {
        "model": "local-gguf",
        "messages": [{"role": "user", "content": str(prompt or "")}],
        "temperature": float(temperature),
        "max_tokens": int(max_tokens),
        "reasoning_format": "none",
        "stream": False,
    }
    req = urllib.request.Request(f"{_gguf_base_url()}/v1/chat/completions", data=json.dumps(payload).encode("utf-8"), headers={"Content-Type":"application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=max(15, min(600, int(timeout or 120)))) as resp:
        raw = json.loads(resp.read().decode("utf-8", errors="replace") or "{}")
    choices = raw.get("choices") or []
    content = ""
    reasoning = ""
    if choices:
        message = choices[0].get("message") or {}
        content = str(message.get("content") or "")
        reasoning = str(message.get("reasoning_content") or message.get("reasoning") or "")
    usage = raw.get("usage") if isinstance(raw.get("usage"), dict) else {}
    return {"response": content, "thinking": reasoning, "done": True, "usage": usage}


def prompt_history_path() -> Path:
    """Use a version-independent Windows location so upgrades retain history."""
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
        if base:
            return Path(base) / "BobDoyleMedia" / "HailuoH3PromptBuilder" / "prompt-history.json"
    return RUNTIME_DIR / "prompt-history.json"


def read_prompt_history() -> list[dict]:
    path = prompt_history_path()
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except (OSError, ValueError):
        return []


def write_prompt_history(items: list[dict]) -> None:
    path = prompt_history_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(items[:HISTORY_LIMIT], ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def local_network_urls(port: int) -> list[str]:
    """Return private-LAN addresses that another device can use."""
    addresses: set[str] = set()
    try:
        for result in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            addresses.add(result[4][0])
    except OSError:
        pass
    try:
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        probe.connect(("8.8.8.8", 80))
        addresses.add(probe.getsockname()[0])
        probe.close()
    except OSError:
        pass
    usable = sorted(address for address in addresses if ipaddress.ip_address(address).is_private and not ipaddress.ip_address(address).is_loopback)
    return [f"http://{address}:{port}" for address in usable]


def server_bind_host(phone_access: bool) -> str:
    """Bind locally by default; expose to the private LAN only by explicit opt-in."""
    return "0.0.0.0" if phone_access else "127.0.0.1"


def normalize_ollama_url(value: str) -> str:
    value = (value or "http://localhost:11434").strip().rstrip("/")
    parsed = urllib.parse.urlparse(value)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise ValueError("Ollama URL must be a valid http:// or https:// address")
    if parsed.hostname not in ("localhost", "127.0.0.1", "::1"):
        raise ValueError("For safety, this local app connects only to Ollama on this computer")
    return value


class H3Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


class Handler(SimpleHTTPRequestHandler):
    server_version = "Hailuo-H3-Prompt-Builder/2.8"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(WEB_ROOT), **kwargs)

    def log_message(self, fmt, *args):
        print(f"[{self.log_date_time_string()}] {fmt % args}")

    def end_headers(self):
        self.send_header("Cache-Control", "no-store, max-age=0")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        super().end_headers()

    def json_response(self, status: int, payload: dict):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def read_json(self, limit=1_000_000):
        length = int(self.headers.get("Content-Length", "0"))
        if length <= 0 or length > limit:
            raise ValueError("Invalid request size")
        return json.loads(self.rfile.read(length).decode("utf-8"))

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/api/settings/llm":
            settings = _load_llm_preferences()
            bundled = _find_bundled_llama_runner()
            if bundled:
                settings["llama_runner"] = bundled
                _save_llm_preferences({"llama_runner": bundled})
            return self.json_response(200, {"ok": True, "settings": settings, "path": str(LLM_PREFS_FILE)})
        if parsed.path == "/api/status":
            port = self.server.server_address[1]
            phone_access = bool(getattr(self.server, "phone_access", False))
            return self.json_response(200, {
                "ok": True,
                "app": "hailuo-h3-prompt-builder",
                "version": APP_VERSION,
                "pid": os.getpid(),
                "phone_access": phone_access,
                "network_urls": local_network_urls(port) if phone_access else [],
            })
        if parsed.path == "/api/gguf/bundle/status":
            return self.json_response(200, {"ok": True, **_llama_bundle_status()})
        if parsed.path == "/api/gguf/status":
            ready, payload = _gguf_health(timeout=1.0)
            return self.json_response(200, {
                "ok": True,
                "running": _gguf_alive(),
                "ready": ready,
                "model": GGUF_MODEL,
                "runner": GGUF_RUNNER,
                "ctx": GGUF_CTX,
                "port": GGUF_PORT,
                "base_url": _gguf_base_url(),
                "health": payload,
            })
        if parsed.path == "/api/history":
            with HISTORY_LOCK:
                return self.json_response(200, {"items": read_prompt_history()})
        if parsed.path == "/api/ollama/tags":
            try:
                query = urllib.parse.parse_qs(parsed.query)
                base = normalize_ollama_url(query.get("url", [""])[0])
                with urllib.request.urlopen(f"{base}/api/tags", timeout=3) as response:
                    return self.json_response(200, json.loads(response.read().decode("utf-8")))
            except Exception as exc:
                return self.json_response(502, {"error": friendly_error(exc)})
        if parsed.path == "/api/ollama/ps":
            try:
                query = urllib.parse.parse_qs(parsed.query)
                base = normalize_ollama_url(query.get("url", [""])[0])
                with urllib.request.urlopen(f"{base}/api/ps", timeout=3) as response:
                    return self.json_response(200, json.loads(response.read().decode("utf-8")))
            except Exception as exc:
                return self.json_response(502, {"error": friendly_error(exc)})
        if parsed.path == "/":
            self.path = "/index.html"
        return super().do_GET()

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/api/settings/llm":
            try:
                body = self.read_json()
                saved = _save_llm_preferences(body)
                return self.json_response(200, {"ok": True, "settings": saved, "path": str(LLM_PREFS_FILE)})
            except Exception as exc:
                return self.json_response(400, {"error": str(exc)})
        if parsed.path == "/api/shutdown":
            self.json_response(200, {"ok": True, "message": "Server stopping"})
            threading.Thread(target=self.server.shutdown, daemon=True).start()
            return
        if parsed.path == "/api/gguf/browse-folder":
            try:
                body = self.read_json()
                chosen = _browse_local_folder(str(body.get("initial", "")))
                if chosen:
                    _save_llm_preferences({"gguf_folder": chosen})
                return self.json_response(200, {"path": chosen})
            except Exception as exc:
                return self.json_response(400, {"error": str(exc)})
        if parsed.path == "/api/gguf/bundle/download":
            try:
                return self.json_response(200, _start_llama_bundle_download())
            except Exception as exc:
                return self.json_response(500, {"error": str(exc)})
        if parsed.path == "/api/gguf/browse-runner":
            try:
                body = self.read_json()
                chosen = _browse_local_file(str(body.get("initial", "")))
                if chosen:
                    _save_llm_preferences({"llama_runner": chosen})
                return self.json_response(200, {"path": chosen})
            except Exception as exc:
                return self.json_response(400, {"error": str(exc)})
        if parsed.path == "/api/gguf/scan":
            try:
                body = self.read_json()
                folder = str(body.get("folder", ""))
                return self.json_response(200, {"folder": os.path.abspath(folder), "models": _scan_gguf_folder(folder)})
            except Exception as exc:
                return self.json_response(400, {"error": str(exc)})
        if parsed.path == "/api/gguf/load":
            try:
                body = self.read_json()
                result = _start_gguf_server(str(body.get("runner", "")), str(body.get("model", "")), int(body.get("ctx", 16384)), int(body.get("timeout", 240)))
                return self.json_response(200, result)
            except Exception as exc:
                return self.json_response(500, {"error": str(exc)})
        if parsed.path == "/api/gguf/unload":
            _stop_gguf_server()
            return self.json_response(200, {"ok": True})
        if parsed.path == "/api/gguf/generate":
            try:
                body = self.read_json()
                result = _gguf_chat_request(str(body.get("prompt", "")), float(body.get("temperature", 0.8)), int(body.get("num_predict", 7000)), int(body.get("timeout", 120)))
                return self.json_response(200, result)
            except Exception as exc:
                return self.json_response(500, {"error": str(exc)})
        if parsed.path == "/api/gguf/generate-stream":
            try:
                body = self.read_json()
                if not _gguf_alive():
                    raise RuntimeError("No local GGUF model is loaded")
                ready, _ = _gguf_health(timeout=2.0)
                if not ready:
                    raise RuntimeError("The local GGUF model is still loading")
                payload = {
                    "model": "local-gguf",
                    "messages": [{"role":"user","content":str(body.get("prompt", ""))}],
                    "temperature": float(body.get("temperature", 0.8)),
                    "max_tokens": int(body.get("num_predict", 7000)),
                    "reasoning_format": "none",
                    "stream": True,
                    "stream_options": {"include_usage": True},
                }
                req = urllib.request.Request(f"{_gguf_base_url()}/v1/chat/completions", data=json.dumps(payload).encode("utf-8"), headers={"Content-Type":"application/json", "Accept":"text/event-stream"}, method="POST")
                upstream = urllib.request.urlopen(req, timeout=max(15, min(600, int(body.get("timeout", 120)))))
                self.send_response(200)
                self.send_header("Content-Type", "application/x-ndjson; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                usage = {}
                for raw_line in upstream:
                    line = raw_line.decode("utf-8", errors="replace").strip()
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        break
                    try:
                        item = json.loads(data)
                    except Exception:
                        continue
                    if isinstance(item.get("usage"), dict):
                        usage = item.get("usage") or usage
                    choices = item.get("choices") or []
                    text = ""
                    thinking = ""
                    if choices:
                        delta = choices[0].get("delta") or {}
                        text = str(delta.get("content") or "")
                        thinking = str(delta.get("reasoning_content") or delta.get("reasoning") or "")
                    if thinking:
                        self.wfile.write((json.dumps({"thinking": thinking, "done": False})+"\n").encode("utf-8")); self.wfile.flush()
                    if text:
                        self.wfile.write((json.dumps({"response": text, "done": False})+"\n").encode("utf-8")); self.wfile.flush()
                self.wfile.write((json.dumps({"done": True, "usage": usage})+"\n").encode("utf-8")); self.wfile.flush()
                upstream.close()
            except Exception as exc:
                try:
                    self.send_response(500); self.send_header("Content-Type","application/json"); self.end_headers(); self.wfile.write(json.dumps({"error":str(exc)}).encode("utf-8"))
                except Exception:
                    pass
            return
        if parsed.path == "/api/history":
            try:
                body = self.read_json()
                action = body.get("action")
                with HISTORY_LOCK:
                    items = read_prompt_history()
                    if action == "add":
                        values = body.get("values")
                        output = str(body.get("output", "")).strip()[:12000]
                        if not isinstance(values, dict) or not str(values.get("idea", "")).strip() or not output:
                            raise ValueError("A complete prompt and its settings are required")
                        idea = str(values.get("idea", "")).strip()
                        entry = {"id": uuid4().hex, "created_at": datetime.now(timezone.utc).isoformat(), "title": idea[:90], "values": values, "output": output}
                        items.insert(0, entry)
                        write_prompt_history(items)
                        return self.json_response(200, {"item": entry, "count": len(items[:HISTORY_LIMIT])})
                    if action == "delete":
                        entry_id = str(body.get("id", ""))
                        filtered = [item for item in items if str(item.get("id")) != entry_id]
                        write_prompt_history(filtered)
                        return self.json_response(200, {"ok": True, "count": len(filtered)})
                    raise ValueError("Unknown history action")
            except Exception as exc:
                return self.json_response(400, {"error": str(exc) or "History could not be updated"})
        if parsed.path == "/api/ollama/model-cancel":
            try:
                body = self.read_json()
                load_id = str(body.get("load_id", "")).strip()
                if not load_id:
                    raise ValueError("A model load identifier is required")
                with MODEL_LOADS_LOCK:
                    CANCELLED_MODEL_LOADS.add(load_id)
                    connection = ACTIVE_MODEL_LOADS.pop(load_id, None)
                if connection is not None:
                    if connection.sock is not None:
                        try:
                            connection.sock.shutdown(socket.SHUT_RDWR)
                        except OSError:
                            pass
                    connection.close()
                return self.json_response(200, {"ok": True, "cancelled": connection is not None, "load_id": load_id})
            except Exception as exc:
                return self.json_response(400, {"error": str(exc) or "The model load could not be cancelled"})
        if parsed.path == "/api/ollama/generate":
            try:
                body = self.read_json()
                base = normalize_ollama_url(body.get("url", ""))
                model = str(body.get("model", "")).strip()
                prompt = str(body.get("prompt", ""))
                timeout = max(15, min(120, int(body.get("timeout", 120))))
                if not model or not prompt:
                    raise ValueError("Model and prompt are required")
                raw_options = body.get("options") if isinstance(body.get("options"), dict) else {}
                options = dict(raw_options)
                options["num_predict"] = max(128, min(700, int(options.get("num_predict", 520))))
                ollama_payload = {
                    "model": model,
                    "prompt": prompt,
                    "stream": False,
                    "options": options,
                    "keep_alive": "15m",
                }
                if body.get("format"):
                    ollama_payload["format"] = body["format"]
                payload = json.dumps(ollama_payload).encode("utf-8")
                request = urllib.request.Request(f"{base}/api/generate", data=payload, headers={"Content-Type": "application/json"}, method="POST")
                with urllib.request.urlopen(request, timeout=timeout) as response:
                    data = json.loads(response.read().decode("utf-8"))
                return self.json_response(200, {
                    "response": data.get("response", ""), "model": model, "done": data.get("done", True),
                    "prompt_eval_count": data.get("prompt_eval_count"), "eval_count": data.get("eval_count"),
                    "total_duration": data.get("total_duration"), "eval_duration": data.get("eval_duration"),
                })
            except Exception as exc:
                return self.json_response(502, {"error": friendly_error(exc)})
        if parsed.path == "/api/ollama/generate-stream":
            response_started = False
            try:
                body = self.read_json()
                base = normalize_ollama_url(body.get("url", ""))
                model = str(body.get("model", "")).strip()
                prompt = str(body.get("prompt", ""))
                timeout = max(15, min(120, int(body.get("timeout", 120))))
                if not model or not prompt:
                    raise ValueError("Model and prompt are required")
                raw_options = body.get("options") if isinstance(body.get("options"), dict) else {}
                options = dict(raw_options)
                options["num_predict"] = max(128, min(600, int(options.get("num_predict", 520))))
                payload = json.dumps({
                    "model": model, "prompt": prompt, "stream": True,
                    "options": options, "keep_alive": "15m",
                }).encode("utf-8")
                request = urllib.request.Request(f"{base}/api/generate", data=payload, headers={"Content-Type": "application/json"}, method="POST")
                with urllib.request.urlopen(request, timeout=timeout) as response:
                    self.send_response(200)
                    self.send_header("Content-Type", "application/x-ndjson; charset=utf-8")
                    self.send_header("Connection", "close")
                    self.end_headers()
                    response_started = True
                    for line in response:
                        if not line.strip():
                            continue
                        self.wfile.write(line if line.endswith(b"\n") else line + b"\n")
                        self.wfile.flush()
                self.close_connection = True
                return
            except (BrokenPipeError, ConnectionResetError):
                self.close_connection = True
                return
            except Exception as exc:
                if not response_started:
                    return self.json_response(502, {"error": friendly_error(exc)})
                try:
                    self.wfile.write((json.dumps({"error": friendly_error(exc), "done": True}) + "\n").encode("utf-8"))
                    self.wfile.flush()
                except OSError:
                    pass
                self.close_connection = True
                return
        if parsed.path == "/api/ollama/model":
            load_id = ""
            connection = None
            try:
                body = self.read_json()
                base = normalize_ollama_url(body.get("url", ""))
                model = str(body.get("model", "")).strip()
                action = str(body.get("action", "load")).strip().lower()
                load_id = str(body.get("load_id", "")).strip()
                if not model or action not in ("load", "unload"):
                    raise ValueError("A model and a valid load action are required")
                payload = json.dumps({
                    "model": model,
                    "stream": False,
                    "keep_alive": "15m" if action == "load" else 0,
                }).encode("utf-8")
                if action == "load":
                    if not load_id:
                        raise ValueError("A cancellable model load identifier is required")
                    target = urllib.parse.urlparse(base)
                    connection_type = http.client.HTTPSConnection if target.scheme == "https" else http.client.HTTPConnection
                    connection = connection_type(target.hostname, target.port, timeout=1800)
                    with MODEL_LOADS_LOCK:
                        if load_id in CANCELLED_MODEL_LOADS:
                            CANCELLED_MODEL_LOADS.discard(load_id)
                            raise RuntimeError("Model loading was cancelled")
                        ACTIVE_MODEL_LOADS[load_id] = connection
                    endpoint = f"{target.path.rstrip('/')}/api/generate" or "/api/generate"
                    connection.request("POST", endpoint, body=payload, headers={"Content-Type": "application/json"})
                    response = connection.getresponse()
                    raw = response.read()
                    if response.status >= 400:
                        raise RuntimeError(f"Ollama returned HTTP {response.status}")
                    data = json.loads(raw.decode("utf-8"))
                else:
                    request = urllib.request.Request(f"{base}/api/generate", data=payload, headers={"Content-Type": "application/json"}, method="POST")
                    with urllib.request.urlopen(request, timeout=30) as response:
                        data = json.loads(response.read().decode("utf-8"))
                return self.json_response(200, {
                    "ok": True, "action": action, "model": model,
                    "load_duration": data.get("load_duration"), "total_duration": data.get("total_duration"),
                })
            except Exception as exc:
                with MODEL_LOADS_LOCK:
                    cancelled = bool(load_id and load_id in CANCELLED_MODEL_LOADS)
                    if cancelled:
                        CANCELLED_MODEL_LOADS.discard(load_id)
                try:
                    return self.json_response(409 if cancelled else 502, {"error": "Model loading was cancelled" if cancelled else friendly_error(exc), "cancelled": cancelled})
                except (BrokenPipeError, ConnectionResetError):
                    return
            finally:
                if load_id:
                    with MODEL_LOADS_LOCK:
                        if ACTIVE_MODEL_LOADS.get(load_id) is connection:
                            ACTIVE_MODEL_LOADS.pop(load_id, None)
                if connection is not None:
                    connection.close()
        return self.json_response(404, {"error": "Unknown endpoint"})


def friendly_error(exc: Exception) -> str:
    if isinstance(exc, urllib.error.HTTPError):
        try:
            detail = json.loads(exc.read().decode("utf-8")).get("error")
        except Exception:
            detail = None
        return detail or f"Ollama returned HTTP {exc.code}"
    if isinstance(exc, urllib.error.URLError):
        return "Could not reach Ollama. Start Ollama and test the connection again"
    if isinstance(exc, TimeoutError):
        return "The Ollama request reached its hard timeout"
    return str(exc) or exc.__class__.__name__


def cleanup(*_):
    _stop_gguf_server()
    try:
        if PID_FILE.exists() and PID_FILE.read_text(encoding="utf-8").strip() == str(os.getpid()):
            PID_FILE.unlink()
    except OSError:
        pass


def main():
    parser = argparse.ArgumentParser(description="Run the Hailuo H3 Prompt Builder locally")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--phone-access", action="store_true", help="Allow devices on the private network to connect")
    args = parser.parse_args()
    if not WEB_ROOT.is_dir():
        raise SystemExit(f"Web files not found: {WEB_ROOT}")
    RUNTIME_DIR.mkdir(exist_ok=True)
    PID_FILE.write_text(str(os.getpid()), encoding="utf-8")
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    try:
        server = H3Server((server_bind_host(args.phone_access), args.port), Handler)
        server.phone_access = args.phone_access
    except OSError as exc:
        cleanup()
        raise SystemExit(f"Could not start on port {args.port}: {exc}")
    print(f"Hailuo H3 Prompt Builder v{APP_VERSION}")
    print(f"Open http://localhost:{args.port}")
    if args.phone_access:
        for network_url in local_network_urls(args.port):
            print(f"Phone access: {network_url}")
    else:
        print("Phone access: off (local computer only)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        cleanup()


if __name__ == "__main__":
    main()
