# MiniMax H3 Image — Agent operating guide

This guide is for AI agents operating the portable Windows MiniMax H3 Image standalone application, including Codex and other agents with local file/command access. It describes the implementation in `helpers/h3_image_gui.py` and `runtime/h3_image/engine.py`. Use the installed application to generate and edit images; do not modify application code just to complete a creative task. Check the installed source if its version differs from this guide.

## 1. Discover the installation and protect the user environment

- The H3 Image root is the folder containing `start.bat`, `install.bat`, `helpers/h3_image_gui.py` and `runtime/h3_image/engine.py`. Resolve the actual root from the approved project folders; do not assume a drive letter or search unrelated drives.
- If working on a GrizzlyMax music video, read GrizzlyMax's root `AGENTS.md` as well. Its project organization and production rules take precedence for that project. This image guide is stored in `assets/`, not at the root; explicitly read it when using H3 Image. Do not assume agents automatically discover nested instruction files.
- Use the portable Python environment, not system Python. Run CMD and Python commands with the H3 Image root as the working directory. No PowerShell, registry changes, QSettings, system Python installation or extra external services are needed for normal image work.
- Use the existing local ComfyUI backend; this is the application's implemented inference engine, not a requirement to create a separate ComfyUI project or browser workflow. Do not install a second backend, replace checkpoints, download large models, update dependencies or modify source code unless requested. Do not use online image generation as an unapproved substitute.
- Do not inspect unrelated personal files, publish/upload anything, expose credentials or alter existing media. The local backend's loopback API is used for inference; it does not require internet access during ordinary generation once the installation is complete. These are operating instructions, not an enforced security boundary.

## 2. Project output — mandatory

For a GrizzlyMax production, use the existing active project directory directly under GrizzlyMax's default `output/` (for example `<GrizzlyMax>/output/project_5_manga/`). Save every generated reference, image edit, prompt, test, approved candidate and project-specific script/manifest in that same project, preferably in descriptive subfolders such as `references/`, `images/`, `images/tests/` and `prompts/`. Reuse the same project across chats. Do not create a separate image-app output project for the same production.

For standalone image-only work outside GrizzlyMax, use the user-approved destination or a dedicated project folder under the image app's own `output/`. Default GUI destinations are `output/images` and `output/edits`; set the destination to the active project before running a job. Use absolute paths, preserve originals and do not overwrite approved results. Copy approved external references into the project if portability is required. Keep shared models, Python environments, global settings, native engine caches/logs and runtime infrastructure in their normal application locations. The engine may maintain internal input/output copies and framed references; do not delete these while jobs are active.

Record the actual prompt, ordered reference paths, requested dimensions, seed, steps, model filenames, low-VRAM and framing settings, output path, and a short test result or decision in the project manifest. Record useful decisions and observations, not private model reasoning. Do not claim an image was generated until the file exists and can be opened.

## 3. File map and launch

| Path relative to H3 Image root | Purpose |
|---|---|
| `start.bat` | Launch the maximized PySide6 GUI |
| `install.bat` | Portable installation/repair; may download substantial dependencies and weights |
| `helpers/h3_image_gui.py` | GUI, default settings, JSON persistence, generation controls and model discovery |
| `runtime/h3_image/engine.py` | Local backend lifecycle, graph building, reference preparation, request submission, output fetching |
| `presets/setsave/h3_image_settings.json` | Persistent GUI settings, created on use |
| `presets/extra_env/h3_image_install.py` | Backend installer and model download choices |
| `environments/h3_image_gui/Scripts/python.exe` | GUI/runtime client Python (Pillow, PySide6) |
| `environments/h3_image_comfy/Scripts/python.exe` | Backend Python |
| `models/minimax_h3_image/comfyui_backend/main.py` | Default dedicated backend entry point |
| `runtime/h3_image/extra_model_paths.yaml` | Generated checkpoint-directory mapping |
| `logs/h3_image_gui_YYYYMMDD.log` | GUI generation activity |
| `logs/h3_image_backend_*.log` | Backend startup/inference diagnostics |
| `temp/h3_image/prepared_refs/` | Automatically framed temporary reference images |
| `assets/H3_image_agent.md` | This guide |

Default backend URL: `http://127.0.0.1:8197`. Use `start.bat` if the user wants to work in the GUI. If generating programmatically, use the existing runtime functions (section 7), not undocumented CLI flags. `start.bat` does not expose a prompt submission CLI. No GrizzlyMax video batch/queue bridge for H3 Image is implemented in this version.

