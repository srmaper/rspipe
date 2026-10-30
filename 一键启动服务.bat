@echo off
rem rspipe start launcher (logic lives in scripts\start.ps1)
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "scripts\start.ps1"
if errorlevel 1 pause
