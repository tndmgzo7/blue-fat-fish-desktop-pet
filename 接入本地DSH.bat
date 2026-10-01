@echo off
chcp 65001 >nul
setlocal
set "PET_DSH_HOME=%USERPROFILE%\.dsh"
if defined DSH_HOME set "PET_DSH_HOME=%DSH_HOME%"
if exist "%~dp0app\.venv\Scripts\python.exe" (
  "%~dp0app\.venv\Scripts\python.exe" "%~dp0scripts\install_dsh_bridge.py" --home "%PET_DSH_HOME%" --profiles desktop,web
) else (
  py -3 "%~dp0scripts\install_dsh_bridge.py" --home "%PET_DSH_HOME%" --profiles desktop,web
)
if errorlevel 1 (
  echo 接入失败，请检查 Python 与 dsh 目录。
) else (
  echo 已接入存在的网页版和桌面版配置。打开需要的版本，在蓝色大肥鱼右键“连接 dsh”中选择。
)
pause
