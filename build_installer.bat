@echo off
REM Builds the PyInstaller app and wraps it in Setup.exe.
REM Needs: Python 3.12+ on PATH, Inno Setup 6 (https://jrsoftware.org/isinfo.php).
REM Usage: build_installer.bat 1.0.0
setlocal
cd /d "%~dp0"

set VERSION=%1
if "%VERSION%"=="" set VERSION=0.0.0-dev

set ISCC=%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe
if not exist "%ISCC%" set ISCC=%ProgramFiles%\Inno Setup 6\ISCC.exe
if not exist "%ISCC%" set ISCC=%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe
if not exist "%ISCC%" (
  echo Inno Setup 6 not found. Install it from https://jrsoftware.org/isdl.php
  goto :fail
)

powershell -NoProfile -ExecutionPolicy Bypass -File scripts\fetch_tools.ps1 || goto :fail
python -m pip install -r requirements-dev.txt || goto :fail
python -m PyInstaller wa_forensicator.spec --noconfirm || goto :fail
"%ISCC%" /DAppVersion=%VERSION% installer\wa_forensicator.iss || goto :fail

echo.
echo Built: dist-installer\WhatsAppForensicator-Setup-%VERSION%.exe
pause
exit /b 0
:fail
echo Build failed.
pause
exit /b 1
