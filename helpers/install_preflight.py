"""Optional-component preflight for the GrizzlyMax Validate install action.

Keep application installs isolated; models are discovered rather than forcibly migrated.
"""
from __future__ import annotations
import json
import subprocess
from pathlib import Path
from .shared_whisper import shared_python, ct2_medium, cpp_medium
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QComboBox, QFileDialog, QMessageBox, QScrollArea, QWidget)

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / 'presets' / 'setsave' / 'install_preflight_paths.json'
BEAT = ROOT / 'BeatSyncFX'

def paths():
    try:
        value = json.loads(CONFIG.read_text(encoding='utf-8'))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}

def save_path(key, value):
    saved = paths(); saved[key] = str(value)
    CONFIG.parent.mkdir(parents=True, exist_ok=True)
    CONFIG.write_text(json.dumps(saved, indent=2), encoding='utf-8')

def beat_python():
    for candidate in (BEAT/'environments/beatsyncfx/Scripts/python.exe', BEAT/'environments/beatsyncfx/python.exe'):
        if candidate.is_file(): return candidate
    return None

def prompt_llama_runner():
    """Use the Prompt Builder's own preference location and bundled runner folder."""
    config = ROOT / 'presets/setsave/minimax_h3_prompt_builder_llm.json'
    try:
        data = json.loads(config.read_text(encoding='utf-8'))
        runner = Path(str(data.get('llama_runner', '')))
        if runner.is_file() and runner.name.lower() == 'llama-server.exe':
            return runner
    except (OSError, ValueError, TypeError):
        pass
    bundle = ROOT / 'presets/bin/llama'
    if bundle.is_dir():
        runner = bundle / 'llama-server.exe'
        if runner.is_file():
            return runner
        for runner in bundle.rglob('llama-server.exe'):
            if runner.is_file():
                return runner
    return None

def save_prompt_llama_runner(runner):
    """Configure the consuming Prompt Builder, not only the installer preflight."""
    config = ROOT / 'presets/setsave/minimax_h3_prompt_builder_llm.json'
    try:
        data = json.loads(config.read_text(encoding='utf-8'))
        if not isinstance(data, dict):
            data = {}
    except (OSError, ValueError):
        data = {}
    data['llama_runner'] = str(Path(runner).resolve())
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding='utf-8')

def media_state():
    overrides = paths()
    binroot = ROOT/'presets/bin'
    ffroot = Path(overrides.get('ffmpeg', str(binroot)))
    ff_ok = all((ffroot / name).is_file() for name in ('ffmpeg.exe','ffprobe.exe','ffplay.exe'))
    cpp_folder = ROOT/'presets/bin/whisper'
    cpp_exe = Path(overrides.get('whisper_cpp_exe', str(cpp_folder/'whisper-cli.exe')))
    cpp_model = Path(overrides.get('whisper_cpp_model', str(cpp_folder/'ggml-small.bin')))
    if not cpp_exe.is_file() and not overrides.get('whisper_cpp_exe') and cpp_folder.is_dir():
        cpp_exe = next(cpp_folder.rglob('whisper-cli.exe'), cpp_exe)
    cpp_ok = cpp_exe.is_file() and (cpp_model.is_file() or bool(cpp_medium()))
    music_ok = cpp_ok or ((cpp_folder/'ggml-small.bin').is_file() and any(cpp_folder.rglob('whisper-cli.exe')))
    medium = ct2_medium()
    beat_medium = bool(medium)
    beat_env = beat_python()
    llama = prompt_llama_runner()
    whisper_shared_ok = cpp_ok or music_ok
    return [
        dict(key='grizzly', title='GrizzlyMax environment', ok=(ROOT/'environments/.minimax_h3_int4/python.exe').is_file() or (ROOT/'environments/.minimax_h3_int4/Scripts/python.exe').is_file(), action='Install', note='GrizzlyMax root install.bat; existing models are preserved'),
        dict(key='ffmpeg', title='FFmpeg bundle', ok=ff_ok, action='Download', note='Needs ffmpeg.exe, ffprobe.exe and ffplay.exe in presets/bin'),
        dict(key='shared_cpp_whisper', title='Shared Whisper.cpp — Prompt Builder + Music Clip Creator', ok=whisper_shared_ok, action='Install', note='One shared whisper-cli.exe install for both tools; supports GGML/GGUF-compatible models and uses the existing Music Clip Creator whisper.cpp runtime when available'),
        dict(key='prompt_llama', title='Prompt Builder — llama.cpp GGUF backend', ok=bool(llama), action='Install', note='Optional local GGUF backend. Uses Prompt Builder CUDA llama.cpp bundle under presets/bin/llama or your selected llama-server.exe; GGUF model is chosen in Prompt Builder'),
        dict(key='beat', title='BeatSyncFX environment', ok=bool(beat_env and (BEAT/'helpers/gui.py').is_file()), action='Install', note='Separate portable BeatSyncFX environment; does not modify GrizzlyMax Python'),
        dict(key='beat_whisper', title='Shared Faster-Whisper Medium', ok=beat_medium and bool(shared_python()), action='Install Medium', note='Shared GrizzlyMax environments/.whisper and models/whisper; BeatSyncFX uses this when embedded'),
    ]

