@echo off
rem rspipe deploy launcher (logic lives in scripts\deploy.ps1)
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "scripts\deploy.ps1"
if errorlevel 1 pause
