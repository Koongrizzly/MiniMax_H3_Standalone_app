"""Optional MediaPipe text-behind-person quality trial; independent of the main renderer.

No dependency on the GrizzlyMax host paths. All artifacts are inside BeatSyncFX.
"""
from __future__ import annotations
import json
import os
import shutil
import subprocess
import sys
import threading
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODEL_DIR = ROOT / 'models' / 'segmentation'
MODEL_PATH = MODEL_DIR / 'selfie_segmenter_landscape.tflite'
MODEL_URL = 'https://storage.googleapis.com/mediapipe-models/image_segmenter/selfie_segmenter_landscape/float16/latest/selfie_segmenter_landscape.tflite'
OUTPUT = ROOT / 'output' / 'segmentation_test'


def ffmpeg_path():
    local = ROOT / 'presets' / 'bin' / 'ffmpeg.exe'
    if local.is_file():
        return str(local)
    # The standalone application's configured FFmpeg path is supported too.
    try:
        from helpers.core import locate_ffmpeg
        found = locate_ffmpeg()
        if found and Path(found).exists():
            return str(found)
    except Exception:
        pass
    return shutil.which('ffmpeg') or ''


def ensure_model(log):
    if MODEL_PATH.is_file() and MODEL_PATH.stat().st_size > 100_000:
        return MODEL_PATH
    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    log('Downloading optional MediaPipe landscape person model...')
    temp = MODEL_PATH.with_suffix('.part')
    try:
        urllib.request.urlretrieve(MODEL_URL, temp)
        if temp.stat().st_size < 100_000:
            raise ValueError('Downloaded model is unexpectedly small')
        temp.replace(MODEL_PATH)
    finally:
        temp.unlink(missing_ok=True)
    return MODEL_PATH


def font_path():
    for name in ('Anton-Regular.ttf', 'Inter-Variable.ttf'):
        candidate = ROOT / 'assets' / 'fonts' / name
        if candidate.is_file():
            return str(candidate)
    return 'arial.ttf'


