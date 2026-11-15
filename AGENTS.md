# GrizzlyMax — instructions for Codex and other local coding agents

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

**When asked to create and queue a batch, do both.** Create the batch JSON locally (for example under `presets/setsave`), validate it, then run `enqueue-batch` yourself. Do not hand the JSON back to the user for manual import. This command is a real queue submission, not a file export; use it only when the user requests queueing/generation. Existing pending jobs are preserved. The command returns as soon as submission completes, not when videos finish. Check `ok`, `queued_generation_jobs`, job IDs and output paths in the JSON receipt. If `ok` is false, inspect the error and any already-accepted jobs before fixing/retrying. Never blindly submit the full batch again.

## Batch format

Use a JSON object with `defaults` and `jobs`. A per-job value overrides the default. Every missing generation setting inherits the Generate tab snapshot captured when submission begins; references, RefMods, exact audio and continuation switches are deliberately reset per job unless explicitly supplied. Specify the intended mode in every job or in defaults. If omitted the batch uses Ref2VA for compatibility with the existing importer. `schema` lists accepted fields and aliases; `settings` shows actual current settings. `validate-batch` checks references, supported settings and resolutions without creating jobs; native generation checks still run at enqueue. Use true/false JSON booleans, not strings. Use native frame counts (124, 141, 158, etc.); other counts are normalized and reported. For exact dimensions use `width` and `height`; 704p is 1280×704, not 1280×720. Native GUI presets and limits apply. Prefer absolute paths; relative media/model/LoRA paths resolve against the batch file's folder. Do not mix ordinary reference media and RefMods in a Ref2VA job.

```json
{
  "defaults": {
    "mode": "ref2va", "frames": 124, "width": 832, "height": 448,
    "steps": 4, "scheduler": "beta", "seed": -1
  },
  "jobs": [
    {"name": "clip_001", "prompt": "A woman walks through a city.", "ref_images": ["C:/media/woman.png"]},
    {"name": "clip_002", "prompt": "The woman watches a car pass.", "ref_images": ["C:/media/woman.png"]},
    {"name": "clip_003", "mode": "t2va", "prompt": "A wide shot of a city skyline."}
  ]
}
```

Use `first`, `last`, `continue_video` or `continue_last_result` with FL2VA. A later `continue_last_result` job depends on the preceding native generation job in that batch, not an intervening VSR job. Use `refmods` with saved RefMod paths; for T2VA + RefMods also set `t2va_use_refmods: true`. Use `ref_audios` and `lock_source_audio` for exact source audio. `generation_vsr_enabled`, `generation_vsr_scale` and `generation_vsr_engine` can queue a dependent upscale after each generation.

## Other queue tools

```cmd
environments\.minimax_h3_int4\python.exe -m runtime.codex_bridge upscale "C:\media\clip.mp4" --scale 2 --engine nvidia_vsr
environments\.minimax_h3_int4\python.exe -m runtime.codex_bridge upscale "C:\media\clip.mp4" --scale 4 --engine relayvsr
environments\.minimax_h3_int4\python.exe -m runtime.codex_bridge status
environments\.minimax_h3_int4\python.exe -m runtime.codex_bridge result REQUEST_ID
```

Upscale uses the native installed engine, queue and current VSR quality/cleanup settings. NVIDIA VSR supports x1/x2/x3; RelayVSR supports x1/x2/x3/x4. Both need the appropriate installed backend and local FFmpeg. `status` returns live job numbers, IDs, output paths, state, progress and errors. `result` recovers a prior submission receipt by request ID. Exit code 0 means success, 1 means failure (possibly partial), 2 means receipt unavailable/timeout. For a large batch use `--timeout 900` **before** the command, e.g. `... -m runtime.codex_bridge --timeout 900 enqueue-batch "batch.json"`. A timeout does not cancel submission; retrieve the original receipt instead of resubmitting. If the app crashed mid-request and no receipt exists, inspect the queue before submitting any remaining clips. Requests from an earlier app session are not automatically replayed.

The bridge does not require a new server or expose a network port. It uses files under `runtime/codex_mailbox`, which are local operational data, not project source. Keep the desktop app open until the queue receipt arrives. Do not manually alter the queue during a large batch submission. Existing BeatSyncFX project/render tooling, Timeline Director and Music Clip Creator remain unchanged. The batch interface does not create Timeline Director projects or assemble a timeline.

## Source layout

GUI/application modules in `helpers/`, non-GUI support and command tooling in `runtime/`, portable virtual environments in `environments/`, model files in `models/`, persistent GUI settings in `presets/setsave/` as JSON. Do not use Windows Registry/QSettings or system Python. Keep the existing GUI compact.
