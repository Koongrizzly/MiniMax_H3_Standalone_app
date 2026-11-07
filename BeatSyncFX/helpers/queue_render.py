"""Standalone subprocess entry for the host queue; no editor window is created."""
import json
import os
import sys
from pathlib import Path


def main():
    if len(sys.argv) != 4:
        print("ERROR: usage: python -m BeatSyncFX.helpers.queue_render snapshot.json ffmpeg output.mp4", flush=True)
        return 2
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    from .workers import RenderWorker
    snapshot, ffmpeg, destination = sys.argv[1:]
    project = json.loads(Path(snapshot).read_text(encoding="utf-8"))
    app = QApplication.instance() or QApplication([])
    # Queue renders use their own Qt process; GUI font registration is not inherited.
    from .fonts import register_fonts
    register_fonts()
    worker = RenderWorker(project, ffmpeg, destination)
    result = {"status": "failed"}
    def progress(i, total):
        print(f"BEATSYNC_PROGRESS {int(i * 100 / max(1, total))}", flush=True)
    def done(path):
        result["status"] = "done"
        print("Saved " + path, flush=True)
    def failed(message):
        print("ERROR: " + message, flush=True)
    def cancelled():
        result["status"] = "cancelled"
        print("ERROR: BeatSyncFX render cancelled", flush=True)
    worker.progress.connect(progress)
    worker.done.connect(done)
    worker.failed.connect(failed)
    worker.cancelled.connect(cancelled)
    # Direct invocation keeps work in this queue subprocess and preserves all
    # existing RenderWorker compositing/audio/rendering behavior.
    worker.run()
    app.processEvents()
    return 0 if result["status"] == "done" else 1


if __name__ == "__main__":
    sys.exit(main())
