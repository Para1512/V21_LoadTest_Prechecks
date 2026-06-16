@echo off
setlocal
cd /d "%~dp0"

where python >nul 2>&1
if errorlevel 1 goto trynode

echo Using Python backend...
python -m pip install -r requirements.txt
if errorlevel 1 (
  echo pip install failed. Try: python -m pip install --user -r requirements.txt
  pause
  exit /b 1
)
echo Starting Automation Portal...
python app.py
if errorlevel 1 pause
exit /b 0

:trynode
where node >nul 2>&1
if errorlevel 1 (
  echo Neither Python nor Node.js was found on PATH.
  echo Install Python 3 from https://www.python.org/downloads/ ^(check "Add python.exe to PATH"^)
  echo   OR install Node.js from https://nodejs.org
  pause
  exit /b 1
)

echo Using Node.js backend...
if not exist "node_modules\" (
  echo Installing npm packages...
  call npm install
  if errorlevel 1 (
    echo npm install failed.
    pause
    exit /b 1
  )
)
echo Starting Automation Portal...
node server.js
if errorlevel 1 pause
