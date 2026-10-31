@echo off
setlocal
cd /d "%~dp0"
if not exist "environments\beatsyncfx\Scripts\python.exe" (
 echo First run setup_windows.bat to create the environment.
 pause
 exit /b 1
)
"environments\beatsyncfx\Scripts\python.exe" -c "from PIL import Image" >nul 2>&1
if errorlevel 1 (
 echo Pillow is missing from the BeatSync FX environment.
 echo Run setup_windows.bat to install or repair dependencies.
 pause
 exit /b 1
)
"environments\beatsyncfx\Scripts\python.exe" -m helpers.app_main
if errorlevel 1 pause
