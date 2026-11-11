"""Install native ComfyUI RelayVSR in an isolated portable environment.

Does not touch the GrizzlyMax vendored ComfyUI, MiniMax environments or
user settings other than the RelayVSR command stored in JSON.
"""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from urllib.request import urlretrieve
import zipfile

ROOT = Path(__file__).resolve().parents[2]
ENV = ROOT / 'environments' / 'relayvsr'
PYTHON = ENV / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
COMFY = ROOT / 'models' / 'relayvsr' / 'native' / 'native_comfy'
OLD_COMFY = ROOT / 'runtime' / 'relayvsr' / 'native_comfy'
CONFIG = ROOT / 'presets' / 'setsave' / 'relayvsr.json'
MODELS = ROOT / 'models' / 'relayvsr' / 'native'
REPO_URL = 'https://github.com/Comfy-Org/ComfyUI/archive/2f89433.zip'
# Paired official CUDA wheels; never let the default PyPI index select CPU Torch.
CUDA_INDEX = 'https://download.pytorch.org/whl/cu130'
TORCH_VERSION = '2.11.0+cu130'
TORCHVISION_VERSION = '0.26.0+cu130'
CUDA_VERIFY = (
    'import torch,torchvision; '
    'print("Torch:", torch.__version__, "Torchvision:", torchvision.__version__, '
    '"CUDA build:", torch.version.cuda, "CUDA available:", torch.cuda.is_available(), flush=True); '
    'assert torch.version.cuda is not None and "+cu" in torch.__version__, '
    '"CPU-only PyTorch detected; CUDA build is required"; '
    'assert torch.cuda.is_available(), "No working NVIDIA CUDA device detected"; '
    'assert "+cu" in torchvision.__version__, "CPU-only Torchvision detected"'
)


def verify_cuda():
    run([PYTHON, '-c', CUDA_VERIFY])


def install_cuda_pair():
    say('Installing pinned CUDA PyTorch + Torchvision (CPU wheels forbidden)')
    run([PYTHON, '-m', 'pip', 'install', '--upgrade',
         'torch==' + TORCH_VERSION, 'torchvision==' + TORCHVISION_VERSION,
         '--index-url', CUDA_INDEX])
    verify_cuda()

SPECS = {
    2: [('diffusion_models/relayvsr_x2_lightx2v_int8_convrot.safetensors', 'diffusion_models'),
        ('vae/relayvsr_flash_vae_x2_fp32.safetensors', 'vae')],
    4: [('diffusion_models/relayvsr_x4_int8_convrot.safetensors', 'diffusion_models'),
        ('vae/relayvsr_flash_vae_x4_fp32.safetensors', 'vae')],
}


def say(message):
    print('[RelayVSR] ' + str(message), flush=True)


def run(command):
    say('> ' + subprocess.list2cmdline([str(x) for x in command]))
    with subprocess.Popen([str(x) for x in command], cwd=str(ROOT), stdout=subprocess.PIPE,
                          stderr=subprocess.STDOUT, text=True, errors='replace', bufsize=1) as proc:
        for line in proc.stdout:
            say(line.rstrip())
        code = proc.wait()
    if code:
        raise RuntimeError('Command failed (exit %d)' % code)


def safe_unpack(archive, target):
    target = target.resolve()
    for member in archive.infolist():
        resolved = (target / member.filename).resolve()
        if not resolved.is_relative_to(target):
            raise RuntimeError('Unsafe repository archive path')
    archive.extractall(target)


def _valid_comfy(folder):
    return (folder/'main.py').is_file() and (folder/'requirements.txt').is_file() and \
           (folder/'comfy/ldm/wan/model_vsr.py').is_file()


def install_comfy():
    # Backend source is installed data, not a GitHub-distributed runtime asset.
    # Existing users can migrate the previously downloaded source without a re-download.
    if _valid_comfy(COMFY):
        say('Native RelayVSR ComfyUI source already installed in models/relayvsr/native/native_comfy')
        return
    if _valid_comfy(OLD_COMFY):
        if COMFY.exists():
            raise RuntimeError('Destination already exists but is incomplete: '+str(COMFY)+
                               '. Refusing to overwrite it while migrating.')
        COMFY.parent.mkdir(parents=True, exist_ok=True)
        say('Moving previously downloaded ComfyUI source from runtime/ to models/')
        shutil.move(str(OLD_COMFY), str(COMFY))
        if not _valid_comfy(COMFY):
            raise RuntimeError('Native backend migration failed integrity checks: '+str(COMFY))
        say('Backend migration complete; no source download required')
        return
    if COMFY.exists():
        raise RuntimeError('Incomplete ComfyUI backend folder: '+str(COMFY)+
                           '. Refusing to overwrite existing files; inspect or relocate this folder first.')
    # Fresh setup: pull the pinned source at install time. Never bundle or commit it.
    with tempfile.TemporaryDirectory() as temporary:
        temp = Path(temporary)
        source = temp/'native.zip'
        say('Downloading pinned ComfyUI RelayVSR source (commit 2f89433) into models/')
        urlretrieve(REPO_URL, source)
        with zipfile.ZipFile(source) as archive:
            safe_unpack(archive, temp/'extract')
        directories = [d for d in (temp/'extract').iterdir() if _valid_comfy(d)]
        if len(directories) != 1:
            raise RuntimeError('Downloaded ComfyUI does not contain required native RelayVSR files')
        COMFY.parent.mkdir(parents=True, exist_ok=True)
        # Copy to a sibling staging folder, then atomically rename on success.
        staging = COMFY.with_name(COMFY.name+'.installing')
        if staging.exists():
            raise RuntimeError('Previous incomplete backend staging folder: '+str(staging))
        try:
            shutil.copytree(directories[0], staging)
            if not _valid_comfy(staging):
                raise RuntimeError('Downloaded ComfyUI failed staging validation')
            staging.rename(COMFY)
        except Exception:
            if staging.exists():
                shutil.rmtree(staging)
            raise
        say('ComfyUI source installed under models/; no ComfyUI files are bundled in GitHub release')


