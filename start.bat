@echo off
title Gate Status Checker
cd /d "%~dp0"

echo ============================================================
echo            Starting Gate Status Checker
echo ============================================================
echo.

:: Check if virtual environment exists, activate it
if not exist ".venv\Scripts\activate.bat" (
    echo [!] Virtual environment not found in .venv.
    echo [*] Creating virtual environment...
    python -m venv .venv
    call .venv\Scripts\activate.bat
    echo [*] Installing dependencies...
    pip install -r backend\requirements.txt
) else (
    call .venv\Scripts\activate.bat
)

echo.
echo [*] FastAPI is configured to serve BOTH:
echo     1. Backend Endpoints (/gate, /gate/status, /api/health)
echo     2. Frontend Static Webapp (index.html, style.css, app.js)
echo.
echo [*] Server URL:     http://127.0.0.1:8000
echo [*] Local Network:  http://0.0.0.0:8000 (accessible on your phone via local Wi-Fi)
echo.
echo Press Ctrl+C in this window to stop the server.
echo.

:: Open the web application in default browser
start "" "http://127.0.0.1:8000"

:: Start Uvicorn server (host 0.0.0.0 allows phone access on same Wi-Fi)
uvicorn backend.main:app --host 0.0.0.0 --port 8000 --reload

pause
