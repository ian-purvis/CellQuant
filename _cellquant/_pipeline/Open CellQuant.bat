@echo off
setlocal
title CellQuant — napari
cd /d "%~dp0"

echo.
echo  Starting CellQuant (napari)...
echo  If this is the first time, run Install CellQuant.bat first.
echo  Tip: if Cellpose-SAM v4 is too heavy, choose classic v3 when prompted.
echo.

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\launch_napari.ps1" -Engine ask
set "EXITCODE=%ERRORLEVEL%"

if not "%EXITCODE%"=="0" (
  echo.
  echo  Launch failed (exit code %EXITCODE%).
  echo  Common fixes:
  echo    1. Double-click Install CellQuant.bat and choose an install folder
  echo    2. Install Miniconda/Miniforge and reopen this window
  echo    3. See README.md — "How to open it"
  echo.
  pause
  exit /b %EXITCODE%
)

endlocal
