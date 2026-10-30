@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
title rspipe - 一键启动服务

echo ============================================
echo   rspipe 一键启动服务
echo   地址: http://127.0.0.1:8000
echo   关闭本窗口即停止服务
echo ============================================
echo.

if not exist "web\dist\index.html" (
  echo [提示] 未找到前端构建产物，尝试构建...
  where npm.cmd >nul 2>&1
  if errorlevel 1 (
    echo [错误] 缺少 web\dist 且未安装 npm，请先运行「一键部署环境.cmd」
    pause
    exit /b 1
  )
  pushd web
  call npm install --registry=https://registry.npmmirror.com
  call npm run build
  popd
)

rem 2 秒后打开浏览器（等服务起来）
start "" /min cmd /c "timeout /t 4 >nul & start "" http://127.0.0.1:8000/"

call "scripts\web_server.cmd"
