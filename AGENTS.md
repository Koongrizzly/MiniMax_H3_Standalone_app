# GrizzlyMax — local agent operating guide

## Read this first

GrizzlyMax is a portable Windows MiniMax H3 video/audio production application. Use the installed application and its existing workflows to produce media; do not rewrite the application merely to complete a creative task. Work only within user-authorized project folders. This file is guidance, not a security boundary. Ask before accessing unrelated folders, installing software, changing system settings, downloading weights, deleting files or publishing/uploading anything. Never read or expose Telegram credentials or other secrets. Keep originals and existing queue entries intact. Do not modify README.md, .gitignore or repository settings unless explicitly requested.

For detailed feature behavior, consult `assets/Feature_list.html` (search for the relevant section rather than reading the entire document). For exact automation parameters, use the live bridge `schema` and `settings` commands. The installed code is authoritative if the documentation differs. Do not repeatedly scan the whole repository, model directories, environments, outputs or archived ZIPs. Start with the relevant module below. Do not assume a GUI feature has a CLI command.

## Mandatory project storage — applies to every creative workflow

For every new production project, create exactly one dedicated project folder directly inside GrizzlyMax's default `output/` directory, e.g. `<GrizzlyMax root>/output/project_5_manga/`. Choose the next unused project number and a descriptive name when none is supplied. For an existing project, reuse its original folder across sessions and tools; never create a new project folder merely because a new task, test, revision or chat starts. If the active project is ambiguous, confirm it before writing files.

**Everything produced for that project must stay inside that folder, from the first planning step to the final export.** This includes references and generated images, source media copies, audio, prompts, batch JSON, shot lists, planning notes and test observations, test clips (including 384p), failed/rejected candidates retained for review, HQ clips, extracted frames, analysis files, logs and receipts saved for the project, upscaled clips, Timeline Director and Music Clip Creator project files, BeatSyncFX projects/renders, OpenShot `.osp` projects and associated assets, intermediate edits, thumbnails and final exports. Use descriptive subfolders inside the project root if useful; do not scatter project assets across applications' default output folders, Desktop, Documents, Downloads, or other drives. Keep a concise project manifest or README inside the project folder recording the current plan, assets, settings, job IDs, test results, decisions and next steps (not private model reasoning).

- Before generation, set the native output destination to the active project folder or a subfolder; for bridge batches use the supported `output_folder` field with an absolute path and check the returned output paths. Put batch JSON in the project folder, not `presets/setsave/`.
- For MiniMax Image Editor, BeatSyncFX, OpenShot and other tools, explicitly select destinations inside the same GrizzlyMax project folder. If a tool cannot write there directly, use its supported workflow, copy/move the resulting project artifacts into the project folder safely, and update references before continuing. Do not move files still needed by an active job or delete originals. If a tool cannot relocate its artifacts safely, report the limitation rather than claiming the project is self-contained.
- Shared installed models, environments, global application settings, native caches, queue state and bridge mailbox remain in their required application locations; do not relocate them. Project-specific temporary work should be kept inside the project folder whenever the tool permits it.
- Verify that the deliverables and project dependencies resolve inside the project folder. Copy approved external source assets into it when needed for a portable project; preserve original files and avoid duplicating shared model weights. Never overwrite unrelated projects or clean up previous projects without explicit permission.

## Choose the right workflow

| Task | Use | Relevant source |
|---|---|---|
| Single clip, independent clips, references, continuation | Generate / live batch bridge (one job is also a batch) | `helpers/minimax_h3_gui.py`, `helpers/generate.py`, `helpers/generate_ref.py`, `runtime/minimax_batch_import.py` |
| Connected story with editable cuts, continuity and assembly | Timeline Director | `helpers/minimax_timeline.py` |
| Complete song-driven video with original master soundtrack | Music Clip Creator | `helpers/minimax_music_clip.py` |
| Structured H3 prompt construction | H3 Prompt Builder | `h3_prompt_builder/server.py`, `h3_prompt_builder/public/local/` |
| Lyrics, overlays, beat effects, tracking and final effects render | BeatSyncFX | `BeatSyncFX/helpers/gui.py`, `core.py`, `queue_render.py` (under the same helpers folder) |
| Upscale existing clips | Native VSR queue / bridge | `helpers/nvidia_vsr_gui.py` |
| RefMod creation | Generate tab RefMod tools | `runtime/refmod_create.py`, `runtime/refmod_voice_create.py` |
| Remote control | Telegram tab (only if explicitly requested) | `helpers/minimax_telegram_remote.py` |
| Runtime/model/VRAM issues | Settings and native backend | `helpers/minimax_h3_int4_backend.py`, `runtime/vram_manager.py`, `runtime/vram_auto.py` |