## 4. Modes, settings and constraints

The Main tab is **Generate / Edit**, with separate **Settings** and **Logs** tabs.

- **Generate**: text-to-image. Enter the prompt, choose size, seed and steps, then Generate image. This mode intentionally passes an empty reference list; reference controls are hidden.
- **Edit**: reference-conditioned generation or editing. Add 1–16 images; reference 1 is the image to edit. Additional references can provide identity, outfit, object, location or style. Use Edit even for a completely new composition based on reference images. The GUI supports PNG, JPG/JPEG, WebP and BMP in its image picker; use valid readable files. Drag thumbnails to change their order; double-click to preview. Clear/remove references if no longer needed. The model receives ordered references as `images.image_1` through `images.image_16`.
- The backend graph is the same in both modes; the difference is reference input and chosen destination. There is no separate mask/inpainting control, dedicated negative-prompt input or explicit image strength slider in this version. Prompt text controls the requested changes.

### Settings JSON

Use the saved settings as the baseline and change only the requested job parameters. `read_settings()` in the GUI merges known keys with defaults. Do not assume any saved model path is valid: check files. The GUI autosaves after changes (400 ms debounce), on generation and on close. Do not edit this JSON behind the back of a running GUI; it may overwrite changes from its in-memory state. If persistent settings must be edited programmatically, close the GUI first, preserve all existing keys, use an atomic JSON update and reopen only if needed. Prefer per-job overrides in the runtime to avoid altering the user's global configuration.

| Key | Default / behavior |
|---|---|
| `mode` | `Generate` or `Edit`; default `Generate` |
| `prompt` | Nonempty natural-language instruction |
| `width`, `height` | 2048 × 2048; agent defaults by aspect ratio are mandatory (see Resolution choice); GUI range 256–8192 each, step 32. Prefer built-in presets; upper UI limit is not a guarantee of feasible GPU memory. |
| `seed` | -1 = random in GUI; actual generation uses a random integer in `[0, 2**32)`. For reproducible tests use a fixed nonnegative seed in that range. |
| `steps` | 6 default; integers 1–100 accepted, higher values experimental |
| `lowvram` | true; reduces memory pressure at the cost of speed |
| `smart_framing` | true; reference aspect-ratio helper |
| `references` | Ordered list of absolute paths; empty for Generate, 1–16 for Edit |
| `output_dir` | `<H3 root>/output/images` |
| `edit_output_dir` | `<H3 root>/output/edits` |
| `filename_prefix` | `H3_Image`; use letters, digits, underscore or hyphen |
| `diffusion`, `encoder`, `vae` | Absolute paths to selected checkpoint files; initially empty |
| `backend_url` | `http://127.0.0.1:8197` |
| `backend_python`, `backend_main` | Installed backend executable and entry point (see file map) |
| `last_checkpoint_dir`, `last_image_dir` | GUI browsing history; preserve unless needed |

Sampler is fixed to Euler, scheduler simple, CFG 1.0 and denoise 1.0 by `build_graph()`. The graph uses `ConditioningZeroOut` for negative conditioning. Do not invent custom sampler/CFG/strength parameters or assume MiniMax H3 video settings apply to the image engine.

### Resolution choice

**Mandatory default resolution for agent-created images:** Unless the user explicitly requests a different resolution or a low-resolution draft, generate at approximately the pixel count of 2048 × 2048 (4.2 megapixels). Preserve the requested orientation and aspect ratio. Use these default presets:

| Orientation / aspect ratio | Default size |
|---|---|
| Square 1:1 | 2048 × 2048 |
| Landscape 16:9 | 2736 × 1536 |
| Portrait 9:16 | 1536 × 2736 |
| Landscape 4:3 | 2304 × 1728 |
| Portrait 3:4 | 1728 × 2304 (Custom) |

For other aspect ratios, choose dimensions close to the same pixel area, aligned to multiples of 32 when practical. The built-in 2736 × 1536 presets are acceptable as provided. For video references, use the planned video's orientation. Apply these defaults to Generate and Edit jobs, including batches and runtime scripts, regardless of a previously saved GUI resolution. If no orientation is requested or implied, use square. Do not automatically choose 4096 × 4096, other 4K presets, or low-resolution drafts. Use those only when explicitly requested; if memory is insufficient, report the issue and ask before lowering the resolution. A video test at 384p does not imply that its reference images should be low resolution.

