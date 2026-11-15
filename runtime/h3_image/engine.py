"""Runtime helpers for MiniMax H3 Image using a local hidden image engine."""
import json, os, shutil, subprocess, time, urllib.request, uuid, math, re
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]

class H3Error(RuntimeError):
    pass


def api(base, path, method='GET', data=None, timeout=30):
    url = base.rstrip('/') + path
    body = None
    headers = {}
    if data is not None:
        body = json.dumps(data).encode('utf-8')
        headers['Content-Type'] = 'application/json'
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
        return json.loads(raw) if raw else {}
    except Exception as e:
        raise H3Error(f'Backend request failed for {path}: {e}')


def upload_image(base, path):
    boundary = '----GrizzlyMax' + uuid.uuid4().hex
    data = Path(path).read_bytes()
    parts = [
        (f'--{boundary}\r\nContent-Disposition: form-data; name="type"\r\n\r\ninput\r\n').encode('utf-8'),
        (f'--{boundary}\r\nContent-Disposition: form-data; name="image"; filename="{Path(path).name}"\r\nContent-Type: application/octet-stream\r\n\r\n').encode('utf-8'),
        data,
        (f'\r\n--{boundary}--\r\n').encode('utf-8'),
    ]
    req = urllib.request.Request(base.rstrip('/') + '/upload/image', data=b''.join(parts), headers={'Content-Type': f'multipart/form-data; boundary={boundary}'})
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.loads(r.read())['name']


def fetch_output(base, file_info, dest):
    from urllib.parse import urlencode
    qs = urlencode({'filename': file_info['filename'], 'subfolder': file_info.get('subfolder', ''), 'type': file_info.get('type', 'output')})
    with urllib.request.urlopen(base.rstrip('/') + '/view?' + qs, timeout=120) as r:
        dest.parent.mkdir(parents=True, exist_ok=True)
        with dest.open('wb') as f:
            shutil.copyfileobj(r, f)


def build_graph(prompt, seed, width, height, models, uploads, prefix, steps=6, enhanced_details=False, detail_influence=0.25):
    if not isinstance(steps, int) or isinstance(steps, bool) or not 1 <= steps <= 100:
        raise H3Error('Sampling steps must be an integer from 1 to 100.')
    def node(kind, **inputs):
        return {'class_type': kind, 'inputs': inputs}
    required = ('diffusion', 'encoder', 'vae')
    missing = [x for x in required if not models.get(x)]
    if missing:
        raise H3Error('Missing checkpoints: ' + ', '.join(missing) + '. Open Settings and choose installed files.')
    g = {
        '1': node('UNETLoader', unet_name=models['diffusion'], weight_dtype='default'),
        '2': node('CLIPLoader', clip_name=models['encoder'], type='minimax', device='default'),
        '3': node('VAELoader', vae_name=models['vae']),
        '4': node('MiniMaxH3ImageConditioning', clip=['2', 0], vae=['3', 0], prompt=prompt, width=width, height=height),
        '5': node('ConditioningZeroOut', conditioning=['4', 0]),
        '6': node('KSampler', model=['1', 0], positive=['4', 0], negative=['5', 0], latent_image=['4', 1], seed=seed, steps=steps, cfg=1.0, sampler_name='euler', scheduler='simple', denoise=1.0),
        '7': node('VAEDecode', samples=['6', 0], vae=['3', 0]),
        '8': node('SaveImage', images=['7', 0], filename_prefix=prefix),
    }
    for i, name in enumerate(uploads, 1):
        if i > 16:
            raise H3Error('Maximum 16 reference images.')
        nid = str(20 + i)
        g[nid] = node('LoadImage', image=name)
        g['4']['inputs'][f'images.image_{i}'] = [nid, 0]
    if enhanced_details and not uploads:
        end = float(detail_influence)
        if not 0.01 <= end <= 1.0:
            raise H3Error('Detail influence must be between 1 and 100 percent.')
        g['9'] = node('GrizzlyMaxH3DetailConditioning', clip=['2', 0], conditioning=['4', 0], end_percent=end)
        g['6']['inputs']['positive'] = ['9', 0]
    return g


def installed_models():
    roots = [ROOT / 'models', ROOT / 'vendor' / 'ComfyUI' / 'models']
    groups = {'diffusion': [], 'encoder': [], 'vae': []}
    for root in roots:
        if not root.exists():
            continue
        for path in root.rglob('*.safetensors'):
            fn = re.sub(r'[^a-z0-9]+', '', path.stem.lower())
            # Match model families independently of punctuation and word order.
            # Dedicated H3 Image checkpoint folders also accept renamed weights.
            parts = [x.lower() for x in path.relative_to(root).parts[:-1]]
            local = 'minimax_h3_image' in parts
            if (('qwen3vl' in fn and '32b' in fn and 'minimaxh3' in fn)
                    or (local and 'text_encoders' in parts)):
                groups['encoder'].append(str(path))
            elif ('minimaxh3imagevae' in fn or (local and 'vae' in parts)):
                groups['vae'].append(str(path))
            elif (('minimaxh3image' in fn and 'vae' not in fn)
                    or (local and 'diffusion_models' in parts)):
                groups['diffusion'].append(str(path))
    return {k: sorted(set(v)) for k, v in groups.items()}


