@echo off
setlocal
cd /d "%~dp0.."
if not exist "environments\beatsyncfx\Scripts\python.exe" (
 echo First run setup_windows.bat
 pause
 exit /b 1
)
echo Installing OPTIONAL local transcription packages...
"environments\beatsyncfx\Scripts\python.exe" -m pip install --disable-pip-version-check -r helpers\requirements_whisper.txt
if errorlevel 1 (echo Dependency install failed.& pause & exit /b 1)
echo Downloading Whisper base model ONCE, to models\whisper-base...
echo This download requires internet only during setup.
"environments\beatsyncfx\Scripts\python.exe" -m helpers.download_whisper
if errorlevel 1 (echo Model download failed.& pause & exit /b 1)
echo Model installed. Transcription now works without internet.
pause
