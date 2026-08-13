@echo off
setlocal
cd /d "%~dp0"

set "PY=F:\minimax_h3_int4_standalone\environments\.minimax_h3_int4\python.exe"

if not exist "%PY%" (
    echo Could not find standalone Python:
    echo %PY%
    echo.
    echo Edit this BAT if your MiniMax install is in another location.
    pause
    exit /b 1
)

if "%~1"=="" (
    echo Drag a .safetensors VAE file onto this BAT file.
    echo.
    echo Or run:
    echo check_vae_precision.bat "F:\path\to\video_vae.safetensors"
    pause
    exit /b 1
)

"%PY%" "%~dp0check_vae_precision.py" "%~1"
endlocal
