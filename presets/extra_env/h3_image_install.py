"""Portable, non-destructive installer. Never removes existing ComfyUI or model files."""
import os,sys,subprocess,urllib.request,zipfile,shutil,shlex,argparse,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
MODEL=ROOT/'models'/'minimax_h3_image'
COMFY=MODEL/'comfyui_backend'
LEGACY_COMFY=ROOT/'vendor'/'ComfyUI'
UV=ROOT/'presets'/'extra_env'/'uv.exe'
ENV=ROOT/'environments'/'h3_image_comfy'
HF='https://huggingface.co/Comfy-Org/MiniMax-H3-Image/resolve/main/'
FILES={
 'diffusion_models/minimax_h3_image_pruned_w4a8.safetensors': 'diffusion_models',
 'diffusion_models/minimax_h3_image_pruned_int8_convrot.safetensors': 'diffusion_models',
 'text_encoders/qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors': 'text_encoders',
 'text_encoders/qwen3vl_32b_minimax_h3_int8_convrot.safetensors': 'text_encoders',
 'vae/minimax_h3_image_vae_fp16.safetensors': 'vae',
}
def run(*args, env=None):
 print('>', ' '.join(map(str,args)), flush=True)
 subprocess.run([str(x) for x in args],cwd=ROOT,check=True,env=env)
def _size(n):
 for unit in ('B','KB','MB','GB','TB'):
  if n < 1024 or unit == 'TB': return f'{n:.1f} {unit}'
  n /= 1024

def download(url,dest):
 dest.parent.mkdir(parents=True,exist_ok=True)
 print(f'\n[DOWNLOAD] {dest.name}',flush=True)
 if dest.exists() and dest.stat().st_size > 1024:
  print(f'[SKIP] Existing file: {dest} ({_size(dest.stat().st_size)})',flush=True)
  return
 part=dest.with_suffix(dest.suffix+'.partial')
 start=part.stat().st_size if part.exists() else 0
 if start: print(f'[RESUME] Already downloaded: {_size(start)}',flush=True)
 request=urllib.request.Request(url,headers={
  'User-Agent':'GrizzlyMax-H3-Image/1.0',
  **({'Range':f'bytes={start}-'} if start else {})})
 print('[URL]',url,flush=True)
 try:
  with urllib.request.urlopen(request,timeout=120) as resp:
   resumed=(start > 0 and resp.status == 206)
   if start and not resumed:
    print('[INFO] Server did not accept resume; restarting this file.',flush=True)
   offset=start if resumed else 0
   length=int(resp.headers.get('Content-Length') or '0')
   total=offset+length if length else 0
   print(f'[SIZE] {_size(total) if total else "Unknown (showing received bytes)"}',flush=True)
   mode='ab' if resumed else 'wb'
   with open(part,mode) as f:
    received=offset
    last_print=0
    last_time=time.monotonic()
    last_bytes=received
    while True:
     chunk=resp.read(1024*1024)
     if not chunk: break
     f.write(chunk)
     received+=len(chunk)
     now=time.monotonic()
     if now-last_print>=1.0:
      speed=(received-last_bytes)/max(now-last_time,0.001)
      pct=f'{100*received/total:5.1f}%' if total else ' --.-%'
      print(f'\r  {pct} | {_size(received)} / {_size(total) if total else "?"} | {_size(speed)}/s',end='',flush=True)
      last_print=now;last_time=now;last_bytes=received
    print(f'\r  100.0% | {_size(received)} received.                         ',flush=True)
    if total and received!=total:
     raise IOError(f'Incomplete download: {_size(received)} of {_size(total)}. Partial file preserved for retry.')
 except Exception:
  print(f'\n[ERROR] Download interrupted. Retry presets/install_h3_image.bat to resume from {part.name}',flush=True)
  raise
 part.replace(dest)
 print(f'[SAVED] {dest}',flush=True)

def get_comfy():
 if (COMFY/'main.py').exists():print('Reusing existing ComfyUI:',COMFY);return
 archive=ROOT/'temp'/'h3_image_comfy_source.zip'
 download('https://github.com/comfyanonymous/ComfyUI/archive/refs/heads/master.zip',archive)
 import tempfile
 with tempfile.TemporaryDirectory(dir=ROOT/'temp') as td:
  with zipfile.ZipFile(archive) as z:z.extractall(td)
  folders=[p for p in Path(td).iterdir() if (p/'main.py').exists()]
  if not folders:raise RuntimeError('ComfyUI archive missing main.py')
  COMFY.parent.mkdir(parents=True,exist_ok=True)
  shutil.copytree(folders[0], COMFY, dirs_exist_ok=True)