def make_extra_paths(selected=None):
    groups = installed_models()
    config = []
    for key, sub in [('diffusion', 'diffusion_models'), ('encoder', 'text_encoders'), ('vae', 'vae')]:
        dirs = sorted(set(str(Path(x).parent) for x in groups[key]))
        chosen = str(Path(selected[key]).resolve().parent) if selected and selected.get(key) else None
        if chosen:
            dirs = [d for d in dirs if d != chosen] + [chosen]
        for i, d in enumerate(dirs):
            config.append(f'h3_{key}_{i}:\n  base_path: {json.dumps(Path(d).as_posix(), ensure_ascii=False)}\n  {sub}: "."\n' + ('  is_default: true\n' if d == chosen else ''))
    out = ROOT / 'runtime' / 'h3_image' / 'extra_model_paths.yaml'
    out.write_text('\n'.join(config), encoding='utf-8')
    return out


def ensure_backend(server, python, main, base, lowvram, log, stop, timeout=180, selected=None):
    signature = (tuple(sorted((selected or {}).items())), bool(lowvram), str(main), str(python), base)
    if server.proc and server.proc.poll() is None and getattr(server, 'signature', None) != signature:
        log('Restarting image engine for changed checkpoint selection or settings.')
        server.stop()
        server.proc.wait(timeout=30)
    try:
        api(base, '/system_stats', timeout=2)
    except H3Error:
        pass
    else:
        if server.proc and server.proc.poll() is None and getattr(server, 'signature', None) == signature:
            return
        raise H3Error('Image engine port is already in use by another process. Close the other backend and retry.')
    main_path = Path(main)
    if not main_path.is_file():
        migrated = ROOT / 'models' / 'minimax_h3_image' / 'comfyui_backend' / 'main.py'
        legacy = ROOT / 'vendor' / 'ComfyUI' / 'main.py'
        if main_path == legacy and migrated.is_file():
            main_path = migrated
            main = str(migrated)
    if not Path(python).is_file() or not main_path.is_file():
        raise H3Error('Local inference backend not installed. Run install.bat first.')
    from urllib.parse import urlsplit
    port = urlsplit(base).port or 8197
    extra = make_extra_paths(selected)
    # Deploy only our self-contained node; never update ComfyUI or other nodes.
    source = Path(__file__).with_name('detail_conditioning.py')
    destination = main_path.parent / 'custom_nodes' / 'grizzlymax_h3_detail' / '__init__.py'
    destination.parent.mkdir(parents=True, exist_ok=True)
    if not destination.exists() or destination.read_bytes() != source.read_bytes():
        temporary = destination.with_suffix('.tmp')
        temporary.write_bytes(source.read_bytes())
        os.replace(temporary, destination)
    server.launch(python, str(main_path), port, lowvram, extra)
    server.signature = signature
    log('Preparing image engine...')
    started = time.monotonic()
    while time.monotonic() - started < timeout and not stop.is_set():
        if server.proc and server.proc.poll() is not None:
            raise H3Error('Image engine exited during startup. See the timestamped backend log.')
        try:
            api(base, '/system_stats', timeout=2)
            log('Image engine ready')
            return
        except H3Error:
            stop.wait(2)
    raise H3Error('Image engine did not become ready. Check logs/h3_image_backend_*.log')


def _needs_framing_fix(src_w, src_h, dst_w, dst_h):
    src_ratio = src_w / max(1, src_h)
    dst_ratio = dst_w / max(1, dst_h)
    return abs(math.log(max(src_ratio, 1e-6) / max(dst_ratio, 1e-6))) > 0.25


