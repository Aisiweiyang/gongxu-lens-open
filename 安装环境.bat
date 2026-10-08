@echo off
setlocal
cd /d "%~dp0"
where uv >nul 2>nul
if not errorlevel 1 goto :UV
where py >nul 2>nul
if errorlevel 1 (
    echo [ERROR] No Python found. Install Python 3.12 first: https://www.python.org/downloads/
    pause
    exit /b 1
)
py -3 -m venv .venv
if errorlevel 1 goto :failed
.venv\Scripts\python.exe -m pip install -r requirements.txt
if errorlevel 1 goto :failed
echo Done. Now double-click run bat.
pause
exit /b 0
:UV
uv --cache-dir .uv-cache venv --python 3.12 .venv
if errorlevel 1 goto :failed
uv --cache-dir .uv-cache pip install --python .venv\Scripts\python.exe -r requirements.txt
if errorlevel 1 goto :failed
echo Done. Now double-click run bat.
pause
exit /b 0
:failed
echo [ERROR] Setup failed. Check network and retry.
pause
exit /b 1
