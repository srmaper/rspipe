@echo off
rem rspipe web server: kill stale instance on 8000, then keep running with auto-restart
setlocal
cd /d "%~dp0.."
call "%~dp0find_python.cmd"
if errorlevel 1 (
  echo [ERROR] Python 3.11+ not found. Please run the deploy script first.
  pause
  exit /b 1
)
for /f "tokens=5" %%p in ('netstat -aon ^| findstr :8000 ^| findstr LISTENING') do (
  taskkill /f /pid %%p >nul 2>&1
)
if not exist logs mkdir logs

:run
echo [%date% %time%] starting uvicorn >> logs\web.log
%PY% -c "import uvicorn; uvicorn.run('rspipe.web.app:app', host='0.0.0.0', port=8000, log_level='info')" >> logs\web.log 2>&1
echo [%date% %time%] exited code %errorlevel%, restarting in 3s >> logs\web.log
timeout /t 3 /nobreak >nul
goto run
