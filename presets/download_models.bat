@echo off
setlocal
for %%I in ("%~dp0..") do set "ROOT=%%~fI"
cd /d "%ROOT%"
set "PY=%ROOT%\environments\.minimax_h3_int4\python.exe"
if not exist "%PY%" (
  echo ERROR: Runtime environment not found.
  echo Run install.bat first.
  pause
  exit /b 1
)
"%PY%" "%ROOT%\runtime\download_models.py"
set "ERR=%ERRORLEVEL%"
echo.
if not "%ERR%"=="0" echo MODEL DOWNLOAD FAILED. Error code: %ERR%
if "%ERR%"=="0" echo REQUIRED COMPONENTS COMPLETE. Use GrizzlyMax Settings for current INT4 / INT8 checkpoints; rerun this helper for new LoRAs.
pause
exit /b %ERR%
