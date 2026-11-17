@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Python virtual environment missing. Run: python -m venv .venv
  echo Then: .venv\Scripts\python.exe -m pip install -r requirements.txt
  pause
  exit /b 1
)
".venv\Scripts\python.exe" -c "import tkinter, jsonschema" >nul 2>&1
if errorlevel 1 (
  echo Install the updated requirements: .venv\Scripts\python.exe -m pip install -r requirements.txt
  echo Tkinter must also be available in the selected Python installation.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" stock_batch_manager.py
if errorlevel 1 pause
