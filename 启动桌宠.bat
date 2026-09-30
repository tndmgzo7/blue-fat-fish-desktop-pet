@echo off
chcp 65001 >nul
call "%~dp0app\run_windows.bat" --mode standalone %*