def fetch_model(repo_path, subfolder):
    dest = MODELS/subfolder/Path(repo_path).name
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.is_file() and dest.stat().st_size > 50_000_000:
        say('Using existing native file: '+str(dest))
        return
    say('Downloading Comfy-Org/RelayVSR: '+repo_path)
    # Use HF resumable model cache, then materialize into portable model folder.
    command = (
        'from huggingface_hub import hf_hub_download;import sys;'
        'print(hf_hub_download(repo_id="Comfy-Org/RelayVSR",filename=sys.argv[1],local_dir=sys.argv[2]))'
    )
    run([PYTHON, '-c', command, repo_path, str(MODELS)])
    if not dest.is_file() or dest.stat().st_size <= 50_000_000:
        raise RuntimeError('Native model missing or incomplete: '+str(dest))


def save_config():
    CONFIG.parent.mkdir(parents=True, exist_ok=True)
    try:
        data = json.loads(CONFIG.read_text(encoding='utf-8'))
        if not isinstance(data, dict): data = {}
    except (OSError, ValueError):
        data = {}
    new_command = [str(PYTHON), str(ROOT/'runtime/relayvsr/native_inference.py'),
                   '--input','{input}','--output','{output}','--scale','{scale}']
    old_command = data.get('command', [])
    # Migrate only the formerly generated bbaudio direct runner. Respect custom commands.
    if (not old_command or
        (isinstance(old_command, list) and any('relayvsr/direct_inference.py' in x.replace('\\','/')
                                                   for x in old_command if isinstance(x,str)))):
        data['command'] = new_command
    elif isinstance(old_command, list) and any('native_inference.py' in x for x in old_command if isinstance(x,str)):
        data['command'] = new_command
    else:
        say('Custom RelayVSR command left unchanged; choose native runner manually if intended')
    temp = CONFIG.with_suffix('.tmp')
    temp.write_text(json.dumps(data, indent=2), encoding='utf-8')
    os.replace(temp, CONFIG)


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--models', choices=['2','4','both','none'], default='4',
                        help='X4 INT8 is the default and supports both X4 and X2 output. Legacy X2 choice maps to X4.')
    args=parser.parse_args()
    say('STAGE 1/5: Isolated Python environment')
    if not PYTHON.is_file():
        run([sys.executable, '-m', 'venv', str(ENV)])
    run([PYTHON, '-m', 'pip', 'install', '--upgrade', 'pip'])
    say('STAGE 2/5: Native ComfyUI source')
    install_comfy()
    say('STAGE 3/5: Install native ComfyUI dependencies (CUDA-only)')
    # Install official matching CUDA wheels first. Constraints prevent ComfyUI's
    # unpinned `torch` and `torchvision` from replacing them with CPU wheels.
    install_cuda_pair()
    with tempfile.TemporaryDirectory() as td:
        constraints = Path(td)/'cuda_constraints.txt'
        constraints.write_text('torch==' + TORCH_VERSION + '\n' +
                               'torchvision==' + TORCHVISION_VERSION + '\n', encoding='utf-8')
        run([PYTHON, '-m', 'pip', 'install', '-r', str(COMFY/'requirements.txt'),
             '-c', str(constraints)])
        run([PYTHON, '-m', 'pip', 'install', 'av', 'huggingface_hub',
             'comfy-kitchen', 'comfy-aimdo', 'psutil', '-c', str(constraints)])
    # A failed CUDA check aborts installation before downloading model files
    # or writing INSTALL_COMPLETE. Repair existing CPU-only environments.
    verify_cuda()
    say('STAGE 4/5: Native INT8 model and matching Flash VAE')
    choices=[] if args.models=='none' else [4]
    say('Using X4 INT8 checkpoint for both X4 and X2 outputs (native X2 weights are not downloaded).')
    for scale in choices:
        for remote, subfolder in SPECS[scale]:
            fetch_model(remote, subfolder)
    say('STAGE 5/5: Verify native node source, model files and queue integration')
    verify=ROOT/'runtime/relayvsr/native_inference.py'
    verify_cuda()
    run([PYTHON, str(verify), '--verify-imports'])
    run([PYTHON, '-m', 'pip', 'check'])
    save_config()
    say('INSTALL_COMPLETE: native ComfyUI + CUDA PyTorch + Comfy-Org INT8 / Flash VAE installed (inference not tested)')


if __name__=='__main__':
    try: main()
    except Exception as exc:
        say('INSTALL_FAILED: '+str(exc))
        sys.exit(1)
