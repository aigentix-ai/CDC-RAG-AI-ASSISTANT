@echo off
title CDC Regulatory Assistant Demo Launcher
color 0B
echo ==============================================================================
echo       CDC Regulatory Compliance RAG Assistant - Live Demo Launcher
echo ==============================================================================
echo.
echo [1/3] Verifying Python Environment...
if not exist ".venv\Scripts\python.exe" (
    echo [ERROR] Virtual environment not found at .venv\Scripts\python.exe!
    pause
    exit /b 1
)

echo [2/3] Starting Backend Server (Flask + Gemini 2.5 Flash RAG Engine)...
start "CDC Regulatory Assistant Backend" cmd /k "cd /d "%~dp0" && set PYTHONIOENCODING=utf-8 && .venv\Scripts\python.exe backend/app.py"

echo [3/3] Waiting for server to initialize...
timeout /t 3 /nobreak > nul

echo.
echo Opening User Assistant and Admin Dashboard in browser...
start http://127.0.0.1:5000
start http://127.0.0.1:5000/admin

echo.
echo ==============================================================================
echo [SUCCESS] CDC Regulatory Assistant is LIVE!
echo.
echo   * User Assistant UI:  http://127.0.0.1:5000
echo   * Admin Dashboard:    http://127.0.0.1:5000/admin
echo   * Admin Password:     cdc-admin-2026
echo.
echo Keep this window or the backend console open during your presentation.
echo Press any key to exit this launcher window.
echo ==============================================================================
pause > nul
