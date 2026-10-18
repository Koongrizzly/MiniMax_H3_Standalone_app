from pathlib import Path
import json, subprocess, sys, shutil, os
ROOT=Path(__file__).resolve().parents[2]
DIRS=['presets/setsave','presets/bin','logs','output','assets','models','environments','runtime','presets/extra_env']
for d in DIRS:(ROOT/d).mkdir(parents=True,exist_ok=True)
CONFIG=ROOT/'presets/setsave/maxine_vsr.json'
DEFAULT={'input':'','output':'','scale':2,'quality':'HIGH','strength':0.5,'python':'','ffmpeg':'','ffprobe':''}
def load():
    try:return {**DEFAULT,**json.loads(CONFIG.read_text(encoding='utf-8'))}
    except (OSError,ValueError):return DEFAULT.copy()
def save(data):CONFIG.write_text(json.dumps(data,indent=2),encoding='utf-8')
def python_env():return ROOT/'environments/maxine_vfx/Scripts/python.exe' if os.name=='nt' else ROOT/'environments/maxine_vfx/bin/python'
def repo():return ROOT/'models/nvidia_vsr/nvidia-vfx-python-samples'
def find_exe(name,extra=''):
    p=ROOT/'presets/bin'/f'{name}.exe'
    if p.is_file():return str(p)
    if extra and Path(extra).is_file():return extra
    return shutil.which(name) or ''
def run_capture(cmd):
    return subprocess.run(cmd,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,errors='replace').stdout
