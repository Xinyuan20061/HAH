@echo off
setlocal
cd /d "%~dp0"
echo HealthMate Worker watchdog started. Press Ctrl+C to stop.
:restart
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" worker.py
) else (
  python worker.py
)
echo Worker exited with code %ERRORLEVEL%; restarting in 5 seconds.
timeout /t 5 /nobreak >nul
goto restart
