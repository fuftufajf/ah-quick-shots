@echo off
rem Starts the local server without a console window and opens the page. The Windows package brings its own Python
rem and ffmpeg in the folders python and ffmpeg beside this file; a copy of the source needs Python 3.11 or newer on the PATH.
cd /d "%~dp0"
set PY=
if exist "%~dp0python\pythonw.exe" (
  set "PY=%~dp0python\pythonw.exe"
  set "PATH=%~dp0ffmpeg;%PATH%"
)
if not defined PY where pythonw >nul 2>nul && set PY=pythonw
if not defined PY where pyw >nul 2>nul && set PY=pyw
if not defined PY (
  echo Python 3.11 or newer was not found. Install it from https://www.python.org/downloads/
  echo and tick "Add python.exe to PATH" in the installer, then start this file again.
  echo Or download the Windows package, which brings its own: https://github.com/fuftufajf/ah-quick-shots/releases/latest
  pause
  exit /b 1
)
start "" "%PY%" director_api.py
timeout /t 1 /nobreak >nul
start "" "http://127.0.0.1:8766/"
