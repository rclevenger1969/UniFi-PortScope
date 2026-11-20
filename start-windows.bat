@echo off
cd /d "%~dp0"
where py >nul 2>&1
if errorlevel 1 (
 echo Install Python 3.11 or newer from python.org first.
 pause
 exit /b 1
)
if not exist ".venv\Scripts\python.exe" (
  py -3 -m venv .venv
  if errorlevel 1 exit /b 1
)
".venv\Scripts\python.exe" -m pip install -q -r requirements.txt
if errorlevel 1 (
 echo Failed to install Python requirements.
 pause
 exit /b 1
)
echo Open http://127.0.0.1:8899  --  dashboard password appears below.
".venv\Scripts\python.exe" -m uvicorn app.main:app --host 127.0.0.1 --port 8899
pause