## Generation operating guide

- T2VA: text-only generation with native synchronized video/audio. FL2VA: first frame, optional last frame, or continuation from an existing video. Ref2VA: use ordered image/video/audio references to maintain subjects, locations, props or voices. Use actual user-provided media, not invented paths. Ref2VA supports up to 9 image, 3 video and 3 audio references; use only those needed.
- Use the current Generate settings unless the user requests overrides. Preserve checkpoint/hybrid, LoRAs, sampler, scheduler, CFG, shifts, acceleration and VRAM settings. Do not assume all models use the same optimal steps. If the user requests a quick test, use the supported 384p preset; do not silently downgrade final renders. At 24 FPS use native frame presets; 124 frames is about 5.17 seconds. For 704p landscape use 1280×704. Use the schema for supported dimensions and frame counts. Random seed is -1; use a fixed seed when comparing settings.
- RefMods are reusable .safetensors references, not trained LoRAs. Preserve ordered subject assignments; multiple identities can mix. Use the dedicated T2VA RefMod toggle for experimental text-to-video conditioning. Do not mix RefMods with ordinary reference media through the bridge.
- For continuity, choose the previous video explicitly or use the native dependency mechanism. Keep the requested history/latent/audio settings; 90 history frames is a useful user-preferred starting point when unspecified. Do not invent a new chain when a continuation is requested. Exact source audio is distinct from audio memory. Preserve the original soundtrack if requested.
- Prompt each clip as a self-contained instruction. For Ref2VA use ordered `<Picture 1>` etc. before `<Subject 1>` etc.; keep character identity, wardrobe, setting and action clear. Recommended structure: [REFERENCE IMAGES], [SUBJECTS], [SELECTED VISUAL STYLE LOCK], [SCENE DESCRIPTION], [OVERALL SOUNDSCAPE], [MUSIC]. Use short coherent action, positive concrete wording and deliberate camera movement; for short clips prefer a single evolving shot or at most two purposeful shots. Use timestamps only when useful; avoid a new shot at the very end. Do not use vague conversational references such as “same as before.” Preserve exact project-specific requirements, but do not carry them into unrelated projects.
- For a multi-clip request, plan the required clip count, prompts, references, intended durations and output names first. Validate and queue once. Check receipts, track job IDs, inspect completed outputs if asked, and report actual results, not assumed success. Keep generation and assembly separate; submission is not completion.

## Timeline Director

Use for connected sequences and non-destructive editing. Load/create a project, import current Generate settings if intended, add clips with explicit generation/continuation modes, references and durations, then generate selected/missing/all clips as requested. Preserve locked clips and existing candidates. A stale clip is not necessarily missing or unusable. Use 384p tests for prompt checks when requested and HQ Restart for final regeneration; respect per-clip overrides. Preserve trim, speed, volume, transitions and soundtrack edits. Transitions overlap adjacent clips and affect final duration. Assemble only after checking output paths and dependencies; never overwrite originals. Queue assembly and report the resulting file. The batch bridge does not create Timeline projects; use the existing native workflow or inspect its project serialization if the user explicitly requests project editing. Do not invent unsupported CLI commands.

## Music Clip Creator

