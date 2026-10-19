"""Wrapper around the official NVIDIA sample: exact output height, audio preservation, and progress logs."""
import argparse,subprocess,sys,os
from pathlib import Path
_APP_ROOT = Path(__file__).resolve().parents[2]
if str(_APP_ROOT) not in sys.path: sys.path.insert(0,str(_APP_ROOT))
from runtime.nvidia_vsr.core import ROOT,repo,python_env,find_exe,run_capture

def log(s):print(s,flush=True)
def execute(command):
    log('COMMAND: '+subprocess.list2cmdline([str(x) for x in command]))
    p=subprocess.Popen([str(x) for x in command],stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,errors='replace',bufsize=1)
    for line in p.stdout:log(line.rstrip())
    result=p.wait()
    if result:raise RuntimeError('Process exited with code '+str(result))
def dimensions(ffprobe,path):
    command=[ffprobe,'-v','error','-select_streams','v:0','-show_entries','stream=width,height','-of','csv=p=0:s=x',str(path)]
    value=run_capture(command).strip().splitlines()[0]
    w,h=[int(i) for i in value.split('x')]
    if w<1 or h<1:raise ValueError('Invalid source dimensions')
    return w,h

def main():
    p=argparse.ArgumentParser()
    for key in ('input','output','scale','quality','strength','ffmpeg','ffprobe'):p.add_argument('--'+key,required=True)
    p.add_argument('--cleanup',choices=('NONE','DEBLUR_ULTRA','DENOISE_ULTRA'),default='NONE')
    p.add_argument('--cleanup-strength',type=float,default=1.0)
    args=p.parse_args()
    inp=Path(args.input).resolve();out=Path(args.output).resolve()
    if not inp.is_file():raise FileNotFoundError(inp)
    if inp==out:raise ValueError('Input and output cannot be identical')
    if out.suffix.lower()!='.mp4':raise ValueError('Output must be MP4')
    out.parent.mkdir(parents=True,exist_ok=True)
    w,h=dimensions(args.ffprobe,inp)
    scale=int(args.scale)
    if scale not in (1,2,3,4):raise ValueError('Native scale must be 1, 2, 3 or 4')
    if not 0.0 <= args.cleanup_strength <= 1.0:raise ValueError('Cleanup strength must be between 0 and 1')
    temp=ROOT/'output'/f'__maxine_work_{os.getpid()}.mp4'
    cleanup_temp=ROOT/'output'/f'__maxine_cleanup_{os.getpid()}.mp4'
    try:
        script=repo()/'video_super_resolution.py'
        if not script.is_file() or not python_env().is_file():raise RuntimeError('Install NVIDIA dependencies first on Advanced tab')
        log(f'VSR_STAGE: NVIDIA AI upscale {scale}x, original {w}x{h}, result {w*scale}x{h*scale}')
        execute([python_env(),'-u',script,'-i',inp,'-o',temp,'--scale',str(scale),'--quality',args.quality,'--strength',args.strength])
        video_to_finish=temp
        if args.cleanup!='NONE':
            log(f'VSR_STAGE: additional {args.cleanup} pass (same resolution)')
            execute([python_env(),'-u',script,'-i',temp,'-o',cleanup_temp,'--scale','1','--quality',args.cleanup,'--strength',str(args.cleanup_strength)])
            video_to_finish=cleanup_temp
        log('VSR_STAGE: preserving native output pixels and original audio')
        command=[args.ffmpeg,'-hide_banner','-y','-i',str(video_to_finish),'-i',str(inp),'-map','0:v:0','-map','1:a?','-c:v','copy','-c:a','copy','-movflags','+faststart',str(out)]
        execute(command)
        if not out.is_file() or out.stat().st_size<1024:raise RuntimeError('Output missing or empty')
        log('JOB_COMPLETE: '+str(out))
    finally:
        for temporary in (temp,cleanup_temp):
            if temporary.is_file():
                try:temporary.unlink()
                except OSError:pass
if __name__=='__main__':
    try:main()
    except Exception as e:log('JOB_FAILED: '+str(e));sys.exit(1)
