@echo off
setlocal EnableExtensions
cd /d "%~dp0"

echo Sheltr API (Flask). PORT from .env or 5000.
echo.

if not exist ".venv\Scripts\python.exe" (
  echo [ERROR] Missing .venv\Scripts\python.exe
  echo Run start-local-temp.bat from the repo root first (it creates the venv and installs deps^).
  pause
  exit /b 1
)

".venv\Scripts\python.exe" "backend\safe_server.py"
if errorlevel 1 (
  echo.
  echo [ERROR] Backend exited with an error.
  pause
)
endlocal