Other available built-in presets include 1024×1024, 1280×720, 1360×768, 1536×864, 1536×1024, 1536×1152, 1920×1080, 2048×1152, 2048×1536, 2304×1296, 3072×1728, 3072×3072, 4096×2304, 4096×3072, 4096×4096 and portrait 864×1536, 1152×2048, 1296×2304. These are alternatives, not the default agent production sizes.

### Smart framing

With `smart_framing=true`, the runtime expands reference images whose aspect ratio differs substantially from the requested output. It places the entire original on a blurred background canvas and adds a framing-preservation instruction to the prompt. It does not guarantee accurate outpainting or exact framing. Originals are not altered. Turn it off for a controlled comparison, a deliberate crop or if blurred-border conditioning harms a result; keep all other settings fixed for the comparison.

## 5. Prompting and reference consistency

Use self-contained visual instructions, not the conversation history. Describe the desired image, subjects, pose, composition, camera viewpoint, lighting, environment, materials, visual style and essential details. For edits, clearly state what to change and what must remain unchanged. Identify reference roles explicitly by their ordered image numbers; the model does not infer roles from filenames reliably. Example: “Image 1 is the room to edit. Preserve its layout, desk, monitor and daylight. Image 2 shows the character's face and outfit. Place that character seated at the desk, seen from the monitor's webcam viewpoint, with consistent proportions and natural lighting.” This is prompt guidance, not guaranteed model behavior.

- Create single clean reference images when possible. Avoid collages unless a multi-view reference sheet is explicitly useful.
- For a consistent character, reuse the best approved identity reference and describe the unchanged distinguishing details. Add only the necessary extra references. For a reverse angle, describe the actual spatial relationships and camera position, not merely “opposite side”.
- For video production, plan references around the requested shot and keep the same visual style across the active project. Use positive, concrete instructions; don't copy video-only shot timestamps, audio tags or music instructions into still-image prompts.
- To test alternatives, keep the seed and model configuration constant when comparing one setting, or use different explicit seeds when exploring compositions. Save separate files and inspect them. Do not label an unreviewed output as an approved character reference.
- No guarantee of exact text, identity, anatomy, spatial consistency or requested composition. Verify the actual result before passing it to GrizzlyMax.

## 6. Checkpoints and performance

The current installer offers these known filenames under `models/minimax_h3_image/`:

| Group | File options |
|---|---|
| `diffusion_models` | `minimax_h3_image_pruned_w4a8.safetensors`, `minimax_h3_image_pruned_int8_convrot.safetensors` |
| `text_encoders` | `qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors`, `qwen3vl_32b_minimax_h3_int8_convrot.safetensors` |
| `vae` | `minimax_h3_image_vae_fp16.safetensors` |

Use the currently selected working combination. `installed_models()` scans the app's `models/` and legacy `vendor/ComfyUI/models/` directories. It categorizes recognized `.safetensors` files and accepts renamed files in the dedicated H3 Image checkpoint subdirectories. The GUI allows selecting an additional absolute checkpoint path manually. `make_extra_paths(selected)` maps checkpoint directories for the backend; `build_graph()` receives model basenames, not absolute paths. Avoid ambiguous duplicate basenames; verify the intended model is available in the backend. Do not substitute the video VAE for the image VAE.

The installer documents a CUDA 13 backend and portable Python 3.11. Use the installed working environment and GPU drivers, not unverified performance tweaks. `lowvram` defaults to true; retain the user's setting unless specifically testing it. Never launch image generation concurrently with a heavy GrizzlyMax video job without explicit approval and a VRAM check. Finish the image batch and release its owned backend before starting video generation if memory is constrained. Sequential image jobs with the same models can reuse one backend process; changed checkpoint paths, low-VRAM mode or backend configuration can require a restart.

If installation is explicitly requested, the existing `install.bat` menu offers full install (W4A8 + NVFP4 + image VAE), backend upgrade retaining weights, or GUI-only. Do not run it just because an unfamiliar model name appears. The source's GUI-only menu/README describes connecting to an existing backend, but `ensure_backend()` rejects a responding backend not owned by the current `Server` instance. See the next section; do not assume GUI-only external reuse works. Large downloads and installation changes require user approval.

## 7. Direct agent generation using the existing runtime

There is no dedicated headless CLI in this version. The following is a reusable pattern using the exact installed Python APIs, not a proposed new feature. A local agent with execution permission can write a small project-local script and run it with the image app's existing GUI Python environment. This avoids desktop automation and does not change global GUI settings. The script's source and job description should stay inside the active production project. Do not modify `engine.py` or `h3_image_gui.py` merely to generate images.

