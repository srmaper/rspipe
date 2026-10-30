param()

$ErrorActionPreference = 'Continue'
. "$PSScriptRoot\_py.ps1"
Set-RspipeConsole

$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

Write-Host "============================================"
Write-Host "  rspipe 一键启动服务"
Write-Host "  地址: http://127.0.0.1:8000"
Write-Host "  关闭本窗口即停止服务"
Write-Host "============================================"
Write-Host ""

if (-not (Get-RspipePython)) {
    Write-Host "[错误] 未找到 Python 3.11+，请先双击「一键部署环境.bat」" -ForegroundColor Red
    Read-Host "按回车键退出"
    exit 1
}

if (-not (Test-Path (Join-Path $root 'web\dist\index.html'))) {
    Write-Host "[提示] 未找到前端构建产物，尝试构建..."
    if (-not (Get-Command npm.cmd -ErrorAction SilentlyContinue)) {
        Write-Host "[错误] 缺少 web\dist 且未安装 npm，请先运行「一键部署环境.bat」" -ForegroundColor Red
        Read-Host "按回车键退出"
        exit 1
    }
    Push-Location (Join-Path $root 'web')
    & npm install --registry=https://registry.npmmirror.com
    & npm run build
    Pop-Location
}

Start-Job -ScriptBlock {
    Start-Sleep -Seconds 4
    Start-Process "http://127.0.0.1:8000/"
} | Out-Null

& cmd /c "`"$PSScriptRoot\web_server.cmd`""