def _prepare_reference(path, dst_w, dst_h, index, log):
    try:
        from PIL import Image, ImageOps, ImageFilter, ImageEnhance
        try:
            resample = Image.Resampling.LANCZOS
        except AttributeError:
            resample = Image.LANCZOS
    except ImportError as exc:
        raise H3Error('Reference framing requires Pillow. Run install.bat to repair the GUI environment.') from exc
    src_path = Path(path)
    try:
        img = Image.open(src_path).convert('RGB')
    except Exception:
        return str(src_path), False
    if not _needs_framing_fix(img.width, img.height, dst_w, dst_h):
        return str(src_path), False
    dst_ratio = dst_w / max(1, dst_h)
    src_ratio = img.width / max(1, img.height)
    if dst_ratio >= src_ratio:
        canvas_h = img.height
        canvas_w = max(img.width, int(round(canvas_h * dst_ratio)))
    else:
        canvas_w = img.width
        canvas_h = max(img.height, int(round(canvas_w / dst_ratio)))
    bg = ImageOps.fit(img, (canvas_w, canvas_h), method=resample)
    bg = bg.filter(ImageFilter.GaussianBlur(radius=max(8, min(canvas_w, canvas_h) // 60)))
    bg = ImageEnhance.Brightness(bg).enhance(0.9)
    fg = img.copy()
    fg.thumbnail((canvas_w, canvas_h), resample)
    x = (canvas_w - fg.width) // 2
    y = (canvas_h - fg.height) // 2
    bg.paste(fg, (x, y))
    out_dir = ROOT / 'temp' / 'h3_image' / 'prepared_refs'
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f'{src_path.stem}_framed_{dst_w}x{dst_h}_{index}.png'
    bg.save(out_path, 'PNG')
    return str(out_path), True


def _prepare_references(paths, width, height, smart_framing, log):
    if not smart_framing or not paths:
        return list(paths), False
    out = []
    changed = False
    for i, p in enumerate(paths, 1):
        new_path, was_changed = _prepare_reference(p, width, height, i, log)
        out.append(new_path)
        changed = changed or was_changed
    if changed:
        log('Prepared references for the requested aspect ratio to reduce unwanted cropping.')
    return out, changed


def _aspect_help_prompt(prompt, reframed):
    if not reframed:
        return prompt
    prefix = 'Preserve the full contents of the reference image. Recompose it naturally for the requested aspect ratio. Do not crop away major parts of the subject or scene; expand with believable surrounding context when needed. '
    return prefix + prompt.strip()


def generate(base, graph, prompt, seed, width, height, models, references, outdir, prefix, log, stop, smart_framing=True, steps=6, enhanced_details=False, detail_influence=0.25):
    api(base, '/system_stats')
    prepared_refs, reframed = _prepare_references(references, width, height, smart_framing, log)
    names = [upload_image(base, x) for x in prepared_refs]
    token = time.strftime('%Y%m%d_%H%M%S')
    node_prefix = f'H3_Image/{prefix}_{token}_{seed}'
    final_prompt = _aspect_help_prompt(prompt, reframed)
    job = build_graph(final_prompt, seed, width, height, models, names, node_prefix, steps=steps, enhanced_details=enhanced_details, detail_influence=detail_influence)
    if enhanced_details and not names:
        if 'GrizzlyMaxH3DetailConditioning' not in api(base, '/object_info/GrizzlyMaxH3DetailConditioning'):
            raise H3Error('Enhanced fine details node not loaded. Restart the image backend and check its log.')
        log(f'Enhanced fine details: ON ({float(detail_influence):.0%} conditioning duration)')
    result = api(base, '/prompt', 'POST', {'prompt': job, 'client_id': uuid.uuid4().hex}, 120)
    pid = result['prompt_id']
    log('Generating image...')
    log('Generation request ID: ' + pid)
    start = time.monotonic()
    while not stop.is_set():
        hist = api(base, '/history/' + pid, timeout=60)
        if pid in hist:
            data = hist[pid]
            if data.get('status', {}).get('status_str') == 'error':
                raise H3Error('Image engine generation failed: ' + str(data.get('status'))[:1000])
            images = [im for output in data.get('outputs', {}).values() for im in output.get('images', []) if im.get('filename')]
            if not images:
                raise H3Error('Workflow finished but no SaveImage outputs were returned.')
            paths = []
            for i, im in enumerate(images, 1):
                dest = Path(outdir) / f"{prefix}_{token}_{seed}{Path(im['filename']).suffix}"
                if len(images) > 1:
                    dest = Path(outdir) / f"{prefix}_{token}_{seed}_{i}{Path(im['filename']).suffix}"
                fetch_output(base, im, dest)
                paths.append(dest)
            return paths
        if time.monotonic() - start > 3600:
            raise H3Error('Generation timed out after 60 minutes.')
        stop.wait(2)
    raise H3Error('Waiting cancelled. The server job may continue running.')


class Server:
    def __init__(self, log):
        self.proc = None
        self.log = log

    def launch(self, python, main, port, lowvram, extra_paths=None):
        if self.proc and self.proc.poll() is None:
            return
        cmd = [python, main, '--listen', '127.0.0.1', '--port', str(port), '--disable-auto-launch']
        if lowvram:
            cmd += ['--lowvram']
        if extra_paths:
            cmd += ['--extra-model-paths-config', str(extra_paths)]
        stamp = time.strftime('%Y%m%d_%H%M%S')
        logpath = ROOT / 'logs' / f'h3_image_backend_{stamp}.log'
        logpath.parent.mkdir(exist_ok=True)
        self.log('Starting backend: ' + str(logpath))
        f = open(logpath, 'a', encoding='utf-8', buffering=1)
        env = os.environ.copy()
        env.update({
            'HF_HOME': str(ROOT / 'models' / 'minimax_h3_image' / 'hf_cache'),
            'TORCH_HOME': str(ROOT / 'models' / 'minimax_h3_image' / 'torch_cache'),
            'PYTHONNOUSERSITE': '1',
            'PYTHONPATH': '',
            'TEMP': str(ROOT / 'temp'),
            'TMP': str(ROOT / 'temp'),
        })
        self.proc = subprocess.Popen(cmd, cwd=str(Path(main).parent), stdout=f, stderr=subprocess.STDOUT, env=env, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        f.close()

    def stop(self):
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
