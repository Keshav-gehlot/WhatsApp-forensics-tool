@echo off
REM Builds a one-folder Windows app. Needs Python 3.12+ on PATH.
cd /d "%~dp0"
python -m pip install -r requirements-dev.txt || goto :fail
python -m PyInstaller wa_forensicator.spec --noconfirm || goto :fail
if exist bin xcopy /E /I /Y bin dist\WhatsAppForensicator\bin >nul
if exist models xcopy /E /I /Y models dist\WhatsAppForensicator\models >nul
copy /Y LICENSE dist\WhatsAppForensicator\ >nul
copy /Y THIRD_PARTY_NOTICES.md dist\WhatsAppForensicator\ >nul
echo.
echo Built: dist\WhatsAppForensicator\WhatsAppForensicator.exe
echo (adb.exe: put platform-tools\ next to the exe or have adb on PATH.)
pause
exit /b 0
:fail
echo Build failed.
pause
exit /b 1
