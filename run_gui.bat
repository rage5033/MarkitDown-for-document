@echo off
rem Run the GUI from source with the project .venv (no console window). Use run.bat to see console output.
cd /d "%~dp0"
if not exist ".venv\Scripts\pythonw.exe" (
    echo .venv not found. Run: python -m venv .venv ^&^& .venv\Scripts\pip install -r requirements.txt
    pause
    exit /b 1
)
start "" ".venv\Scripts\pythonw.exe" main.py
