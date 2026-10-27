@echo off
setlocal
cd /d "%~dp0"
where py >nul 2>nul
if not errorlevel 1 (
    py -3 -c "import sys; sys.exit(0 if sys.version_info >= (3,11) else 1)" >nul 2>nul
    if not errorlevel 1 (
        py -3 app.py --mode journal --open
        pause
        exit /b
    )
)
where python >nul 2>nul
if not errorlevel 1 (
    python -c "import sys; sys.exit(0 if sys.version_info >= (3,11) else 1)" >nul 2>nul
    if not errorlevel 1 (
        python app.py --mode journal --open
        pause
        exit /b
    )
)
echo Python 3.11 or newer was not found. See README.md for installation and launch instructions.
pause
