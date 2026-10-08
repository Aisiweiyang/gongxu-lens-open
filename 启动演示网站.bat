@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
set "PYTHON="
if exist ".venv\Scripts\python.exe" (
    set "PYTHON=.venv\Scripts\python.exe"
) else (
    where py >nul 2>nul
    if not errorlevel 1 set "PYTHON=py -3"
)
if "%PYTHON%"=="" (
    echo [ERROR] Python not found. Run setup bat first.
    pause
    exit /b 1
)
start "" http://127.0.0.1:8765/
%PYTHON% -B serve.py --port 8765
