@echo off
setlocal EnableExtensions EnableDelayedExpansion
cd /d "%~dp0"
REM Clean legacy root copies only after the new canonical files are present.
if exist "%CD%\presets\download_models.bat" if exist "%CD%\download_models.bat" del /q "%CD%\download_models.bat" >nul 2>&1
if exist "%CD%\presets\requirements-runtime.txt" if exist "%CD%\requirements-runtime.txt" del /q "%CD%\requirements-runtime.txt" >nul 2>&1
if exist "%CD%\helpers\generate.py" if exist "%CD%\generate.py" del /q "%CD%\generate.py" >nul 2>&1
if exist "%CD%\helpers\generate_ref.py" if exist "%CD%\generate_ref.py" del /q "%CD%\generate_ref.py" >nul 2>&1
title MiniMax-H3 INT4 ConvRot Standalone Installer

echo ============================================================
echo MiniMax-H3 INT4 W4A8 ConvRot Standalone - CLEAN INSTALL
echo ============================================================
echo.
echo This creates an isolated Python environment and runtime.
echo After the runtime install finishes, the model downloader starts
echo automatically and lets you choose FL2VA, Ref2VA, both, and LoRAs.
echo.

REM ------------------------------------------------------------
REM Locate Conda robustly. Do not assume Conda was added to PATH.
REM Windows Conda installs may expose Scripts\conda.exe and/or
REM condabin\conda.bat. Miniforge and AppData installs are common.
REM ------------------------------------------------------------
set "CONDA_CMD="
set "CONDA_KIND="

REM 1) Reuse Conda's own environment variables when launched from a Conda prompt.
if defined CONDA_EXE if exist "%CONDA_EXE%" (
  set "CONDA_CMD=%CONDA_EXE%"
  set "CONDA_KIND=exe"
)
if not defined CONDA_CMD if defined CONDA_BAT if exist "%CONDA_BAT%" (
  set "CONDA_CMD=%CONDA_BAT%"
  set "CONDA_KIND=bat"
)
if not defined CONDA_CMD if defined CONDA_PREFIX (
  if exist "%CONDA_PREFIX%\Scripts\conda.exe" (
    set "CONDA_CMD=%CONDA_PREFIX%\Scripts\conda.exe"
    set "CONDA_KIND=exe"
  ) else if exist "%CONDA_PREFIX%\condabin\conda.bat" (
    set "CONDA_CMD=%CONDA_PREFIX%\condabin\conda.bat"
    set "CONDA_KIND=bat"
  )
)
if not defined CONDA_CMD if defined CONDA_PYTHON_EXE (
  for %%R in ("%CONDA_PYTHON_EXE%\..") do (
    if exist "%%~fR\Scripts\conda.exe" (
      set "CONDA_CMD=%%~fR\Scripts\conda.exe"
      set "CONDA_KIND=exe"
    ) else if exist "%%~fR\condabin\conda.bat" (
      set "CONDA_CMD=%%~fR\condabin\conda.bat"
      set "CONDA_KIND=bat"
    )
  )
)

REM 2) Check common per-user, AppData, all-users, Program Files, and Miniforge locations.
if not defined CONDA_CMD (
  for %%R in (
    "%USERPROFILE%\miniconda3"
    "%USERPROFILE%\Miniconda3"
    "%USERPROFILE%\anaconda3"
    "%USERPROFILE%\Anaconda3"
    "%USERPROFILE%\miniforge3"
    "%USERPROFILE%\Miniforge3"
    "%LOCALAPPDATA%\miniconda3"
    "%LOCALAPPDATA%\Miniconda3"
    "%LOCALAPPDATA%\anaconda3"
    "%LOCALAPPDATA%\Anaconda3"
    "%LOCALAPPDATA%\miniforge3"
    "%LOCALAPPDATA%\Miniforge3"
    "%PROGRAMDATA%\miniconda3"
    "%PROGRAMDATA%\Miniconda3"
    "%PROGRAMDATA%\anaconda3"
    "%PROGRAMDATA%\Anaconda3"
    "%PROGRAMDATA%\miniforge3"
    "%PROGRAMDATA%\Miniforge3"
    "%ProgramFiles%\Miniconda3"
    "%ProgramFiles%\Anaconda3"
    "%ProgramFiles%\Miniforge3"
  ) do (
    if not defined CONDA_CMD if exist "%%~R\Scripts\conda.exe" (
      set "CONDA_CMD=%%~R\Scripts\conda.exe"
      set "CONDA_KIND=exe"
    )
    if not defined CONDA_CMD if exist "%%~R\condabin\conda.bat" (
      set "CONDA_CMD=%%~R\condabin\conda.bat"
      set "CONDA_KIND=bat"
    )
  )
)

REM 3) PATH lookup. Check both the executable and the Windows batch wrapper.
if not defined CONDA_CMD (
  for /f "delims=" %%C in ('where conda.exe 2^>nul') do if not defined CONDA_CMD (
    set "CONDA_CMD=%%C"
    set "CONDA_KIND=exe"
  )
)
if not defined CONDA_CMD (
  for /f "delims=" %%C in ('where conda.bat 2^>nul') do if not defined CONDA_CMD (
    set "CONDA_CMD=%%C"
    set "CONDA_KIND=bat"
  )
)

