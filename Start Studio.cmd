@echo off
setlocal
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0launch.ps1"
set "studioExit=%errorlevel%"
if not "%studioExit%"=="0" pause
exit /b %studioExit%
