"""Install NVIDIA's public sample and Python requirements. Proprietary VFX SDK assets remain NVIDIA-managed."""
import sys,subprocess,zipfile,urllib.request,shutil,tempfile,os
from pathlib import Path
_APP_ROOT = Path(__file__).resolve().parents[2]
if str(_APP_ROOT) not in sys.path: sys.path.insert(0,str(_APP_ROOT))
from models.nvidia_vsr.runtime.core import ROOT,repo,python_env

def say(s):print(s,flush=True)
def run(args,cwd=None):
    say('> '+subprocess.list2cmdline([str(x) for x in args]))
    p=subprocess.Popen([str(x) for x in args],cwd=cwd,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,errors='replace',bufsize=1)
    for line in p.stdout:say(line.rstrip())
    if p.wait():raise RuntimeError('Command failed with exit '+str(p.returncode))
def main():
    say('STAGE 1/5: Checking Python')
    if sys.version_info<(3,10):raise RuntimeError('Python 3.10+ required.')
    say('STAGE 2/5: Downloading NVIDIA official sample')
    if not (repo()/'video_super_resolution.py').is_file():
        url='https://github.com/NVIDIA-Maxine/nvidia-vfx-python-samples/archive/refs/heads/main.zip'
        with tempfile.TemporaryDirectory() as td:
            z=Path(td)/'repo.zip';urllib.request.urlretrieve(url,z)
            with zipfile.ZipFile(z) as archive:
                for member in archive.infolist():
                    target=(Path(td)/'unpack'/member.filename).resolve()
                    if not target.is_relative_to((Path(td)/'unpack').resolve()):raise RuntimeError('Unsafe archive member')
                archive.extractall(Path(td)/'unpack')
            source=Path(td)/'unpack/nvidia-vfx-python-samples-main'
            repo().parent.mkdir(parents=True,exist_ok=True)
            shutil.copytree(source,repo(),dirs_exist_ok=True)
    say('STAGE 3/5: Creating isolated environment')
    py=python_env()
    if not py.exists():run([sys.executable,'-m','venv',str(py.parent.parent)])
    say('STAGE 4/5: Installing requirements (may download several GB)')
    run([py,'-m','pip','install','--upgrade','pip'])
    run([py,'-m','pip','install','-r',repo()/'requirements.txt'])
    say('STAGE 5/5: Checking installed dependencies')
    run([py,'-m','pip','check'])
    say('INSTALL_COMPLETE: Python environment and sample installed.')
    say('NOTICE: NVIDIA VFX SDK proprietary runtime/models may require a separate NVIDIA SDK installation, model download, or license acceptance. An initial test will confirm runtime readiness.')
if __name__=='__main__':
    try:main()
    except Exception as e:say('INSTALL_FAILED: '+str(e));sys.exit(1)