def _cmd_for(key):
    if key == 'grizzly': return ['cmd.exe','/c',str(ROOT/'install.bat')], ROOT
    if key == 'beat': return ['cmd.exe','/c',str(BEAT/'setup_windows.bat')], BEAT
    if key == 'ffmpeg':
        py = ROOT/'environments/.minimax_h3_int4/python.exe'
        if not py.is_file(): py = ROOT/'environments/.minimax_h3_int4/Scripts/python.exe'
        if not py.is_file(): return None, None
        return [str(py), str(ROOT/'presets/extra_env/install_ffmpeg_bundle.py')], ROOT
    if key == 'shared_cpp_whisper':
        script = ROOT/'presets/extra_env/install_shared_whisper_cpp.bat'
        if script.is_file(): return ['cmd.exe', '/c', str(script)], ROOT
        return None, None
    if key == 'prompt_llama':
        py = ROOT/'environments/.minimax_h3_int4/python.exe'
        if not py.is_file(): py = ROOT/'environments/.minimax_h3_int4/Scripts/python.exe'
        if not py.is_file(): return None, None
        return [str(py), str(ROOT/'presets/extra_env/install_prompt_llama.py')], ROOT
    if key == 'beat_whisper':
        script = ROOT/'presets/extra_env/setup_shared_whisper_medium.bat'
        if script.is_file(): return ['cmd.exe','/c',str(script)], ROOT
        return None, None
    return None, None

