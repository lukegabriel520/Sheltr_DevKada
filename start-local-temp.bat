@echo off
setlocal EnableExtensions

REM Temporary convenience launcher for local Sheltr demo (Windows).
REM Starts backend from repo-root .venv and Expo from frontend\.

set "ROOT_DIR=%~dp0"
if "%ROOT_DIR:~-1%"=="\" set "ROOT_DIR=%ROOT_DIR:~0,-1%"
set "VENV_PY=%ROOT_DIR%\.venv\Scripts\python.exe"
set "BACKEND_PY=%ROOT_DIR%\backend\safe_server.py"

if not exist "%BACKEND_PY%" (
  echo [ERROR] Missing backend\safe_server.py in: "%ROOT_DIR%"
  pause
  exit /b 1
)

if not exist "%ROOT_DIR%\frontend\package.json" (
  echo [ERROR] Missing frontend\package.json in: "%ROOT_DIR%"
  pause
  exit /b 1
)

echo.
echo Starting Sheltr backend and frontend...
echo.

if not exist "%VENV_PY%" (
  echo [1/2] Creating Python venv in .venv ...
  pushd "%ROOT_DIR%" || goto :fail
  py -3 -m venv .venv
  if errorlevel 1 (
    python -m venv .venv
    if errorlevel 1 (
      echo [ERROR] Could not create venv. Install Python 3 and ensure "py" or "python" is on PATH.
      popd
      pause
      exit /b 1
    )
  )
  popd
  echo        Done.
  echo.
)

echo [2/2] Installing ^/ upgrading backend packages...
pushd "%ROOT_DIR%" || goto :fail
"%VENV_PY%" -m pip install -q --upgrade pip
if errorlevel 1 goto :pipfail
"%VENV_PY%" -m pip install -r "backend\requirements.txt"
if errorlevel 1 goto :pipfail
popd
echo        Done.
echo.

REM Use small .cmd helpers so paths with spaces (e.g. Users\Luke Gabriel\...) are not broken by nested quotes.
if not exist "%ROOT_DIR%\run-backend-local.cmd" (
  echo [ERROR] Missing run-backend-local.cmd next to this batch file.
  pause
  exit /b 1
)
if not exist "%ROOT_DIR%\run-frontend-local.cmd" (
  echo [ERROR] Missing run-frontend-local.cmd next to this batch file.
  pause
  exit /b 1
)

start "Sheltr Backend (Temp)" "%ROOT_DIR%\run-backend-local.cmd"
start "Sheltr Frontend (Temp)" "%ROOT_DIR%\run-frontend-local.cmd"

echo Two windows opened:
echo   - Backend: http://127.0.0.1:5000 (or PORT in .env^)
echo   - Expo: scan with Expo Go (LAN^) or press w for web
echo.
echo Delete this file when you no longer need it:
echo   "%ROOT_DIR%\start-local-temp.bat"
echo.
pause
endlocal
exit /b 0

:pipfail
echo [ERROR] pip install failed. See messages above.
popd
pause
exit /b 1

:fail
echo [ERROR] Could not cd to project root.
pause
exit /b 1
