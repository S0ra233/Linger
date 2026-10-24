@echo off
chcp 65001 >nul
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\initialize.ps1"
set "RP_INIT_EXIT=%ERRORLEVEL%"
pause
exit /b %RP_INIT_EXIT%
