@echo off
setlocal
cd /d "%~dp0"
echo ========================================
echo  BeatSync FX - Windows offline editor
 echo ========================================
if not exist "environments\beatsyncfx\Scripts\python.exe" (
    echo Creating application-local Python environment...
    where py >nul 2>&1
    if not errorlevel 1 (
        py -3.11 -m venv "environments\beatsyncfx" 2>nul
        if errorlevel 1 py -3 -m venv "environments\beatsyncfx"
    ) else (
        python -m venv "environments\beatsyncfx"
    )
)
if not exist "environments\beatsyncfx\Scripts\python.exe" (
    echo ERROR: Python 3.11+ is required. Install Python and rerun this script.
    pause
    exit /b 1
)
echo Installing PySide6, NumPy and Pillow (internet needed for initial setup only)...
"environments\beatsyncfx\Scripts\python.exe" -m pip install --disable-pip-version-check -r helpers\requirements.txt
if errorlevel 1 (
    echo ERROR: Dependencies could not be installed.
    pause
    exit /b 1
)
echo Checking animated GIF dependency (Pillow)...
"environments\beatsyncfx\Scripts\python.exe" -c "from PIL import Image, ImageSequence; print('Pillow available')" >nul 2>&1
if errorlevel 1 (
    echo Pillow is missing. Installing it into the BeatSync FX environment...
    "environments\beatsyncfx\Scripts\python.exe" -m pip install --disable-pip-version-check "Pillow>=10,<13"
    if errorlevel 1 (
        echo ERROR: Pillow installation failed. Check internet access and retry setup_windows.bat.
        pause
        exit /b 1
    )
)
"environments\beatsyncfx\Scripts\python.exe" -c "from PIL import Image, ImageSequence; print('Pillow available')"
if errorlevel 1 (
    echo ERROR: Pillow still cannot be imported. Setup is incomplete.
    pause
    exit /b 1
)
echo.
echo Setup complete. Run BeatSync_FX.bat to start.
echo FFmpeg and ffprobe are also required. Choose them from the GUI if not bundled in presets\bin\.
pause
