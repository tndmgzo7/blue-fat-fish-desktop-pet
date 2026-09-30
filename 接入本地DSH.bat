@echo off
chcp 65001 >nul
setlocal
set "PET_DSH_HOME=%USERPROFILE%\.dsh"
if defined DSH_HOME set "PET_DSH_HOME=%DSH_HOME%"
if exist "%~dp0app\.venv\Scripts\python.exe" (
  "%~dp0app\.venv\Scripts\python.exe" "%~dp0scripts\install_dsh_bridge.py" --home "%PET_DSH_HOME%" --profiles desktop
) else (
  py -3 "%~dp0scripts\install_dsh_bridge.py" --home "%PET_DSH_HOME%" --profiles desktop
)
if errorlevel 1 (
  echo 接入失败，请检查 Python 与 dsh 目录。
) else (
  echo 已处理 dsh 桌面配置。请运行 dsh，再启动DSH桌宠。
)
pause
