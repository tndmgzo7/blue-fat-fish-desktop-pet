@echo off
setlocal
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" -c "import sys; assert sys.version_info >= (3, 9)" >nul 2>&1
  if not errorlevel 1 goto check_dependencies
)
echo Preparing the desktop pet environment...
if exist ".venv" (
  if exist ".venv.backup" goto environment_error
  move ".venv" ".venv.backup" >nul
  if errorlevel 1 goto environment_error
)
py -3 -m venv .venv >nul 2>&1
if errorlevel 1 python -m venv .venv
if errorlevel 1 goto environment_error

:check_dependencies
".venv\Scripts\python.exe" -c "import PySide6.QtWidgets, PySide6.QtNetwork" >nul 2>&1
if not errorlevel 1 goto launch
echo Installing desktop pet dependencies. Please wait...
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto dependency_error

:launch
start "" ".venv\Scripts\pythonw.exe" "%~dp0whale_pet.py" %*
exit /b 0

:environment_error
echo Unable to prepare Python. Install Python 3.9 or newer from python.org.
echo If .venv.backup exists, rename it before repairing this environment again.
pause
exit /b 1

:dependency_error
echo Installation failed. Check your internet connection and run this file again.
pause
exit /b 1
