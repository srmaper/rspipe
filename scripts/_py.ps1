# 查找可用的 Python 3.11+（dot-source 后调用 Get-RspipePython）
function Get-RspipePython {
    $candidates = @()

    if (Get-Command py -ErrorAction SilentlyContinue) {
        foreach ($v in @('3.13', '3.12', '3.11')) {
            $exe = & py "-$v" -c "import sys; print(sys.executable)" 2>$null
            if ($LASTEXITCODE -eq 0 -and $exe) { $candidates += $exe.Trim() }
        }
    }

    $sys = Get-Command python -ErrorAction SilentlyContinue
    if ($sys) {
        $exe = & python -c "import sys; print(sys.executable)" 2>$null
        if ($LASTEXITCODE -eq 0 -and $exe) { $candidates += $exe.Trim() }
    }

    foreach ($exe in $candidates) {
        $ok = & $exe -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)" 2>$null
        if ($LASTEXITCODE -eq 0) { return $exe }
    }
    return $null
}

function Set-RspipeConsole {
    try {
        chcp 65001 > $null
        [Console]::OutputEncoding = [System.Text.Encoding]::UTF8
        $script:OutputEncoding = [System.Text.Encoding]::UTF8
    } catch { }
}
