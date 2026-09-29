@echo off
setlocal EnableExtensions
title CellQuant - install
cd /d "%~dp0"

REM Keeps this window open so errors can be read, and writes a full log next
REM to this file (opened in Notepad if the install fails).

set "LOG=%~dp0install_last.log"
set "EXITCODE=1"

echo.
echo  CellQuant installer
echo  -------------------
echo  Checks this computer, recommends a Cellpose engine, and installs the
echo  engines you choose:
echo    Cellpose-SAM ^(Cellpose 4^): most accurate, best with an NVIDIA GPU
echo    Classic Cellpose ^(Cellpose 3^): lighter, faster without a GPU
echo  GPU support is added automatically on computers with an NVIDIA GPU.
echo  Press Enter at any question to accept the suggested answer.
echo.
echo  Log: %LOG%
echo.

REM Optional: pass a folder for the environments to skip that question.
REM Example: "Install CellQuant.bat" "D:\CellQuant"
if "%~1"=="" (
  powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0packaging\windows\install_windows.ps1" -LogPath "%LOG%"
) else (
  powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0packaging\windows\install_windows.ps1" -LogPath "%LOG%" -Parent "%~1"
)
set "EXITCODE=%ERRORLEVEL%"

echo.
if "%EXITCODE%"=="0" (
  echo  Install finished. Double-click Open CellQuant.bat to start.
) else (
  echo  Install did not finish ^(exit code %EXITCODE%^).
  echo  Opening the log in Notepad so the error can be read or copied...
  if exist "%LOG%" start "" notepad.exe "%LOG%"
)

echo.
echo  Press any key to close this window...
pause >nul
exit /b %EXITCODE%