class PreflightDialog(QDialog):
    def __init__(self,parent=None):
        super().__init__(parent)
        self.setWindowTitle('GrizzlyMax — Validate installation')
        self.resize(780, 600)
        self.setMinimumWidth(600)
        outer = QVBoxLayout(self)
        intro=QLabel('Detected components are listed below. Choose Install, Locate or Skip for missing items. Existing installations are not overwritten.\nFor current MiniMax H3 INT4 and INT8 diffusion checkpoints, open the Settings tab and use its checkpoint selector/download feature.\nPrompt Builder llama.cpp is optional; you can use the built-in or Ollama backend instead.')
        intro.setWordWrap(True);outer.addWidget(intro)
        scroll=QScrollArea();scroll.setWidgetResizable(True);outer.addWidget(scroll,1)
        content=QWidget();self.rows=QVBoxLayout(content);scroll.setWidget(content)
        self.controls={}
        self.refresh()
        actions=QHBoxLayout();outer.addLayout(actions)
        refresh=QPushButton('Recheck');refresh.clicked.connect(self.refresh);actions.addWidget(refresh)
        install=QPushButton('Run selected actions');install.clicked.connect(self.install_selected);actions.addWidget(install)
        close=QPushButton('Close');close.clicked.connect(self.accept);actions.addWidget(close)

    def refresh(self):
        while self.rows.count():
            item=self.rows.takeAt(0); w=item.widget()
            if w: w.deleteLater()
        self.controls={}
        for record in media_state():
            line=QWidget();h=QHBoxLayout(line);h.setContentsMargins(2,4,2,4)
            label=QLabel(('[OK] ' if record['ok'] else '[MISSING] ')+record['title']+'\n'+record['note'])
            label.setWordWrap(True);h.addWidget(label,1)
            select=QComboBox()
            if record['ok']:
                choices=[('Installed — no action', 'none'), ('Locate / change path', 'locate'), ('Repair / reinstall', 'repair')]
            else:
                choices=[(record['action'], 'install'), ('Locate existing', 'locate'), ('Skip', 'none')]
            for label_text, action_id in choices:
                select.addItem(label_text, userData=action_id)
            select.setMinimumWidth(175)
            self.controls[record['key']]=(record,select)
            h.addWidget(select);self.rows.addWidget(line)
        self.rows.addStretch(1)

    def locate(self,key):
        if key=='ffmpeg':
            path=QFileDialog.getExistingDirectory(self,'Choose folder containing ffmpeg.exe + ffprobe.exe + ffplay.exe',str(ROOT/'presets/bin'))
            if path and all((Path(path)/name).is_file() for name in ('ffmpeg.exe','ffprobe.exe','ffplay.exe')):
                save_path('ffmpeg',path);return True
            if path: QMessageBox.warning(self,'Invalid FFmpeg folder','All three FFmpeg executables must be present.')
            return False
        if key == 'shared_cpp_whisper':
            exe,_=QFileDialog.getOpenFileName(self,'Locate whisper-cli.exe',str(ROOT/'presets/bin/whisper'),'Executables (*.exe)')
            if not exe:return False
            model,_=QFileDialog.getOpenFileName(self,'Locate GGML/GGUF Whisper model',str(Path(exe).parent),'Whisper models (*.bin *.gguf)')
            if not model:return False
            save_path('whisper_cpp_exe',exe);save_path('whisper_cpp_model',model);return True
        if key=='prompt_llama':
            exe,_=QFileDialog.getOpenFileName(self,'Locate existing llama-server.exe',str(ROOT/'presets/bin/llama'),'llama-server (llama-server.exe);;Executables (*.exe)')
            if not exe: return False
            if Path(exe).name.lower() != 'llama-server.exe' or not Path(exe).is_file():
                QMessageBox.warning(self,'Invalid llama.cpp executable','Select an existing llama-server.exe file.')
                return False
            save_prompt_llama_runner(exe)
            return True
        if key=='beat_whisper':
            folder=QFileDialog.getExistingDirectory(self,'Locate Faster-Whisper Medium model (model.bin + config.json)',str(ROOT/'models/whisper'))
            if folder and (Path(folder)/'model.bin').is_file() and (Path(folder)/'config.json').is_file():
                save_path('beat_whisper_medium',folder);return True
            if folder: QMessageBox.warning(self,'Invalid model folder','Expected model.bin and config.json.')
            return False
        QMessageBox.information(self,'Environment path','Application environments must use their portable install folders. Use Install or Repair for this component.')
        return False

    def install_selected(self):
        chosen=[(r,s.currentData()) for r,s in self.controls.values()]
        requested=[r['title'] for r, action in chosen if action in ('install', 'repair')]
        if requested:
            prompt='Start the following selected installation actions?\n\n' + '\n'.join('• '+x for x in requested)
            if QMessageBox.question(self, 'Confirm installation actions', prompt, QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
                return
        launched=[]
        for record,choice in chosen:
            key=record['key']
            if choice == 'locate':
                self.locate(key)
                continue
            if choice not in ('install', 'repair'): continue
            command,cwd=_cmd_for(key)
            if not command:
                QMessageBox.warning(self,'Installer unavailable',f'Installer for {record["title"]} cannot run yet. Install GrizzlyMax/BeatSyncFX core environment first, then validate again.')
                continue
            try:
                subprocess.Popen(command, cwd=str(cwd), creationflags=getattr(subprocess, 'CREATE_NEW_CONSOLE', 0))
                launched.append(record['title'])
            except OSError as exc: QMessageBox.warning(self,'Installer error',f'{record["title"]}: {exc}')
        self.refresh()
        if launched:
            QMessageBox.information(self,'Installers launched','Installation windows opened for:\n'+ '\n'.join(launched)+'\n\nCheck each installer window for success or errors, then click Recheck.\nWhisper.cpp diagnostics: logs/install_shared_whisper_cpp.log')
