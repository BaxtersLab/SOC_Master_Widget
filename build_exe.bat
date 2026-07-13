@echo off
REM Optional: build a single-file exe with the widget icon baked in.
REM Installs PyInstaller on demand (the ONLY dependency, build-time only).
cd /d "%~dp0"
py -3 -m PyInstaller --version >nul 2>nul || py -3 -m pip install pyinstaller
py -3 -m PyInstaller --onefile --windowed --name "SOC Master Widget" ^
    --icon "assets/master_widget.ico" --distpath "." soc_master_widget.py
echo Done: "SOC Master Widget.exe" (reads soc_master_apps.json next to itself)
