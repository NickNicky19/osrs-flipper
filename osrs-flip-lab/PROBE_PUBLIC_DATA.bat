@echo off
setlocal
cd /d "%~dp0"
where py >nul 2>nul
if not errorlevel 1 (
    py -3 connection_check.py
    pause
    exit /b
)
where python >nul 2>nul
if not errorlevel 1 (
    python connection_check.py
    pause
    exit /b
)
echo Python 3.11 or newer is required. See README.md.
pause
