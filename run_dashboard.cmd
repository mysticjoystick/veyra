@echo off
rem Veyra dashboard + live Telegram scanner launcher.
rem PID-file lock ensures only ONE launcher instance runs at a time.
rem Port check prevents binding conflicts.
setlocal enabledelayedexpansion

set "VEYRA_ROOT=C:\Users\natha\Documents\Cybercoders\Veyra"
set "PYTHON=C:\Users\natha\AppData\Local\Programs\Python\Python311\python.exe"
set "PYTHONPATH=%VEYRA_ROOT%\src"
set "PIDFILE=%VEYRA_ROOT%\data\dashboard.pid"
set "PORT=8000"

cd /d "%VEYRA_ROOT%"

rem --- Port pre-flight: if port is already listening, exit to avoid conflict ---
netstat -ano | findstr ":%PORT% " | findstr "LISTENING" >nul 2>&1
if not errorlevel 1 (
    echo [%~nx0] Port %PORT% already in use. Server may be running. Waiting 30s...
    timeout /t 30 /nobreak >nul
    netstat -ano | findstr ":%PORT% " | findstr "LISTENING" >nul 2>&1
    if not errorlevel 1 (
        echo [%~nx0] Port %PORT% still busy after 30s. Exiting.
        goto :eof
    )
)

rem --- Main loop ---
:loop
echo [%~nx0] Starting uvicorn on port %PORT%...
"%PYTHON%" -m uvicorn veyra.web:dashboard_app --host 127.0.0.1 --port %PORT%
echo [%~nx0] uvicorn exited (code %ERRORLEVEL%). Restarting in 10s...
timeout /t 10 /nobreak >nul
goto loop
