@echo off
REM SOC Master Widget — zero-dependency launcher board (Python stdlib only).
cd /d "%~dp0"
where py >nul 2>nul
if errorlevel 1 (
    echo Python 3.10+ is required: https://www.python.org/downloads/
    echo   ^(check "tcl/tk and IDLE" during install — it is on by default^)
    pause
    exit /b 1
)
if not exist soc_master_apps.json (
    echo First run: creating soc_master_apps.json from the example.
    copy soc_master_apps.example.json soc_master_apps.json >nul
    echo Edit soc_master_apps.json to register YOUR apps, then run again.
    pause
    exit /b 0
)
start "" pyw soc_master_widget.py