def run_test(video, start, seconds, caption, log):
    import mediapipe as mp
    import numpy as np
    from PIL import Image, ImageDraw, ImageFont, ImageFilter

    source = Path(video)
    if not source.is_file():
        raise FileNotFoundError('Video not found: ' + str(source))
    ffmpeg = ffmpeg_path()
    if not ffmpeg:
        raise FileNotFoundError('FFmpeg not found: put ffmpeg.exe in BeatSyncFX/presets/bin or use the standalone app setup.')
    model = ensure_model(log)
    OUTPUT.mkdir(parents=True, exist_ok=True)
    WIDTH, HEIGHT, FPS = 960, 540, 12  # Quality comparison, not final output resolution.
    start = max(0.0, float(start))
    seconds = max(1.0, min(15.0, float(seconds)))
    nbytes = WIDTH * HEIGHT * 3
    label = ''.join(c for c in source.stem if c.isalnum() or c in '-_')[:45] or 'video'
    output = OUTPUT / f'{label}_text_behind_test.mp4'
    tmp_video = OUTPUT / f'{label}_silent_test.mp4'
    reader_cmd = [ffmpeg, '-hide_banner', '-loglevel', 'error', '-nostdin', '-ss', str(start), '-i', str(source),
                  '-t', str(seconds), '-vf', f'fps={FPS},scale={WIDTH}:{HEIGHT}:flags=bicubic',
                  '-f', 'rawvideo', '-pix_fmt', 'rgb24', 'pipe:1']
    encoder_cmd = [ffmpeg, '-hide_banner', '-loglevel', 'error', '-nostdin', '-y',
                   '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-s:v', f'{WIDTH}x{HEIGHT}', '-r', str(FPS), '-i', 'pipe:0',
                   '-an', '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '20', '-pix_fmt', 'yuv420p', str(tmp_video)]
    creationflags = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
    reader = subprocess.Popen(reader_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, creationflags=creationflags)
    writer = subprocess.Popen(encoder_cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE, creationflags=creationflags)
    log('Processing 960×540 at 12 fps for this quality test. The original video is untouched.')
    options = mp.tasks.vision.ImageSegmenterOptions(
        base_options=mp.tasks.BaseOptions(model_asset_path=str(model)),
        running_mode=mp.tasks.vision.RunningMode.VIDEO,
        output_confidence_masks=True,
        output_category_mask=False)
    font_size = 70
    try:
        font = ImageFont.truetype(font_path(), font_size)
    except OSError:
        font = ImageFont.load_default()
    count = 0
    try:
        with mp.tasks.vision.ImageSegmenter.create_from_options(options) as segmenter:
            while count < int(round(FPS * seconds)):
                data = reader.stdout.read(nbytes)
                if len(data) != nbytes:
                    break
                frame = np.frombuffer(data, dtype=np.uint8).reshape(HEIGHT, WIDTH, 3).copy()
                result = segmenter.segment_for_video(
                    mp.Image(image_format=mp.ImageFormat.SRGB, data=frame),
                    int(round(count * 1000 / FPS)))
                if not result.confidence_masks or len(result.confidence_masks) < 2:
                    raise RuntimeError('Model did not return a person confidence mask.')
                alpha = np.asarray(result.confidence_masks[1].numpy_view(), dtype=np.float32)
                # Resize only if task model's output size differs. Use a soft transition
                # rather than a hard binary mask to inspect hair edges.
                matte = Image.fromarray(np.uint8(np.clip(alpha, 0, 1) * 255), 'L')
                if matte.size != (WIDTH, HEIGHT):
                    matte = matte.resize((WIDTH, HEIGHT), Image.Resampling.BILINEAR)
                # Preserve soft confidence at edges; minimal smoothing reduces pixel artifacts.
                matte = matte.filter(ImageFilter.GaussianBlur(0.55))
                img = Image.fromarray(frame, 'RGB')
                text_layer = Image.new('RGB', (WIDTH, HEIGHT), (0, 0, 0))
                # draw transparent text, rather than covering the whole original frame
                text_rgba = Image.new('RGBA', (WIDTH, HEIGHT), (0, 0, 0, 0))
                draw = ImageDraw.Draw(text_rgba)
                bbox = draw.textbbox((0, 0), caption, font=font, stroke_width=2)
                tx = (WIDTH - (bbox[2]-bbox[0])) // 2 - bbox[0]
                ty = (HEIGHT - (bbox[3]-bbox[1])) // 2 - bbox[1]
                draw.text((tx, ty), caption, font=font, fill=(243, 247, 255, 255),
                          stroke_width=2, stroke_fill=(10, 19, 32, 255))
                underneath = Image.alpha_composite(img.convert('RGBA'), text_rgba).convert('RGB')
                composed = Image.composite(img, underneath, matte)
                writer.stdin.write(np.asarray(composed, dtype=np.uint8).tobytes())
                count += 1
                if count == 1 or count % 12 == 0:
                    log(f'Processed {count} frames ({count / FPS:.1f}s)')
    finally:
        if writer.stdin and not writer.stdin.closed:
            writer.stdin.close()
        if reader.stdout:
            reader.stdout.close()
        try:
            reader.wait(timeout=20)
        except subprocess.TimeoutExpired:
            reader.kill(); reader.wait()
        try:
            writer.wait(timeout=60)
        except subprocess.TimeoutExpired:
            writer.kill(); writer.wait()
    if count == 0 or writer.returncode != 0:
        detail = writer.stderr.read().decode('utf-8', errors='replace')[-2000:] if writer.stderr else ''
        raise RuntimeError('Video creation failed. ' + detail)
    # Copy original audio into the trial if present, retaining the correct seek.
    mux = [ffmpeg, '-hide_banner', '-loglevel', 'error', '-nostdin', '-y', '-i', str(tmp_video),
           '-ss', str(start), '-t', str(count / FPS), '-i', str(source), '-map', '0:v:0',
           '-map', '1:a:0?', '-c:v', 'copy', '-c:a', 'aac', '-b:a', '192k', '-shortest', str(output)]
    proc = subprocess.run(mux, capture_output=True, text=True, creationflags=creationflags)
    if proc.returncode:
        # Still provide the silent comparison if the audio mux is unsupported.
        output = tmp_video
        log('Audio copy failed; generated silent video instead: ' + proc.stderr[-250:])
    else:
        tmp_video.unlink(missing_ok=True)
    log(f'Finished: {output}')
    return output


def main():
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk
    root = tk.Tk()
    root.title('BeatSync FX — Text Behind Person | MediaPipe quality test')
    root.geometry('770x460')
    root.configure(bg='#10202b')
    path = tk.StringVar()
    begin = tk.StringVar(value='0.0')
    duration = tk.StringVar(value='5.0')
    words = tk.StringVar(value='PARADISE OR PANIC')
    status = tk.StringVar(value='This is an optional quality test. It does not modify the main BeatSync FX project.')
    container = ttk.Frame(root, padding=18); container.pack(fill='both', expand=True)
    container.columnconfigure(1, weight=1)
    ttk.Label(container, text='Video').grid(row=0,column=0,sticky='w')
    ttk.Entry(container,textvariable=path).grid(row=0,column=1,sticky='ew',padx=8)
    ttk.Button(container,text='Browse…',command=lambda:path.set(filedialog.askopenfilename(filetypes=[('Video files','*.mp4 *.mov *.mkv'),('All files','*.*')]))).grid(row=0,column=2)
    ttk.Label(container,text='Start (seconds)').grid(row=1,column=0,sticky='w',pady=9)
    ttk.Entry(container,textvariable=begin,width=12).grid(row=1,column=1,sticky='w')
    ttk.Label(container,text='Duration (max 15s)').grid(row=2,column=0,sticky='w',pady=9)
    ttk.Entry(container,textvariable=duration,width=12).grid(row=2,column=1,sticky='w')
    ttk.Label(container,text='Text behind person').grid(row=3,column=0,sticky='w',pady=9)
    ttk.Entry(container,textvariable=words).grid(row=3,column=1,columnspan=2,sticky='ew')
    log_box = tk.Text(container,height=10,wrap='word');log_box.grid(row=5,column=0,columnspan=3,sticky='nsew',pady=8)
    container.rowconfigure(5,weight=1)
    ttk.Label(container,textvariable=status,wraplength=700).grid(row=6,column=0,columnspan=3,sticky='w',pady=4)
    def log(message):
        root.after(0,lambda s=message:(log_box.insert('end',s+'\n'),log_box.see('end')))
    def go():
        video = path.get()
        if not Path(video).is_file():
            messagebox.showerror('Choose a video','Choose an existing video first.');return
        try:
            start=float(begin.get().replace(',','.'))
            secs=float(duration.get().replace(',','.'))
        except ValueError:
            messagebox.showerror('Time values','Enter numbers for Start and Duration.');return
        if start < 0 or secs < 1 or secs > 15:
            messagebox.showerror('Time values','Start must be >=0 and duration between 1 and 15 seconds.');return
        run_button.configure(state='disabled');status.set('Processing short video sample...')
        def worker():
            try:
                out=run_test(video,start,secs,words.get().strip() or 'THE FUTURE',log)
                root.after(0,lambda:(status.set('Finished: '+str(out)),run_button.configure(state='normal'),os.startfile(out) if os.name=='nt' else None))
            except Exception as exc:
                log('ERROR: '+str(exc))
                root.after(0,lambda:(status.set('Test failed: '+str(exc)),run_button.configure(state='normal')))
        threading.Thread(target=worker,daemon=True).start()
    run_button=ttk.Button(container,text='Create 5–15 second comparison',command=go)
    run_button.grid(row=4,column=0,columnspan=3,pady=15,sticky='ew')
    root.mainloop()


if __name__=='__main__':
    main()
