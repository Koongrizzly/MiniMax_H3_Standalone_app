@echo off
setlocal
cd /d "%~dp0"

rem This converter is intended to sit anywhere inside:
rem F:\minimax_h3_int4_standalone
rem The standalone's real environment is .minimax_h3_int4.

set "ROOT=F:\minimax_h3_int4_standalone"
set "PY=%ROOT%\environments\.minimax_h3_int4\python.exe"

if not exist "%PY%" (
    rem Fallback: converter is one folder below the standalone root.
    for %%R in ("%~dp0..") do set "ROOT=%%~fR"
    set "PY=%ROOT%\environments\.minimax_h3_int4\python.exe"
)

if not exist "%PY%" (
    rem Fallback: converter is directly in the standalone root.
    for %%R in ("%~dp0") do set "ROOT=%%~fR"
    set "PY=%ROOT%\environments\.minimax_h3_int4\python.exe"
)

if not exist "%PY%" (
    echo.
    echo ERROR: Could not find:
    echo   environments\.minimax_h3_int4\python.exe
    echo.
    echo Expected standalone root:
    echo   F:\minimax_h3_int4_standalone
    echo.
    echo No file editing is required.
    pause
    exit /b 1
)

echo Using:
echo   %PY%
echo.

set "MINIMAX_H3_ROOT=%ROOT%"
"%PY%" "%~dp0vae_fp16_converter.py"
set "RC=%ERRORLEVEL%"

if not "%RC%"=="0" (
    echo.
    echo Converter exited with code %RC%.
    pause
)
exit /b %RC%
