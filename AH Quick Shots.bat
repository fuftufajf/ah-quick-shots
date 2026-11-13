@echo off
rem Starts the local server without a console window and opens the page. Needs Python 3.11 or newer on the PATH.
cd /d "%~dp0"
set PY=
where pythonw >nul 2>nul && set PY=pythonw
if not defined PY where pyw >nul 2>nul && set PY=pyw
if not defined PY (
  echo Python 3.11 or newer was not found. Install it from https://www.python.org/downloads/
  echo and tick "Add python.exe to PATH" in the installer, then start this file again.
  pause
  exit /b 1
)
start "" %PY% director_api.py
timeout /t 1 /nobreak >nul
start "" "http://127.0.0.1:8766/"
