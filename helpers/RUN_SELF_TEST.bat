@echo off
setlocal
cd /d "%~dp0.."
if not exist "environments\beatsyncfx\Scripts\python.exe" (
 echo Run setup_windows.bat first.
 pause
 exit /b 1
)
"environments\beatsyncfx\Scripts\python.exe" -m helpers.smoke_test
pause
