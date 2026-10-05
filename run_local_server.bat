@echo off
title CDC Regulatory AI Assistant - Local Persistent Server
color 0A

:: Set root directory
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8

echo ==============================================================================
echo        CDC Regulatory AI Assistant - Local Persistent Server Watchdog
echo ==============================================================================
echo.

:: Check for virtual environment
if not exist ".venv\Scripts\python.exe" (
    echo [ERROR] Virtualenv not found at .venv\Scripts\python.exe!
    echo Please run: python -m venv .venv
    pause
    exit /b 1
)

echo [OK] Python environment verified.
echo [INFO] Starting Flask backend on http://127.0.0.1:5000 ...
echo [INFO] Watchdog active: If the server terminates unexpectedly, it will auto-restart.
echo.

:server_loop
echo [%DATE% %TIME%] Starting Flask Server...
.venv\Scripts\python.exe backend/app.py
set EXIT_CODE=%ERRORLEVEL%

echo.
echo [%DATE% %TIME%] [WARNING] Flask server exited (Exit code: %EXIT_CODE%).
echo Restarting server in 2 seconds... (Press Ctrl+C to stop watchdog)
timeout /t 2 /nobreak > nul
echo.
goto server_loop