**Preflight:** Confirm the active project and destination, read saved settings, verify the three checkpoint files and backend paths exist, and verify references. Make sure no competing GPU job is active. Do not start a second backend if the GUI already owns one. If necessary use the GUI instead, or close it with the user's approval after confirming no job is running. Do not kill another application's backend or interrupt a job. The `ensure_backend` function rejects another process responding on its configured port. For a known compatible externally managed H3 backend, the lower-level `generate()` function can target its loopback URL directly if the required models are already exposed; do not terminate that external process or claim the GUI's `ensure_backend` attaches to it. Prefer the owned-backend example below.

Save the following example as a project-local `run_h3_images.py`. Edit the absolute `APP_ROOT` / `PROJECT` paths and `JOBS` values to match the task. Do not leave placeholders in an actual run. The example contains a text-only job and an optional reference-conditioned job; only include jobs actually requested. The reference example is commented out to avoid accidentally requesting a nonexistent asset.

```python
import sys
import threading
import random
import time
import re
from pathlib import Path

APP_ROOT = Path(r"C:\path\to\h3_image_standalone").resolve()
PROJECT = Path(r"E:\grizzlymax\output\project_5_example").resolve()
OUT = PROJECT / "images"
sys.path.insert(0, str(APP_ROOT))

from helpers.h3_image_gui import read_settings
from runtime.h3_image.engine import Server, ensure_backend, generate

JOBS = [
    dict(name="room_front", mode="Generate", prompt="A realistic daylight room with a desktop computer and a black monitor screen, viewed from the doorway.", width=2736, height=1536, seed=12345, references=[]),
    # dict(name="room_reverse", mode="Edit", prompt="Image 1 is the room. Show its exact layout from the monitor webcam viewpoint, preserving the furniture and lighting.", width=2736, height=1536, seed=12346, references=[str(PROJECT / "references" / "room.png")]),
]

d = read_settings()
selected = {k: d[k] for k in ("diffusion", "encoder", "vae")}
for k, path in selected.items():
    if not path or not Path(path).is_absolute() or not Path(path).is_file():
        raise RuntimeError(f"Missing/invalid selected {k}: {path!r}. Choose the installed checkpoint first.")
for k in ("backend_python", "backend_main"):
    if not Path(d[k]).is_file():
        raise RuntimeError(f"Backend path missing: {d[k]}")
# Never send local references to an unapproved remote endpoint.
from urllib.parse import urlsplit
base = d["backend_url"]
url = urlsplit(base)
if url.scheme != "http" or url.hostname != "127.0.0.1":
    raise RuntimeError("Expected a local loopback H3 backend; verify configuration.")

# Validate all requested jobs before starting the engine.
for j in JOBS:
    if j["mode"] not in ("Generate", "Edit") or not j["prompt"].strip():
        raise ValueError("Invalid mode or empty prompt")
    refs = j.get("references", [])
    if j["mode"] == "Generate" and refs:
        raise ValueError("Use Edit mode for references")
    if j["mode"] == "Edit" and not 1 <= len(refs) <= 16:
        raise ValueError("Edit mode requires 1–16 references")
    if any(not Path(p).is_absolute() or not Path(p).is_file() for p in refs):
        raise FileNotFoundError("Missing or non-absolute reference path")
    if any(type(j[k]) is not int or not 256 <= j[k] <= 8192 for k in ("width", "height")):
        raise ValueError("Invalid size")
    steps = j.get("steps", d["steps"])
    if type(steps) is not int or not 1 <= steps <= 100:
        raise ValueError("Invalid steps")
    seed = j.get("seed", -1)
    if type(seed) is not int or not -1 <= seed < 2**32:
        raise ValueError("Invalid seed")

OUT.mkdir(parents=True, exist_ok=True)
stop = threading.Event()
server = Server(lambda msg: print(msg, flush=True))
models = {k: Path(v).name for k, v in selected.items()}
try:
    ensure_backend(server, d["backend_python"], d["backend_main"], base,
                   d["lowvram"], server.log, stop, selected=selected)
    for i, j in enumerate(JOBS, 1):
        seed = j.get("seed", -1)
        if seed == -1:
            seed = random.randrange(2**32)
        # Unique names preserve previous trials even with the same seed.
        name = re.sub(r"[^A-Za-z0-9_-]", "_", j["name"])
        prefix = f"{name}_{time.time_ns()}_{i}"
        paths = generate(base, None, j["prompt"], seed,
                         j["width"], j["height"], models,
                         j.get("references", []), OUT, prefix,
                         server.log, stop,
                         smart_framing=j.get("smart_framing", d["smart_framing"]),
                         steps=j.get("steps", d["steps"]))
        for p in paths:
            if not p.is_file() or p.stat().st_size == 0:
                raise RuntimeError(f"Output missing or empty: {p}")
            from PIL import Image
            with Image.open(p) as im:
                im.verify()
            print(f"SAVED: {p} | SEED: {seed}", flush=True)
            # Agent: record settings, references, prompt and path in project notes.
finally:
    # Stop only this script's own backend. External processes are not owned.
    server.stop()
    if server.proc is not None:
        server.proc.wait(timeout=30)
```

