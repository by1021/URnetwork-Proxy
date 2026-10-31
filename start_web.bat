@echo off
setlocal
cd /d "%~dp0"
python main.py --web --open-browser %*
if errorlevel 1 pause
