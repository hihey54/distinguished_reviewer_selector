@echo off
setlocal
cd /d "%~dp0.."
where py >nul 2>nul
if %ERRORLEVEL% EQU 0 (
  py -3 desktop_app\launch.py
) else (
  python desktop_app\launch.py
)
if errorlevel 1 (
  echo Could not start the source application. Install Python 3.10 or newer with tkinter,
  echo or download the standalone PCRecognitionSelector Windows build from Releases.
  pause
)
