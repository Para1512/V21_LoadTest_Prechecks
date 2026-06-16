@echo off
setlocal
cd /d "%~dp0"

if exist ".env" (
  echo .env already exists at:
  echo   %CD%\.env
  echo Edit it with Notepad if Oracle settings are wrong.
  notepad .env
  exit /b 0
)

if not exist ".env.example" (
  echo ERROR: .env.example not found in %CD%
  pause
  exit /b 1
)

copy /Y ".env.example" ".env" >nul
echo Created .env from .env.example:
echo   %CD%\.env
echo.
echo Opening in Notepad — set ORACLE_PASSWORD ^(same as SQL Developer^), save, then:
echo   python app.py
notepad .env
