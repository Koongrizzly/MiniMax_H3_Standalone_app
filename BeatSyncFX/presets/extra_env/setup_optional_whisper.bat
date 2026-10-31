@echo off
setlocal
cd /d "%~dp0..\.."
if not exist "environments\beatsyncfx\Scripts\python.exe" (
 echo Please run setup_windows.bat first.
 pause
 exit /b 1
)
echo Select Whisper model to install. Existing installed models will be preserved.
echo 1. Small      approx 460 MB download / 1 GB VRAM
echo 2. Medium     approx 1.5 GB download / 2-3 GB VRAM [RECOMMENDED]
echo 3. Large-v3   approx 3.1 GB download / 4-6 GB VRAM
choice /c 123 /m "Choose model"
if errorlevel 3 (set "MODEL=large-v3" & goto selected)
if errorlevel 2 (set "MODEL=medium" & goto selected)
set "MODEL=small"
:selected
"environments\beatsyncfx\Scripts\python.exe" -m pip install --disable-pip-version-check -r helpers\requirements_whisper.txt
if errorlevel 1 (echo Dependency install failed.& pause & exit /b 1)
"environments\beatsyncfx\Scripts\python.exe" -m helpers.download_whisper %MODEL%
if errorlevel 1 (echo Model download failed.& pause & exit /b 1)
echo Installed %MODEL% successfully. Select it in BeatSync FX.
pause
