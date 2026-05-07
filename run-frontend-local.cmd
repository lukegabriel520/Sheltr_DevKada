@echo off
setlocal EnableExtensions
cd /d "%~dp0frontend"

if not exist "package.json" (
  echo [ERROR] frontend\package.json not found.
  pause
  exit /b 1
)

if not exist "node_modules\" (
  echo npm install...
  call npm install
  if errorlevel 1 (
    echo [ERROR] npm install failed.
    pause
    exit /b 1
  )
  echo.
)

echo Expo (LAN). Use Expo Go on the same Wi-Fi or press w for web.
echo.
npx expo start --lan
if errorlevel 1 (
  echo.
  echo [ERROR] Expo exited with an error.
  pause
)
endlocal
