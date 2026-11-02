@echo off
setlocal
cd /d "%~dp0..\.."
set "PY=environments\beatsyncfx\Scripts\python.exe"
if not exist "%PY%" (
 echo BeatSyncFX environment is missing. Run BeatSyncFX\setup_windows.bat first.
 pause
 exit /b 1
)
echo Installing optional Faster-Whisper Medium dependencies...
"%PY%" -m pip install --disable-pip-version-check -r helpers\requirements_whisper.txt
if errorlevel 1 (echo ERROR: Whisper dependencies failed. & pause & exit /b 1)
echo Downloading Faster-Whisper Medium into BeatSyncFX\models\whisper-medium...
"%PY%" -m helpers.download_whisper medium
if errorlevel 1 (echo ERROR: Medium model download failed. & pause & exit /b 1)
echo Medium ready. Select it in BeatSyncFX's Whisper model selector.
pause
