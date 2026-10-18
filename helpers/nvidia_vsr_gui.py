import sys,os,datetime,subprocess
from pathlib import Path
_APP_ROOT = Path(__file__).resolve().parents[1]
if str(_APP_ROOT) not in sys.path: sys.path.insert(0,str(_APP_ROOT))
from PySide6.QtCore import Qt,QProcess,QTimer
from PySide6.QtWidgets import (QApplication,QMainWindow,QWidget,QVBoxLayout,QHBoxLayout,QTabWidget,QScrollArea,QLabel,QLineEdit,QPushButton,QFileDialog,QComboBox,QDoubleSpinBox,QTextEdit,QProgressBar,QMessageBox,QGroupBox,QFormLayout,QCheckBox)
from runtime.nvidia_vsr.core import ROOT,CONFIG,load,save,repo,python_env,find_exe,run_capture

class NoWheelCombo(QComboBox):
    def wheelEvent(self,event):event.ignore()
class NoWheelSpin(QDoubleSpinBox):
    def wheelEvent(self,event):event.ignore()
class Window(QMainWindow):
    def __init__(self):
        super().__init__();self.setWindowTitle('NVIDIA Maxine VSR — Standalone Test');self.resize(870,690)
        self.settings=load();self.process=None;self.task='';self.logfile=None
        tabs=QTabWidget();self.setCentralWidget(tabs)
        tabs.addTab(self.make_work(),'Upscaler');tabs.addTab(self.make_advanced(),'Advanced / Install / Logs')
        self.tabs=tabs;self.update_status();self.append('GUI ready. NVIDIA sample and SDK are not bundled.')
    def scroll(self):
        area=QScrollArea();area.setWidgetResizable(True);area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        inner=QWidget();lay=QVBoxLayout(inner);lay.setContentsMargins(18,18,18,18);lay.setSpacing(14)
        area.setWidget(inner);return area,lay
    def line_row(self,label,key,directory=False):
        row=QHBoxLayout(); edit=QLineEdit(self.settings.get(key,''));button=QPushButton('Browse…')
        def browse():
            if directory:
                path=QFileDialog.getSaveFileName(self,'Save output',str(ROOT/'output'/'upscaled.mp4'),'MP4 video (*.mp4)')[0]
            else:path=QFileDialog.getOpenFileName(self,'Choose source video',str(ROOT),'Video (*.mp4 *.mov *.mkv *.webm *.avi)')[0]
            if path:
                edit.setText(path)
                if key=='input':self.update_dimensions()
        button.clicked.connect(browse);row.addWidget(edit,1);row.addWidget(button)
        edit.editingFinished.connect(self.save_settings)
        setattr(self,key+'_edit',edit)
        return label,row
    def make_work(self):
        area,layout=self.scroll()
        title=QLabel('NVIDIA RTX Video Super Resolution');title.setStyleSheet('font-size:22px;font-weight:700');layout.addWidget(title)
        layout.addWidget(QLabel('Test native 2×, 3× or 4× NVIDIA AI upscaling, preserving the original aspect ratio.'))
        box=QGroupBox('Source and output');form=QFormLayout(box)
        for label,key,directory in [('Input video','input',False),('Output MP4','output',True)]:
            caption,row=self.line_row(label,key,directory);form.addRow(caption,row)
        layout.addWidget(box)
        options=QGroupBox('Upscale options');f=QFormLayout(options)
        self.res=NoWheelCombo();self.res.addItems(['2×','3×','4×']);self.res.setCurrentIndex(max(0,min(2,int(self.settings.get('scale',2))-2)));f.addRow('Native scale:',self.res)
        self.dimensions_label=QLabel('Select an input video to calculate the output resolution.');self.dimensions_label.setWordWrap(True);f.addRow('Output dimensions:',self.dimensions_label)
        self.quality=NoWheelCombo();self.quality.addItems(['BICUBIC','LOW','MEDIUM','HIGH','ULTRA','STREAMING_MEDIUM','STREAMING_ULTRA']);self.quality.setCurrentText(self.settings['quality']);f.addRow('NVIDIA quality:',self.quality)
        self.strength=NoWheelSpin();self.strength.setRange(0,1);self.strength.setDecimals(2);self.strength.setSingleStep(.05);self.strength.setValue(float(self.settings['strength']));f.addRow('AI strength:',self.strength)
        layout.addWidget(options)
        self.res.currentTextChanged.connect(self.update_dimensions);self.res.currentTextChanged.connect(self.save_settings);self.quality.currentTextChanged.connect(self.save_settings);self.strength.valueChanged.connect(self.save_settings)
        info=QLabel('NVIDIA native scale is applied directly, with no additional Lanczos resizing. The original audio is retained when available.');info.setWordWrap(True);layout.addWidget(info)
        row=QHBoxLayout();self.start=QPushButton('Start upscale');self.stop=QPushButton('Cancel');self.stop.setEnabled(False)
        row.addWidget(self.start);row.addWidget(self.stop);self.start.clicked.connect(self.start_job);self.stop.clicked.connect(self.cancel);layout.addLayout(row)
        self.work_state=QLabel('Idle');layout.addWidget(self.work_state)
        self.work_progress=QProgressBar();self.work_progress.setRange(0,100);self.work_progress.setValue(0);layout.addWidget(self.work_progress)
        self.input_edit.editingFinished.connect(self.update_dimensions)
        self.update_dimensions()
        layout.addStretch();return area
    def make_advanced(self):
        area,layout=self.scroll()
        heading=QLabel('Installation, diagnostics and logs');heading.setStyleSheet('font-size:22px;font-weight:700');layout.addWidget(heading)
        self.status=QLabel();self.status.setWordWrap(True);layout.addWidget(self.status)
        row=QHBoxLayout();self.install=QPushButton('Install / Repair Python + Sample');self.verify=QPushButton('Verify dependencies');row.addWidget(self.install);row.addWidget(self.verify);layout.addLayout(row)
        self.install.clicked.connect(self.start_install);self.verify.clicked.connect(self.verify_setup)
        self.sdk_note=QLabel('NVIDIA proprietary VFX SDK/models: the installer handles the public Python sample and its pip requirements. SDK runtime/models may require acceptance or downloads through NVIDIA. Check official SDK instructions if model loading fails.');self.sdk_note.setWordWrap(True);layout.addWidget(self.sdk_note)
        sdk=QPushButton('Open official NVIDIA VFX SDK documentation');sdk.clicked.connect(lambda:__import__('webbrowser').open('https://docs.nvidia.com/maxine/vfx/latest/'));layout.addWidget(sdk)
        binbox=QGroupBox('Tools');f=QFormLayout(binbox)
        self.ffmpeg=QLineEdit(self.settings.get('ffmpeg',''));self.ffprobe=QLineEdit(self.settings.get('ffprobe',''))
        f.addRow('FFmpeg override (optional):',self.ffmpeg);f.addRow('FFprobe override (optional):',self.ffprobe)
        self.ffmpeg.editingFinished.connect(self.save_settings);self.ffprobe.editingFinished.connect(self.save_settings)
        layout.addWidget(binbox)
        self.progress=QProgressBar();self.progress.setRange(0,100);self.progress.setValue(0);layout.addWidget(self.progress)
        self.progress_label=QLabel('No task running');layout.addWidget(self.progress_label)
        row=QHBoxLayout();clear=QPushButton('Clear displayed log');open_logs=QPushButton('Open logs folder');row.addWidget(clear);row.addWidget(open_logs);layout.addLayout(row)
        self.logs=QTextEdit();self.logs.setReadOnly(True);self.logs.setMinimumHeight(270);layout.addWidget(self.logs)
        clear.clicked.connect(self.logs.clear);open_logs.clicked.connect(lambda:os.startfile(ROOT/'logs') if os.name=='nt' else None)
        return area
    def update_dimensions(self, *_):
        if not hasattr(self,'dimensions_label'):return
        source=Path(self.input_edit.text().strip())
        if not source.is_file():
            self.dimensions_label.setText('Select an existing input video to calculate output size.')
            return
        ffprobe=find_exe('ffprobe',self.ffprobe.text() if hasattr(self,'ffprobe') else self.settings.get('ffprobe',''))
        if not ffprobe:
            self.dimensions_label.setText('Install ffprobe.exe in presets/bin/ to detect video dimensions.')
            return
        try:
            result=run_capture([ffprobe,'-v','error','-select_streams','v:0','-show_entries','stream=width,height','-of','csv=p=0:s=x',str(source)])
            width,height=(int(x) for x in result.strip().splitlines()[0].split('x'))
            scale=self.res.currentIndex()+2
            self.dimensions_label.setText(f'{width} × {height}  →  {width*scale} × {height*scale}  ({scale}×)')
        except (ValueError,IndexError,OSError) as exc:
            self.dimensions_label.setText(f'Cannot detect dimensions: {exc}')

    def save_settings(self):
        if not hasattr(self,'res') or not hasattr(self,'ffmpeg'):return
        d={'input':self.input_edit.text(),'output':self.output_edit.text(),'scale':self.res.currentIndex()+2,'quality':self.quality.currentText(),'strength':self.strength.value(),'ffmpeg':self.ffmpeg.text(),'ffprobe':self.ffprobe.text()}
        self.settings.update(d);save(self.settings)
    def append(self,message):
        message=message.rstrip('\r\n');self.logs.append(message)
        if self.logfile:
            with open(self.logfile,'a',encoding='utf-8',errors='replace') as f:f.write(message+'\n')
        if 'STAGE ' in message:
            import re
            m=re.search(r'STAGE (\d+)/(\d+)',message)
            if m:self.progress.setValue(int(100*(int(m.group(1))-1)/int(m.group(2))))
        if 'VSR_STAGE:' in message:self.progress_label.setText(message)
    def update_status(self):
        items=[('Sample repo',repo()/'video_super_resolution.py'),('Python environment',python_env())]
        self.status.setText('\n'.join(f'{label}: {"Found" if path.is_file() else "Missing"} — {path}' for label,path in items))
    def run_process(self,task,cmd,cwd=None):
        if self.process and self.process.state()!=QProcess.NotRunning:return
        self.task=task;self.logfile=ROOT/'logs'/(task+'_'+datetime.datetime.now().strftime('%Y%m%d_%H%M%S')+'.log')
        self.process=QProcess(self);self.process.setProcessChannelMode(QProcess.MergedChannels)
        if cwd:self.process.setWorkingDirectory(str(cwd))
        self.process.readyReadStandardOutput.connect(self.read_output)
        self.process.finished.connect(self.finished)
        self.process.errorOccurred.connect(lambda e:self.append('Process error: '+self.process.errorString()))
        self.process.start(str(cmd[0]),[str(x) for x in cmd[1:]])
        self.start.setEnabled(False);self.install.setEnabled(False);self.stop.setEnabled(True)
        self.progress.setRange(0,0);self.work_progress.setRange(0,0);self.work_state.setText(task+' running…');self.progress_label.setText(task+' running…')
        self.append('Starting '+task)
    def read_output(self):
        text=bytes(self.process.readAllStandardOutput()).decode('utf-8',errors='replace')
        for line in text.splitlines():self.append(line)
    def finished(self,code,status):
        self.read_output();success=code==0 and status==QProcess.NormalExit
        self.append(f'{self.task} {"finished" if success else "failed/cancelled"} (exit code {code})')
        self.progress.setRange(0,100);self.progress.setValue(100 if success else 0)
        self.work_progress.setRange(0,100);self.work_progress.setValue(100 if success else 0)
        self.progress_label.setText('Finished' if success else 'Failed or cancelled — see log')
        self.work_state.setText(self.progress_label.text());self.start.setEnabled(True);self.install.setEnabled(True);self.stop.setEnabled(False);self.update_status()
        if not success:QMessageBox.warning(self,'Task not completed','Check the Advanced tab logs for the exact error.')
    def start_install(self):
        self.tabs.setCurrentIndex(1)
        self.run_process('install',[sys.executable,'-u',ROOT/'presets/extra_env/nvidia_vsr_installer.py'],ROOT)
    def verify_setup(self):
        self.update_status()
        if not python_env().is_file():QMessageBox.information(self,'Verify','Environment not installed. Use Install / Repair.');return
        self.tabs.setCurrentIndex(1)
        self.run_process('verify',[python_env(),'-c','import torch,av,nvvfx; print("Dependencies import OK; CUDA:",torch.cuda.is_available(),flush=True)'],ROOT)
    def start_job(self):
        self.save_settings();source=Path(self.settings['input']);out=self.settings['output']
        if not source.is_file():QMessageBox.warning(self,'Missing source','Select an existing video file.');return
        if not out:out=str(ROOT/'output'/(source.stem+'_maxine_'+str(self.res.currentIndex()+2)+'x.mp4'));self.output_edit.setText(out);self.save_settings()
        ffmpeg=find_exe('ffmpeg',self.settings['ffmpeg']);ffprobe=find_exe('ffprobe',self.settings['ffprobe'])
        if not ffmpeg or not ffprobe:QMessageBox.warning(self,'FFmpeg missing','Place ffmpeg.exe and ffprobe.exe in presets/bin/ or specify an override in Advanced.');return
        if not python_env().is_file():QMessageBox.warning(self,'Not installed','Install the NVIDIA Python environment from the Advanced tab.');return
        outpath=Path(out)
        if source.resolve()==outpath.resolve():QMessageBox.warning(self,'Output invalid','Choose an output different from the source.');return
        if outpath.exists() and QMessageBox.question(self,'Overwrite output','Replace existing output?')!=QMessageBox.Yes:return
        self.run_process('upscale',[sys.executable,'-u',ROOT/'runtime/nvidia_vsr/runner.py','--input',source,'--output',outpath,'--scale',str(self.res.currentIndex()+2),'--quality',self.quality.currentText(),'--strength',str(self.strength.value()),'--ffmpeg',ffmpeg,'--ffprobe',ffprobe],ROOT)
    def cancel(self):
        if self.process and self.process.state()!=QProcess.NotRunning:
            self.append('Cancellation requested')
            # Kill child process tree on Windows to avoid orphaned GPU workers.
            if os.name=='nt':subprocess.run(['taskkill','/PID',str(self.process.processId()),'/T','/F'],capture_output=True)
            else:self.process.kill()
    def closeEvent(self,event):
        if self.process and self.process.state()!=QProcess.NotRunning:
            QMessageBox.warning(self,'Task running','Cancel or finish the task before closing.');event.ignore();return
        self.save_settings();event.accept()
if __name__=='__main__':
    app=QApplication(sys.argv)
    app.setStyleSheet('QPushButton { padding: 8px; } QGroupBox { font-weight: bold; margin-top: 10px; padding-top: 8px; } QLineEdit,QComboBox,QDoubleSpinBox { min-height: 26px; }')
    w=Window();w.show();sys.exit(app.exec())
