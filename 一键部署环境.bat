@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
title rspipe - 一键部署环境

echo ============================================
echo   rspipe 一键部署环境
echo   1) 安装 Python 依赖 (pip install -e .[dev])
echo   2) 安装并构建前端 (npm install ^&^& npm run build)
echo ============================================
echo.

where python >nul 2>&1
if errorlevel 1 (
  echo [错误] 未找到 python，请先安装 Python 3.11+ 并加入 PATH
  pause
  exit /b 1
)
python -c "import sys; sys.exit(0 if sys.version_info>=(3,11) else 1)"
if errorlevel 1 (
  echo [错误] Python 版本过低，需要 3.11+
  python -V
  pause
  exit /b 1
)
echo [1/2] 安装 Python 依赖...
python -m pip install -e ".[dev]"
if errorlevel 1 (
  echo [错误] Python 依赖安装失败
  pause
  exit /b 1
)

where npm.cmd >nul 2>&1
if errorlevel 1 (
  echo [跳过] 未检测到 npm，保留现有 web\dist
) else (
  echo.
  echo [2/2] 安装并构建前端...
  pushd web
  call npm install --registry=https://registry.npmmirror.com
  if errorlevel 1 (
    echo [警告] npm install 失败，保留现有 dist
  ) else (
    call npm run build
    if errorlevel 1 echo [警告] 前端构建失败，保留现有 dist
  )
  popd
)

echo.
echo ============================================
echo   部署完成。双击「一键启动服务.cmd」启动平台
echo ============================================
pause
