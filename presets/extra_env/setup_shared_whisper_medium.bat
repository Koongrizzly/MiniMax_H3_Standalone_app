@echo off
setlocal
cd /d "%~dp0..\.."
set "MAINPY=environments\.minimax_h3_int4\python.exe"
if not exist "%MAINPY%" set "MAINPY=environments\.minimax_h3_int4\Scripts\python.exe"
if not exist "%MAINPY%" (echo GrizzlyMax environment not found. Run install.bat first.& exit /b 1)
if not exist "environments\.whisper\Scripts\python.exe" (
  "%MAINPY%" -m venv "environments\.whisper"
  if errorlevel 1 (echo Could not create isolated Whisper environment with GrizzlyMax Python.& exit /b 1)
)
set "WHISPERPY=environments\.whisper\Scripts\python.exe"
"%WHISPERPY%" -m pip install --disable-pip-version-check faster-whisper huggingface-hub "av==18.1.0"
if errorlevel 1 exit /b 1
"%WHISPERPY%" -c "from huggingface_hub import snapshot_download; snapshot_download(repo_id='Systran/faster-whisper-medium',local_dir='models/whisper/faster-whisper-medium')"
if errorlevel 1 exit /b 1
if not exist "models\whisper\faster-whisper-medium\model.bin" exit /b 1
echo Shared Faster-Whisper Medium installed.
exit /b 0
