@echo off
setlocal
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup.ps1"
set "studioExit=%errorlevel%"
echo.
pause
exit /b %studioExit%
