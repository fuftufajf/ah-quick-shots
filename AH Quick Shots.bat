@echo off
rem Starts the local server without a console window; the server opens the page once it listens. The Windows
rem package brings its own Python and ffmpeg in the folders python and ffmpeg beside this file; a copy of the
rem source needs Python 3.11 or newer on the PATH.
cd /d "%~dp0"
set PY=
if exist "%~dp0python\pythonw.exe" (
  set "PY=%~dp0python\pythonw.exe"
  set "PATH=%~dp0ffmpeg;%PATH%"
)
rem The package's first start from this folder puts a shortcut to this file on the desktop, once: deleted, it stays
rem deleted. The mark sits in python, which only the package has and which a copied .quickshots does not carry.
if defined PY if not exist "%~dp0python\shortcut-made" (
  set "AHQS=%~f0"
  powershell -NoProfile -NonInteractive -Command "$s = (New-Object -ComObject WScript.Shell).CreateShortcut([Environment]::GetFolderPath('Desktop') + '\AH Quick Shots.lnk'); $s.TargetPath = $env:AHQS; $s.WorkingDirectory = Split-Path $env:AHQS; $s.WindowStyle = 7; $s.Save()" >nul 2>nul && type nul > "%~dp0python\shortcut-made"
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
start "" "%PY%" director_api.py --page