Use for a master song with planned visual shots and final music-video assembly. Load the song, set creative brief, visual style, cast/locations, reference roles and generation quality. Choose instrumental mode if lyrics are not relevant; otherwise use existing lyrics/Whisper timing. Analyze song, review Director / Shot List, assign references and refine prompts. Preserve fixed cast or randomization choices; random mode can rotate references, manual mode preserves assignments. Avoid treating scenery or objects as performers. Generate selected or missing shots, not all completed shots again. Each shot uses its corresponding exact source audio chunk; final assembly trims to the planned ranges and muxes the original master song. Verify duration, continuity and missing shots before assembly. This workflow has its own project manifest and is not interchangeable with a generic generation batch.

## BeatSyncFX

Use existing footage rather than regenerating it to add graphics. Load the correct video, analyze audio/beat timing, review lyrics (optional), add timed effects/media layers, preview, save and render. Use existing project JSON as schema evidence before changing it; preserve unknown keys, timestamps, source paths, and unrelated effects. Do not re-run Whisper over corrected lyrics unless requested; Medium is the preferred model when available. Effects may overlap and should have explicit time ranges. Preserve the original video and soundtrack, and use the existing queue render integration when embedded. Timeline handoffs can attach finished effects renders as alternate candidates without replacing source media. Keep that project open if automatic attachment is required. Do not claim direct CLI render support without verifying it in installed code. Do not rewrite the editor for a project-only request.

## Prompt Builder, queue and performance

Prompt Builder is optional: Codex can write H3 prompts directly using the guide above. Its local LLM and companion image prompts are available when needed; do not launch extra models unnecessarily. Queue status is the source of truth for live jobs; do not clear history, cancel jobs, retry failed jobs or reset counters unless asked. Never start competing GPU generation processes. On a failure inspect the actual job error/log and retry only the requested failed/missing work. Keep the installed VRAM protection and acceleration settings; do not enable aggressive memory overrides, shared-memory spill or warm backends without checking hardware and existing settings. Local generation does not need internet; downloads and remote integrations do. Use local FFmpeg/FFprobe under `presets/bin` for media checks.

## Use the live GrizzlyMax queue

Run commands from the GrizzlyMax root (the folder with `start_MM_H3.bat`). GrizzlyMax must be open, using its normal portable environment. Use one app instance. Do not edit `presets/setsave/minimax_h3_queue.json` directly: the GUI owns that file and keeps a live in-memory queue. Do not start separate GPU generation processes while the integrated queue is running.

The local automation interface needs no extra installation, network connection, API key or GUI clicking. Call it through the existing portable Python, not system Python:

```cmd
environments\.minimax_h3_int4\python.exe -m runtime.codex_bridge schema
environments\.minimax_h3_int4\python.exe -m runtime.codex_bridge settings
environments\.minimax_h3_int4\python.exe -m runtime.codex_bridge validate-batch "path\to\batch.json"
environments\.minimax_h3_int4\python.exe -m runtime.codex_bridge enqueue-batch "path\to\batch.json"
environments\.minimax_h3_int4\python.exe -m runtime.codex_bridge status
```

**When asked to create and queue a batch, do both.** Create the batch JSON inside the active project folder under `output/`, validate it, then run `enqueue-batch` yourself. Do not hand the JSON back to the user for manual import. This command is a real queue submission, not a file export; use it only when the user requests queueing/generation. Existing pending jobs are preserved. The command returns as soon as submission completes, not when videos finish. Check `ok`, `queued_generation_jobs`, job IDs and output paths in the JSON receipt. If `ok` is false, inspect the error and any already-accepted jobs before fixing/retrying. Never blindly submit the full batch again.

## Batch format

Use a JSON object with `defaults` and `jobs`. A per-job value overrides the default. Every missing generation setting inherits the Generate tab snapshot captured when submission begins; references, RefMods, exact audio and continuation switches are deliberately reset per job unless explicitly supplied. Specify the intended mode in every job or in defaults. If omitted the batch uses Ref2VA for compatibility with the existing importer. `schema` lists accepted fields and aliases; `settings` shows actual current settings. `validate-batch` checks references, supported settings and resolutions without creating jobs; native generation checks still run at enqueue. Use true/false JSON booleans, not strings. Use native frame counts (124, 141, 158, etc.); other counts are normalized and reported. For exact dimensions use `width` and `height`; 704p is 1280×704, not 1280×720. Native GUI presets and limits apply. Prefer absolute paths; relative media/model/LoRA paths resolve against the batch file's folder. Do not mix ordinary reference media and RefMods in a Ref2VA job.

