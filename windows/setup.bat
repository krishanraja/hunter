@echo off
REM One-time setup. Double-click this once.
REM
REM Installs hunter, its browser driver, and makes the watcher start with
REM Windows so you never have to think about it again.

title Hunter setup
cd /d "%~dp0\.."

echo.
echo   Checking Python...
python --version >nul 2>&1
if errorlevel 1 (
  echo.
  echo   Python is not installed. Get it from https://python.org/downloads
  echo   Tick "Add python.exe to PATH" on the first screen, then run this again.
  echo.
  pause
  exit /b 1
)

echo   Installing hunter...
python -m pip install --quiet --upgrade pip
python -m pip install --quiet -e ".[browser]"
if errorlevel 1 (
  echo   Install failed. Send the lines above to Claude.
  pause
  exit /b 1
)

echo   Installing the browser driver...
python -m playwright install chromium

echo   Making it start with Windows...
set STARTUP=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup
copy /y "%~dp0hunter-watch.bat" "%STARTUP%\hunter-watch.bat" >nul
if errorlevel 1 (
  echo   Could not write to the Startup folder. You can still start it by
  echo   double-clicking windows\hunter-watch.bat whenever you want it running.
) else (
  echo   Done. It will start automatically next time you log in.
)

echo.
echo   Now put your two Supabase values in windows\env.bat, then double-click
echo   windows\hunter-watch.bat to start it for the first time.
echo.
pause
