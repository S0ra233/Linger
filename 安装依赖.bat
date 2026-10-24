@echo off
chcp 65001 >nul
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\setup.ps1"
set "RP_INSTALL_EXIT=%ERRORLEVEL%"
if errorlevel 1 echo 安装未完成，请查看上方原因。
pause
exit /b %RP_INSTALL_EXIT%
