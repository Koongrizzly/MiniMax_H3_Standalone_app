@echo off
setlocal
cd /d "%~dp0..\.."
set "PY=environments\.minimax_h3_int4\python.exe"
if not exist "%PY%" set "PY=environments\.minimax_h3_int4\Scripts\python.exe"
if not exist "%PY%" (
  echo GrizzlyMax Python not found. Install GrizzlyMax first.
  goto FAILED
)
echo Installing / checking shared Whisper.cpp for Prompt Builder and Music Clip Creator...
"%PY%" "presets\extra_env\install_shared_whisper_cpp.py"
if errorlevel 1 goto FAILED
echo.
echo Installation successful. Click Recheck in Validate Install.
echo.
pause
exit /b 0
:FAILED
echo.
echo Whisper.cpp setup FAILED. See logs\install_shared_whisper_cpp.log for details.
echo.
pause
exit /b 1
