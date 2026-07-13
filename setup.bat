@echo off
REM SOC Master Widget — one-click environment bootstrap.
REM Installs everything the SOC Ultralight stack needs if not already present:
REM   1. SOC Ultralight itself (cloned next to this folder if missing)
REM   2. SOC's Python dependencies (pip, from SOC's requirements.txt)
REM   3. Tesseract OCR (the one system binary SOC needs)
REM The widget itself needs NOTHING beyond Python stdlib.
cd /d "%~dp0"

where py >nul 2>nul
if errorlevel 1 (
    echo Python 3.10+ is required first: https://www.python.org/downloads/
    pause & exit /b 1
)

REM ── 1. SOC Ultralight ────────────────────────────────────────────────
if not exist "..\SOC_Ultralight" (
    where git >nul 2>nul
    if errorlevel 1 (
        echo git not found — install git or clone SOC manually next to this folder:
        echo   https://github.com/BaxtersLab2/SOC_Ultralight
    ) else (
        echo Cloning SOC Ultralight...
        git clone https://github.com/BaxtersLab2/SOC_Ultralight "..\SOC_Ultralight"
    )
) else (
    echo SOC Ultralight: found.
)

REM ── 2. Python dependencies ───────────────────────────────────────────
if exist "..\SOC_Ultralight\requirements.txt" (
    echo Installing SOC Python dependencies...
    py -3 -m pip install -r "..\SOC_Ultralight\requirements.txt"
) else (
    echo Installing SOC Python dependencies ^(inline list^)...
    py -3 -m pip install pyautogui pyperclip pywin32 mss Pillow requests pytesseract opencv-python numpy
)
py -3 -m pip install psutil

REM ── 3. Tesseract OCR binary ──────────────────────────────────────────
where tesseract >nul 2>nul
if not errorlevel 1 goto :tess_ok
if exist "C:\Program Files\Tesseract-OCR\tesseract.exe" goto :tess_ok
echo Tesseract OCR not found — attempting winget install...
winget install --id UB-Mannheim.TesseractOCR -e --silent
if errorlevel 1 (
    echo winget failed — install manually: https://github.com/UB-Mannheim/tesseract/wiki
)
:tess_ok
echo.
echo Setup complete. Run run.bat to open the board.
pause
