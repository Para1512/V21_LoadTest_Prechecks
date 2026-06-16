@echo off
setlocal
cd /d "%~dp0"

where node >nul 2>&1
if errorlevel 1 (
  echo Node.js is required for the desktop app. Install from https://nodejs.org
  pause
  exit /b 1
)

if not exist "node_modules\" (
  echo Installing npm packages...
  call npm install
  if errorlevel 1 (
    echo npm install failed.
    pause
    exit /b 1
  )
)

echo Starting V21 PreChecks Portal (desktop window^)...
set OPEN_BROWSER=0
call npm run desktop
if errorlevel 1 pause
exit /b 0
