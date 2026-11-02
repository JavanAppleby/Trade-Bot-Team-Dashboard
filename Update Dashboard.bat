@echo off
rem Double-click to update the Trade Dashboard. Close the dashboard's window first.
rem Everything is one parenthesised block that ends in exit /b: cmd reads the whole block before
rem it runs any of it, so when git pull replaces this file the rest is not read from the new text.
cd /d "%~dp0"
(
  git pull
  if errorlevel 1 (
    echo.
    echo The update did not download. Copy the message above to Claude.
    pause
    exit /b 1
  )
  ".venv\Scripts\python.exe" -m pip install -q -e ".[dev]"
  if errorlevel 1 (
    echo.
    echo The update downloaded but did not install. Copy the message above to Claude.
    pause
    exit /b 1
  )
  ".venv\Scripts\python.exe" -m dashboard --version
  echo.
  echo Updated. Double-click "Start Dashboard" to start it again.
  pause
  exit /b 0
)