```json
{
  "defaults": {
    "mode": "ref2va", "frames": 124, "width": 832, "height": 448,
    "steps": 4, "scheduler": "beta", "seed": -1,
    "output_folder": "E:/grizzlymax/output/project_5_manga/clips"
  },
  "jobs": [
    {"name": "clip_001", "prompt": "A woman walks through a city.", "ref_images": ["E:/grizzlymax/output/project_5_manga/references/woman.png"]},
    {"name": "clip_002", "prompt": "The woman watches a car pass.", "ref_images": ["E:/grizzlymax/output/project_5_manga/references/woman.png"]},
    {"name": "clip_003", "mode": "t2va", "prompt": "A wide shot of a city skyline."}
  ]
}
```

Use `first`, `last`, `continue_video` or `continue_last_result` with FL2VA. A later `continue_last_result` job depends on the preceding native generation job in that batch, not an intervening VSR job. Use `refmods` with saved RefMod paths; for T2VA + RefMods also set `t2va_use_refmods: true`. Use `ref_audios` and `lock_source_audio` for exact source audio. `generation_vsr_enabled`, `generation_vsr_scale` and `generation_vsr_engine` can queue a dependent upscale after each generation.

## Other queue tools

```cmd
environments\.minimax_h3_int4\python.exe -m runtime.codex_bridge upscale "E:\grizzlymax\output\project_5_manga\clips\clip.mp4" --scale 2 --engine nvidia_vsr
environments\.minimax_h3_int4\python.exe -m runtime.codex_bridge upscale "E:\grizzlymax\output\project_5_manga\clips\clip.mp4" --scale 4 --engine relayvsr
environments\.minimax_h3_int4\python.exe -m runtime.codex_bridge status
environments\.minimax_h3_int4\python.exe -m runtime.codex_bridge result REQUEST_ID
```

Upscale uses the native installed engine, queue and current VSR quality/cleanup settings. NVIDIA VSR supports x1/x2/x3; RelayVSR supports x1/x2/x3/x4. Both need the appropriate installed backend and local FFmpeg. `status` returns live job numbers, IDs, output paths, state, progress and errors. `result` recovers a prior submission receipt by request ID. Exit code 0 means success, 1 means failure (possibly partial), 2 means receipt unavailable/timeout. For a large batch use `--timeout 900` **before** the command, e.g. `... -m runtime.codex_bridge --timeout 900 enqueue-batch "batch.json"`. A timeout does not cancel submission; retrieve the original receipt instead of resubmitting. If the app crashed mid-request and no receipt exists, inspect the queue before submitting any remaining clips. Requests from an earlier app session are not automatically replayed.

The bridge does not require a new server or expose a network port. It uses files under `runtime/codex_mailbox`, which are local operational data, not project source. Keep the desktop app open until the queue receipt arrives. Do not manually alter the queue during a large batch submission. Existing BeatSyncFX project/render tooling, Timeline Director and Music Clip Creator remain unchanged. The batch interface does not create Timeline Director projects or assemble a timeline.

## Development and portable layout

Root launchers: `start_MM_H3.bat`, `install.bat`. GUI/application modules in `helpers/`, non-GUI support and command tooling in `runtime/`, portable virtual environments in `environments/`, model files in `models/`, persistent settings in `presets/setsave/` as JSON. BeatSyncFX has its own portable subtree. Use CMD and portable Python, not system Python or PowerShell. Preserve JSON-only settings; never introduce QSettings/Registry persistence. Keep GUI compact, creator-focused, maximized by default, with vertical scrolling where needed and wheel changes only on focused controls. Put detailed feature help in the existing `assets/Feature_list.html`, not large GUI explanations. Prefer native/installed inference paths over introducing new external backends; preserve the current working MiniMax backend. Do not create extra GUI helper files for non-GUI utilities. Do not change code or install dependencies merely to operate the app. For requested code changes, make focused edits, test syntax and relevant tests, and disclose untested Windows/GPU behavior.
