@echo off
REM Start PEG. Double-click this file, or run it from a command prompt.
REM
REM Nothing to install: PEG uses only the Python standard library, so this just
REM finds a Python 3.11+ and hands it the game.

cd /d "%~dp0"

REM The Windows launcher "py" is the reliable way to pick a version.
where py >nul 2>nul
if %errorlevel%==0 (
    py -3 -m peg %*
    goto done
)

where python >nul 2>nul
if %errorlevel%==0 (
    python -m peg %*
    goto done
)

echo PEG needs Python 3.11 or newer, and I could not find it.
echo.
echo   Install it from https://www.python.org/downloads/
echo   Tick "Add Python to PATH" during setup.
echo.
pause
exit /b 1

:done
REM Keep the window open when double-clicked, so any message stays readable.
if "%~1"=="" pause
