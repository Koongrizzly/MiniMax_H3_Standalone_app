@echo off
setlocal
cd /d "%~dp0"
REM Clean legacy root copies only after the new canonical files are present.
if exist "%CD%\presets\download_models.bat" if exist "%CD%\download_models.bat" del /q "%CD%\download_models.bat" >nul 2>&1
if exist "%CD%\presets\requirements-runtime.txt" if exist "%CD%\requirements-runtime.txt" del /q "%CD%\requirements-runtime.txt" >nul 2>&1
if exist "%CD%\helpers\generate.py" if exist "%CD%\generate.py" del /q "%CD%\generate.py" >nul 2>&1
if exist "%CD%\helpers\generate_ref.py" if exist "%CD%\generate_ref.py" del /q "%CD%\generate_ref.py" >nul 2>&1
set "PY=%CD%\environments\.minimax_h3_int4\python.exe"
if not exist "%PY%" (
  echo ERROR: MiniMax H3 environment not found: %PY%
  pause
  exit /b 1
)
"%PY%" -c "import PySide6" >nul 2>nul
if errorlevel 1 (
  echo ERROR: PySide6 is not installed in the MiniMax environment.
  echo Run install.bat again after applying this patch, or run:
  echo   "%PY%" -m pip install PySide6
  pause
  exit /b 2
)
"%PY%" helpers\minimax_h3_gui.py
exit /b %ERRORLEVEL%