CUDA_INDEX = 'https://download.pytorch.org/whl/cu130'
TORCH_PINS = ('torch==2.14.1+cu130', 'torchvision==0.29.1+cu130')

def backend():
 get_comfy()
 py=ENV/'Scripts'/'python.exe'
 if not py.is_file():
  run(UV,'venv','--python','3.11',ENV)
 print('[CUDA] Installing PyTorch 2.14.1 / CUDA 13.0 in the H3 Image environment.',flush=True)
 # Exact local-version pins replace cu126 even if the public version is identical.
 run(UV,'pip','install','--python',py,*TORCH_PINS,'--index-url',CUDA_INDEX)
 # Keep the CUDA wheel variants pinned during the backend dependency resolution.
 constraints=ROOT/'presets'/'extra_env'/'h3_image_cuda_constraints.txt'
 run(UV,'pip','install','--python',py,'-r',COMFY/'requirements.txt','-c',constraints,'--extra-index-url',CUDA_INDEX)
 run(UV,'pip','install','--python',py,'Pillow>=10.4,<13')
 run(UV,'pip','check','--python',py)
 print('[CUDA] Checking installed build and running a GPU operation...',flush=True)
 run(py,'-c',"import torch, torchvision; print('PyTorch:', torch.__version__, 'TorchVision:', torchvision.__version__, 'CUDA:', torch.version.cuda, flush=True); assert torch.version.cuda == '13.0', 'Expected CUDA 13.0 PyTorch'; assert torch.cuda.is_available(), 'CUDA unavailable: check NVIDIA driver and GPU compatibility'; x=torch.ones(8,device='cuda'); assert x.sum().item()==8; print('GPU:',torch.cuda.get_device_name(0),'| CUDA operation passed',flush=True)")
 print('ComfyUI backend installed at',COMFY,flush=True)
 print('[NEXT] Checking requested model downloads.',flush=True)
def main():
 ap=argparse.ArgumentParser()
 ap.add_argument('--backend',action='store_true',help='Install portable ComfyUI backend')
 ap.add_argument('--models',choices=['none','w4a8','int8'],default='none',help='Optionally download large model weights')
 ap.add_argument('--encoder',choices=['none','nvfp4','int8'],default='none')
 ap.add_argument('--vae', action='store_true', help='Download Image VAE independently')
 ap.add_argument('--no-auto-vae', action='store_true', help='Do not include VAE with diffusion download')
 args=ap.parse_args()
 for name in ('helpers','environments','models','presets/setsave','output/images','output/edits','runtime/h3_image','logs','temp','assets'):(ROOT/name).mkdir(parents=True,exist_ok=True)
 if args.backend:backend()
 desired=['vae/minimax_h3_image_vae_fp16.safetensors'] if args.vae or (args.models!='none' and not args.no_auto_vae) else []
 if args.models=='w4a8':desired.append('diffusion_models/minimax_h3_image_pruned_w4a8.safetensors')
 if args.models=='int8':desired.append('diffusion_models/minimax_h3_image_pruned_int8_convrot.safetensors')
 if args.encoder=='nvfp4':desired.append('text_encoders/qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors')
 if args.encoder=='int8':desired.append('text_encoders/qwen3vl_32b_minimax_h3_int8_convrot.safetensors')
 print(f'[MODELS] {len(desired)} file(s) to check/download',flush=True)
 for i,rel in enumerate(desired,1):
  print(f'[MODEL {i}/{len(desired)}] {rel}',flush=True)
  download(HF+rel,MODEL/rel)
 print('Install complete. Models are optional and can be reused from elsewhere via Comfy extra_model_paths.')
 print('For a new backend run presets/install_h3_image.bat --backend --models w4a8 --encoder nvfp4')
 print('H3 Image uses an internal workflow and automatically starts its image engine when generating.')
if __name__=='__main__':
 try:main()
 except Exception as exc:print('INSTALL ERROR:',exc,file=sys.stderr);sys.exit(1)