REM 4) Last resort: ask whether the user has Conda in a custom location.
if not defined CONDA_CMD (
  echo.
  echo Conda not found in default locations.
  echo If you have Conda installed in a custom location, continue with Y
  echo to enter the exact path. Choose N to exit and download + install
  echo Miniconda first.
  echo.
  choice /C YN /N /M "Do you want to enter a custom Conda path? [Y/N]: "
  if errorlevel 2 (
    echo.
    echo Installer cancelled. Please download and install Miniconda, then run this installer again.
    pause
    exit /b 1
  )

  echo.
  echo Enter the Conda installation folder, for example:
  echo   C:\Users\YourName\miniconda3
  echo   C:\Users\YourName\AppData\Local\anaconda3
  echo   D:\Miniforge3
  echo.
  set /p "CONDA_ROOT=Exact Conda folder: "
  if defined CONDA_ROOT (
    set "CONDA_ROOT=!CONDA_ROOT:"=!"
    if exist "!CONDA_ROOT!\Scripts\conda.exe" (
      set "CONDA_CMD=!CONDA_ROOT!\Scripts\conda.exe"
      set "CONDA_KIND=exe"
    ) else if exist "!CONDA_ROOT!\condabin\conda.bat" (
      set "CONDA_CMD=!CONDA_ROOT!\condabin\conda.bat"
      set "CONDA_KIND=bat"
    )
  )
)

if not defined CONDA_CMD (
  echo.
  echo ERROR: No working Conda installation was found at the path you entered.
  echo Check the path and run the installer again, or install Miniconda first.
  pause
  exit /b 1
)

echo Found Conda: %CONDA_CMD%

REM Validate the discovered command before using it.
if /i "%CONDA_KIND%"=="bat" (
  call "%CONDA_CMD%" --version >nul 2>&1
) else (
  "%CONDA_CMD%" --version >nul 2>&1
)
if errorlevel 1 (
  echo ERROR: Conda was found but could not be executed:
  echo   %CONDA_CMD%
  pause
  exit /b 1
)

set "ENV=%CD%\environments\.minimax_h3_int4"
set "PY=%ENV%\python.exe"
if not exist "%PY%" (
  echo [1/7] Creating isolated Python 3.12 environment...
  if /i "%CONDA_KIND%"=="bat" (
    call "%CONDA_CMD%" create -y -p "%ENV%" python=3.12 pip
  ) else (
    "%CONDA_CMD%" create -y -p "%ENV%" python=3.12 pip
  )
  if errorlevel 1 goto :fail
) else (
  echo [1/7] Environment already exists.
)

echo [2/7] Updating pip...
"%PY%" -m pip install --upgrade pip setuptools wheel
if errorlevel 1 goto :fail

echo [3/7] Installing matched PyTorch 2.11.0 CUDA 13.0 stack...
echo       torch 2.11.0 / torchvision 0.26.0 / torchaudio 2.11.0
REM PyTorch 2.12.1 does not publish a matching torchaudio 2.12.1 cu130 Windows wheel.
REM Clear any partial packages from an interrupted/failed previous install first.
"%PY%" -m pip uninstall -y torch torchvision torchaudio >nul 2>&1
"%PY%" -m pip install torch==2.11.0 torchvision==0.26.0 torchaudio==2.11.0 --index-url https://download.pytorch.org/whl/cu130
if errorlevel 1 goto :fail

echo [4/7] Installing MiniMax-H3 runtime dependencies...
"%PY%" -m pip install -r "%CD%\presets\requirements-runtime.txt"
if errorlevel 1 goto :fail

echo [5/7] Installing Triton-Windows for Torch 2.10+...
REM SageAttention imports Triton at runtime. Triton-Windows 3.6 is the compatible
REM Windows line for PyTorch 2.10+; cap below 3.7 to avoid a future incompatible upgrade.
"%PY%" -c "import triton; import importlib.metadata as m; v=m.version('triton-windows'); assert v.startswith('3.6.'), v" >nul 2>&1
if errorlevel 1 (
  echo       Triton-Windows 3.6 missing or incompatible; installing before SageAttention...
  "%PY%" -m pip install --upgrade "triton-windows>=3.6,<3.7"
  if errorlevel 1 goto :fail
) else (
  echo       Compatible Triton-Windows already installed; skipping download.
)

echo [6/7] Installing SageAttention 2.2.0.post6 for CUDA 13 / Torch 2.10+ ABI...
set "SAGE_WHEEL=https://github.com/woct0rdho/SageAttention/releases/download/v2.2.0-windows.post6/sageattention-2.2.0+cu130torch2.10.0andhigher.post6-cp310-abi3-win_amd64.whl"
REM Do not let SageAttention modify the already matched Torch/CUDA stack.
"%PY%" -c "import sageattention; from sageattention import sageattn_qk_int8_pv_fp16_cuda" >nul 2>&1
if errorlevel 1 (
  echo       SageAttention missing or incomplete; installing exact prebuilt wheel with --no-deps...
  "%PY%" -m pip install --no-deps "%SAGE_WHEEL%"
  if errorlevel 1 goto :fail
) else (
  echo       Compatible SageAttention already installed; skipping download.
)

echo [7/7] Running import / CUDA / SageAttention smoke test...
"%PY%" "%CD%\runtime\validate_install.py"
if errorlevel 1 goto :fail

echo.
echo ============================================================
echo RUNTIME INSTALL COMPLETE
echo ============================================================
echo.
echo Starting model downloader...
echo.
call "%CD%\presets\download_models.bat"
set "DLERR=%ERRORLEVEL%"
if not "%DLERR%"=="0" (
  echo.
  echo Runtime installation completed, but model download returned code %DLERR%.
  echo You can run presets\download_models.bat again at any time.
  pause
  exit /b %DLERR%
)

echo.
echo ============================================================
echo INSTALL AND MODEL DOWNLOAD COMPLETE
echo ============================================================
echo Run start.bat to open MiniMax-H3.
pause
exit /b 0

:fail
echo.
echo INSTALL FAILED. Error code: %ERRORLEVEL%
pause
exit /b 1
