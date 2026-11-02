@echo off
rem Double-click to start the Trade Dashboard. It only reads the bots' files and never trades.
rem The page opens at http://127.0.0.1:8765/ . Stop the dashboard by closing this window (or Ctrl+C).
cd /d "%~dp0"
title Trade Dashboard
".venv\Scripts\python.exe" -m dashboard
set "RC=%ERRORLEVEL%"
echo.
if "%RC%"=="2" (
  echo The dashboard did not start. If it says the port is in use, it is probably already running:
  echo open http://127.0.0.1:8765/ in your browser. Otherwise copy the message above to Claude.
  pause
  exit /b 2
)
if not "%RC%"=="0" echo The dashboard stopped with an error. Copy the message above to Claude.
echo The dashboard has stopped. Double-click "Start Dashboard" to start it again.
pause
