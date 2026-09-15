@echo off
REM Hunter, watching for applications you have approved.
REM
REM Double-click this, or let Windows start it for you (see setup.bat).
REM While it is running: reply APPROVE to an application email, and within a
REM minute the form opens in Chrome, filled in, with your CV attached. Read it,
REM press Submit. Nothing else to do; the employer's "thanks for applying" email
REM closes the row by itself.
REM
REM Closing this window stops it. Nothing is ever submitted by this program.

title Hunter
cd /d "%~dp0\.."

if not defined SUPABASE_URL (
  if exist "%~dp0env.bat" call "%~dp0env.bat"
)
if not defined SUPABASE_URL (
  echo.
  echo   Missing SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY.
  echo   Put them in windows\env.bat, then run this again. See windows\README.md
  echo.
  pause
  exit /b 1
)

echo.
echo   Hunter is watching. Approve an application in your email and the form
echo   will open here, filled in, for you to press Submit.
echo.
echo   Leave this window open. Close it to stop.
echo.

python -m hunter.run watch --every 60
pause
