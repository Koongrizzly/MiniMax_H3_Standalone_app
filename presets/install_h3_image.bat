@echo off
setlocal EnableExtensions
cd /d "%~dp0.."
set "UV_CACHE_DIR=%CD%\temp\uv_cache"
set "UV_PYTHON_INSTALL_DIR=%CD%\environments\python"
set "UV_PYTHON_BIN_DIR=%CD%\environments\python_bin"
set "UV_NO_PROGRESS=1"
set "UV_PYTHON_PREFERENCE=only-managed"
set "COREPY=%CD%\environments\.minimax_h3_int4\python.exe"
if not exist "%COREPY%" set "COREPY=%CD%\environments\.minimax_h3_int4\Scripts\python.exe"
if not exist "%COREPY%" (
 echo Install the GrizzlyMax core environment first.
 goto :failed
)
if not exist "temp" mkdir "temp" || goto :failed
if not exist "presets\extra_env\uv.exe" (
 echo Downloading portable uv bootstrap...
 curl.exe -fL --retry 3 "https://github.com/astral-sh/uv/releases/latest/download/uv-x86_64-pc-windows-msvc.zip" -o "temp\uv.zip" || goto :failed
 tar.exe -xf "temp\uv.zip" -C "presets\extra_env" || goto :failed
)
"presets\extra_env\uv.exe" python install 3.11 || goto :failed
if not "%~1"=="" (
 "%COREPY%" "presets\extra_env\h3_image_install.py" %* || goto :failed
) else (
 echo MiniMax H3 Image - portable backend and default W4A8 / VAE
 echo Reuses the installed MiniMax H3 text encoder by default.
 "%COREPY%" "presets\extra_env\h3_image_install.py" --backend --models w4a8 --encoder none || goto :failed
)
echo.
echo Setup finished. Return to GrizzlyMax and recheck installation.
pause
exit /b 0
:failed
echo Setup failed. See messages above.
pause
exit /b 1
