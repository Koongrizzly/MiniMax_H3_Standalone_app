@echo off
setlocal
cd /d "%~dp0..\.."
set "WHISPERPY=environments\.whisper\Scripts\python.exe"
if not exist "%WHISPERPY%" set "WHISPERPY=environments\.whisper\python.exe"
if not exist "%WHISPERPY%" (
  echo Shared Whisper environment was not found.
  echo Run presets\extra_env\setup_shared_whisper_medium.bat first.
  pause
  exit /b 1
)
echo Repairing only the Shared Whisper PyAV dependency...
"%WHISPERPY%" -m pip install --disable-pip-version-check --upgrade "av==18.1.0"
if errorlevel 1 (
  echo FAILED: PyAV repair could not complete.
  pause
  exit /b 1
)
"%WHISPERPY%" -c "import av; from faster_whisper import WhisperModel; print('PyAV:', av.__version__, '- Faster-Whisper import OK')"
if errorlevel 1 (
  echo FAILED: dependency import test failed.
  pause
  exit /b 1
)
echo Done. Your existing Whisper Medium model was not changed.
pause
exit /b 0
