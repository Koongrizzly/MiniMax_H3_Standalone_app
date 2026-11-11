"""RelayVSR external backend adapter. Does not import or modify GrizzlyMax's torch env.

A separate installed inference executable must be configured in presets/setsave/relayvsr.json.
The executable must accept source video, output path and desired native upscale factor.
"""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys


def log(message):
    print(message, flush=True)


def run(command):
    p = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                         text=True, errors="replace", bufsize=1)
    for line in p.stdout:
        log(line.rstrip())
    rc = p.wait()
    if rc:
        raise RuntimeError(f"External backend returned exit status {rc}")


def args_from_config(config_path, source, destination, scale):
    data = json.loads(Path(config_path).read_text(encoding="utf-8"))
    template = data.get("command")
    if not isinstance(template, list) or len(template) < 2 or not all(isinstance(v, str) and v for v in template):
        raise ValueError("RelayVSR command must be a non-empty JSON argument array")
    for name in ("input", "output", "scale"):
        if not any("{" + name + "}" in part for part in template):
            raise ValueError(f"Missing {{{name}}} placeholder")
    replacements = {"{input}": str(source), "{output}": str(destination), "{scale}": str(scale)}
    return [replace_tokens(part, replacements) for part in template]


def replace_tokens(part, replacements):
    for name, value in replacements.items():
        part = part.replace(name, value)
    return part


def main():
    parser = argparse.ArgumentParser()
    for name in ("input", "output", "scale", "ffmpeg", "ffprobe", "config"):
        parser.add_argument("--" + name, required=True)
    args = parser.parse_args()
    source, destination = Path(args.input).resolve(), Path(args.output).resolve()
    if not source.is_file():
        raise FileNotFoundError(f"Source missing: {source}")
    if source == destination or destination.suffix.lower() != ".mp4":
        raise ValueError("Output must be a different MP4 file")
    scale = int(args.scale)
    if scale not in (1, 2, 3, 4):
        raise ValueError("RelayVSR supports x1/x2/x3/x4 output using the X4 model")
    if not Path(args.ffmpeg).is_file() or not Path(args.ffprobe).is_file():
        raise FileNotFoundError("Bundled FFmpeg or FFprobe missing")
    dest_parent = destination.parent
    dest_parent.mkdir(parents=True, exist_ok=True)
    raw_output = dest_parent / f".__relayvsr_raw_{os.getpid()}.mp4"
    merged_output = dest_parent / f".__relayvsr_audio_{os.getpid()}.mp4"
    try:
        command = args_from_config(args.config, source, raw_output, scale)
        log(f"VSR_STAGE: RelayVSR x{scale} via configured external backend")
        # No shell=True; arguments with Windows spaces remain correctly quoted.
        run(command)
        if not raw_output.is_file() or raw_output.stat().st_size < 1024:
            raise RuntimeError("RelayVSR backend did not create a valid MP4 at the requested output path")
        # Refuse silent incorrect-sized results from a misconfigured model or adapter.
        def probe_size(path):
            proc = subprocess.run([args.ffprobe, "-v", "error", "-select_streams", "v:0",
                                   "-show_entries", "stream=width,height", "-of", "csv=p=0:s=x", str(path)],
                                  capture_output=True, text=True, check=True)
            return tuple(int(v) for v in proc.stdout.strip().splitlines()[0].split("x"))
        src_size = probe_size(source)
        target = (src_size[0] * scale, src_size[1] * scale)
        actual = probe_size(raw_output)
        if actual != target:
            raise RuntimeError(f"Unexpected RelayVSR resolution {actual}, expected native x{scale} {target}")
        log("VSR_STAGE: copying video and preserving source audio")
        remux = [args.ffmpeg, "-hide_banner", "-y", "-i", str(raw_output), "-i", str(source),
                 "-map", "0:v:0", "-map", "1:a?", "-c:v", "copy", "-c:a", "copy",
                 "-movflags", "+faststart", str(merged_output)]
        run(remux)
        if not merged_output.is_file() or merged_output.stat().st_size < 1024:
            raise RuntimeError("Audio remux did not create a valid output")
        if destination.exists():
            raise FileExistsError(f"Refusing to overwrite output: {destination}")
        os.replace(merged_output, destination)
        log(f"JOB_COMPLETE: {destination}")
    finally:
        for filename in (raw_output, merged_output):
            try:
                filename.unlink(missing_ok=True)
            except OSError:
                pass


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        log(f"JOB_FAILED: {exc}")
        sys.exit(1)
