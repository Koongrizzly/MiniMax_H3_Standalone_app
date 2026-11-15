"""Serialized image jobs; each job owns and releases its local backend."""
import json, random, sys, threading, time, uuid, os, shutil
from pathlib import Path
from .engine import ROOT, H3Error, Server, ensure_backend, generate


def shared_encoder(override=''):
    from runtime.validate_models import _resolve_override_or_scan
    from runtime.paths import TEXT_ENCODER_DIR, EXPECTED_TE
    path, error = _resolve_override_or_scan(override or None, TEXT_ENCODER_DIR, EXPECTED_TE, 'text encoder', 'text_encoder')
    if error or not path:
        raise H3Error(str(error or 'Install the MiniMax H3 text encoder first.'))
    return str(path)


def make_job(settings, models):
    d = dict(settings)
    if not str(d['prompt']).strip(): raise H3Error('Enter a prompt.')
    refs = list(d['references']) if d['mode'] == 'Edit' else []
    if d['mode'] == 'Edit' and not refs: raise H3Error('Add an image to edit.')
    if len(refs) > 16 or any(not Path(x).is_file() for x in refs): raise H3Error('Check reference images (maximum 16).')
    if not all(models.get(k) and Path(models[k]).is_file() for k in ('diffusion','encoder','vae')): raise H3Error('Install or select diffusion, text encoder and Image VAE in Images Settings.')
    py = Path(d['backend_python']); main = Path(d['backend_main'])
    if not py.is_file() or not main.is_file(): raise H3Error('Install MiniMax H3 Image from Validate install first.')
    jid = uuid.uuid4().hex
    seed = int(d['seed']) if int(d['seed']) >= 0 else random.randrange(2**32)
    prefix = ''.join(x if x.isalnum() or x in '_-' else '_' for x in d['filename_prefix'])[:80] or 'H3_Image'
    folder = Path(d['edit_output_dir'] if d['mode'] == 'Edit' else d['output_dir'])
    folder.mkdir(parents=True, exist_ok=True)
    output = folder / f'{prefix}_{time.strftime("%Y%m%d_%H%M%S")}_{seed}_{jid[:8]}.png'
    d.update(seed=seed, references=refs, models={k:str(Path(v).resolve()) for k,v in models.items()}, output=str(output.resolve()), job_id=jid)
    return dict(id=jid, job_type='h3_image', state='pending', created_at=time.time(), started_at=None, finished_at=None, elapsed=0, mode=-1, mode_name='Image '+d['mode'], model_label='H3 Image: '+Path(models['diffusion']).name, output=d['output'], seed=seed, resolution=f"{d['width']} × {d['height']}", frames=None, steps=d['steps'], prompt=d['prompt'], args=['-m','runtime.h3_image.queue_worker',jid], queue_program=str(py), progress=None, phase='Waiting', error='', settings=d, log_tail='', continue_last_result=False, manual_continue_video='', glue_results=False)


def load_queued_settings(job_id):
    # The persisted queue already owns the immutable snapshot. Passing its ID avoids
    # Windows command-line size limits for long prompts and 16 reference paths.
    queue = ROOT / 'presets' / 'setsave' / 'minimax_h3_queue.json'
    jobs = json.loads(queue.read_text(encoding='utf-8'))['jobs']
    job = next((j for j in jobs if j.get('id') == job_id and j.get('job_type') == 'h3_image'), None)
    if not job or job.get('state') != 'running': raise H3Error('Image job snapshot not found in the running queue.')
    d = job['settings']
    if d.get('job_id') != job_id or d.get('output') != job.get('output'): raise H3Error('Image job snapshot does not match the queue.')
    return d


def run(d):
    def log(text):
        print(text, flush=True)
        if text.startswith('Preparing image engine'): print('IMAGE_STAGE Preparing image engine', flush=True)
        elif text.startswith('Generating image'): print('IMAGE_STAGE Generating image', flush=True)
    server=Server(log); stop=threading.Event()
    target=Path(d['output']); target.parent.mkdir(parents=True,exist_ok=True)
    # Temporary output staging on the destination volume, followed by atomic publish.
    stage=target.parent / ('.h3_image_'+d['job_id'])
    shutil.rmtree(stage,ignore_errors=True)
    try:
        ensure_backend(server,d['backend_python'],d['backend_main'],d['backend_url'],d['lowvram'],log,stop,selected=d['models'])
        paths=generate(d['backend_url'],None,d['prompt'],d['seed'],d['width'],d['height'],{k:Path(v).name for k,v in d['models'].items()},d['references'],stage,'queued',log,stop,d.get('smart_framing',True),steps=d['steps'],enhanced_details=d.get('enhanced_details',False) and d['mode']=='Generate',detail_influence=d.get('detail_influence',0.25))
        if len(paths)!=1: raise H3Error('Expected one image output.')
        # SaveImage is PNG; reject unexpected output instead of mislabeling.
        if paths[0].suffix.lower() != '.png': raise H3Error('Expected PNG from SaveImage.')
        os.replace(paths[0],target)
        log('Saved: '+str(target))
    finally:
        try:
            print('IMAGE_STAGE Releasing image backend', flush=True)
            server.stop()
            if server.proc:
                try: server.proc.wait(timeout=20)
                except Exception: server.proc.kill(); server.proc.wait(timeout=10)
        finally:
            shutil.rmtree(stage,ignore_errors=True)

if __name__=='__main__':
    try: run(load_queued_settings(sys.argv[1]))
    except Exception as exc:
        print('ERROR: '+str(exc),flush=True); raise
