@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
set "PYTHON="
if exist ".e2e-venv\Scripts\python.exe" (
    set "PYTHON=.e2e-venv\Scripts\python.exe"
) else if exist ".venv\Scripts\python.exe" (
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
echo 首次启动请在打开的页面中初始化管理员账号（无默认密码，至少 8 位）。
start "" http://127.0.0.1:8765/
%PYTHON% -B pilot\start_pilot.py --port 8765
