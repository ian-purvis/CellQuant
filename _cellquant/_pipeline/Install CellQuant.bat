@echo off
setlocal EnableExtensions
title CellQuant — install
cd /d "%~dp0"

echo.
echo  CellQuant installer
echo  -------------------
echo  Installs TWO environments: Cellpose-SAM v4 and classic Cellpose v3.
echo  You choose the v4 folder; v3 is created beside it as ^<name^>-v3.
echo  On NVIDIA GPUs, CUDA PyTorch is installed automatically.
echo  Press Enter at the prompt to use the default folder.
echo.

REM Optional: pass a folder as the first argument to skip the prompt.
REM Example: Install CellQuant.bat "D:\Software\cellquant-napari"
if "%~1"=="" (
  powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\install_windows.ps1"
) else (
  powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\install_windows.ps1" -Prefix "%~1"
)
set "EXITCODE=%ERRORLEVEL%"

echo.
if "%EXITCODE%"=="0" (
  echo  Install finished. Double-click Open CellQuant.bat and pick v3 or v4.
  echo  Paths were saved in cellquant_env.json.
) else (
  echo  Install failed (exit code %EXITCODE%).
  echo  A popup should have explained the problem.
  echo  Common fix: run again and press Enter for the default folder on THIS PC.
  echo  Do not use a path under another user's C:\Users\... folder.
  echo  See README.md for requirements ^(conda / Miniconda^).
)
echo.
pause
exit /b %EXITCODE%
