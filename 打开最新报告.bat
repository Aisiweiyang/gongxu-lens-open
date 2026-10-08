@echo off
setlocal
set "DIR=%~dp0output"
for /f "delims=" %%f in ('dir /b /o-d "%DIR%\*.html" 2^>nul') do (
    start "" "%DIR%\%%f"
    goto :end
)
echo 尚未生成报告。
pause
:end
