@echo off
setlocal
title CellQuant
cd /d "%~dp0"

echo.
echo  Starting CellQuant...
echo  If this is the first time, run Install CellQuant.bat first.
echo.

powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0packaging\windows\launch_cellquant.ps1" -Engine ask
set "EXITCODE=%ERRORLEVEL%"

if not "%EXITCODE%"=="0" (
  echo.
  echo  CellQuant stopped with an error ^(exit code %EXITCODE%^).
  echo  Common fixes:
  echo    1. Double-click Install CellQuant.bat and choose [U] Update
  echo    2. Install Miniforge or Miniconda, then run Install CellQuant.bat
  echo.
  pause
  exit /b %EXITCODE%
)
endlocal
