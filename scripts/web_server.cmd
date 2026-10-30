@echo off
rem rspipe web 启动：杀占用 8000 的旧实例 → 常驻运行，崩溃自动拉起
setlocal
cd /d "%~dp0.."
for /f "tokens=5" %%p in ('netstat -aon ^| findstr :8000 ^| findstr LISTENING') do (
  taskkill /f /pid %%p >nul 2>&1
)
if not exist logs mkdir logs

:run
echo [%date% %time%] starting uvicorn >> logs\web.log
python -c "import uvicorn; uvicorn.run('rspipe.web.app:app', host='0.0.0.0', port=8000, log_level='info')" >> logs\web.log 2>&1
echo [%date% %time%] exited code %errorlevel%, restarting in 3s >> logs\web.log
timeout /t 3 /nobreak >nul
goto run
