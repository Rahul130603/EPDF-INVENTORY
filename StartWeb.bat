@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Run Setup.bat first.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" web_app.py --port 4510 --open-browser
if errorlevel 1 pause
