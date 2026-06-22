@echo off
REM Launches the Russian Translator with a visible console so you can see
REM any errors. For everyday use, double-click "Launch Russian Translator.vbs"
REM instead (no console window).

cd /d "%~dp0"
set PYTHONUTF8=1
".venv\Scripts\python.exe" "src\main.py"

echo.
echo (App closed. Window stays open so you can read any messages above.)
pause