Run from CMD (replace paths with actual locations):

```cmd
cd /d "C:\path\to\h3_image_standalone"
"environments\h3_image_gui\Scripts\python.exe" "E:\grizzlymax\output\project_5_example\run_h3_images.py"
```

The runtime starts the local backend, waits for `/system_stats`, prepares references if enabled, uploads them to the loopback `/upload/image`, builds the graph internally, submits to `/prompt`, logs the returned request ID, polls `/history/<prompt_id>` and retrieves finished images through `/view`. It returns a list of actual output `Path` objects. The `graph` argument to `generate()` is unused in the supplied implementation; pass `None`. Keep the command alive until results arrive. Allow enough execution time for backend startup and inference. Do not submit duplicate jobs simply because a command is still running. Record any partial successes before retrying.

For a larger batch, keep one owned server for sequential jobs with identical checkpoint/low-VRAM configuration; change prompts, reference lists, sizes, seeds and steps per job as needed. To generate dependent edits, use the actual returned file from the preceding job as reference 1. Do not assume that an output filename or suffix is fixed. Changing selected model combinations requires appropriate model-path configuration and backend restart. The example is a source-compatible operating pattern, not a GPU-tested new CLI or a persistent queue service.

## 8. Verify results, troubleshoot and hand off to GrizzlyMax

- The runtime normally generates files named `<prefix>_<timestamp>_<seed>.<extension>`, with an additional numbered suffix if multiple outputs are returned. Use the returned paths, never guess the timestamp. The backend itself also writes internal output files with `H3_Image/` prefixes. Only the fetched project-local result should be used for project handoff.
- Check that each output exists, opens and has plausible dimensions. Inspect the actual image visually where available, particularly faces, reference identity, objects and spatial consistency. Report the saved path and seed to the user. If visual inspection is unavailable, state that only the file was validated.
- If generation fails, read the actual backend/GUI log and identify missing weights, port conflict, driver/VRAM failure, backend startup error, incompatible workflow nodes or incorrect input. The startup timeout defaults to 180 seconds and generation polling times out after 60 minutes. Do not repeatedly reinstall the backend or download models to troubleshoot unrelated failures.
- The GUI's Stop waiting button only cancels polling; it does not reliably interrupt inference. Verify the server job state before retrying, closing or launching another GPU-heavy app. Do not cancel active jobs without authorization.
- If a project has a confirmed image candidate, save it to the shared GrizzlyMax project reference directory and record its purpose. For GrizzlyMax video generation use its own `AGENTS.md` and batch bridge, with actual absolute image paths. H3 Image does not submit GrizzlyMax video jobs, create RefMods or edit BeatSyncFX layers by itself.
- For evaluating whether H3 Image merits future integration, compare a few representative text-to-image, identity-preserving edit, multi-reference composition and reverse-angle jobs. Record model combination, seed, resolution, steps, elapsed time, observed peak VRAM if measurable and result quality in the project. Do not download alternative weights just to expand this test without asking.

## 9. Development and integration boundaries

This standalone app is currently being evaluated independently of GrizzlyMax. Keep it standalone unless the user explicitly requests integration. Do not duplicate its large checkpoint downloads in GrizzlyMax. For future integration, reuse its implemented backend and runtime, preserve the portable folder conventions, JSON-only settings, and existing model paths; prefer a single shared installed checkpoint copy when supported. The current code does not implement a dedicated CLI, batch queue, model-size reporting or automated image quality scoring. Do not advertise these as existing features. Use the source/runtime entry points documented above, or propose an explicit implementation separately if requested.
