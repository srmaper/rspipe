"""环境部署与服务控制（一键部署 / 一键启动）。"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]  # 仓库根

_state: dict = {"running": False, "ok": None, "log": [], "started": None}
_lock = threading.Lock()


def _add(msg: str) -> None:
    with _lock:
        _state["log"].append(msg)
        if len(_state["log"]) > 400:
            _state["log"] = _state["log"][-200:]


def _run(cmd: list[str], cwd: Path) -> int:
    _add("$ " + " ".join(cmd))
    try:
        p = subprocess.Popen(
            cmd,
            cwd=str(cwd),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            shell=False,
        )
        assert p.stdout is not None
        for line in p.stdout:
            line = line.rstrip()
            if line:
                _add(line)
        return p.wait()
    except FileNotFoundError:
        _add("未找到命令：" + cmd[0])
        return 127


def env_status() -> dict:
    deps = {}
    for mod in ("fastapi", "uvicorn", "rasterio", "numpy", "pyproj", "PIL"):
        try:
            __import__(mod)
            deps[mod] = True
        except Exception:
            deps[mod] = False
    node = shutil.which("node")
    npm = shutil.which("npm") or shutil.which("npm.cmd")
    dist = (ROOT / "web" / "dist" / "index.html").exists()
    return {
        "python": sys.version.split()[0],
        "python_ok": sys.version_info >= (3, 11),
        "deps": deps,
        "deps_ok": all(deps.values()),
        "node": (
            subprocess.run([node, "-v"], capture_output=True, text=True).stdout.strip()
            if node
            else None
        ),
        "npm": bool(npm),
        "dist_ok": dist,
        "setup_running": _state["running"],
        "setup_ok": _state["ok"],
        "log": _state["log"][-40:],
    }


def start_setup() -> dict:
    if _state["running"]:
        return {"ok": False, "error": "部署正在进行中"}

    def worker() -> None:
        with _lock:
            _state.update(running=True, ok=None, log=[], started=time.time())
        _add("开始部署环境…")
        py = sys.executable
        code = _run([py, "-m", "pip", "install", "-e", ".[dev]"], ROOT)
        if code != 0:
            _add("pip 安装失败")
            with _lock:
                _state.update(running=False, ok=False)
            return
        npm = shutil.which("npm.cmd") or shutil.which("npm")
        if npm and (ROOT / "web" / "package.json").exists():
            code = _run([npm, "install", "--registry=https://registry.npmmirror.com"], ROOT / "web")
            if code == 0:
                code = _run([npm, "run", "build"], ROOT / "web")
            if code != 0:
                _add("前端构建失败（不影响已有 dist）")
        else:
            _add("未检测到 npm，跳过前端构建")
        _add("部署完成")
        with _lock:
            _state.update(running=False, ok=True)

    threading.Thread(target=worker, daemon=True, name="setup").start()
    return {"ok": True, "started": True}


def restart_service() -> dict:
    """延迟 2 秒后重启服务（先返回响应，避免把当前请求的连接掐断）。"""
    cmd = ROOT / "scripts" / "web_server.cmd"
    if not cmd.exists():
        return {"ok": False, "error": "缺少 scripts/web_server.cmd"}
    flags = 0
    if os.name == "nt":
        # 这两个常量只在 Windows 上存在
        flags = int(getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)) | int(
            getattr(subprocess, "DETACHED_PROCESS", 0)
        )
    subprocess.Popen(
        ["cmd", "/c", f'timeout /t 2 >nul & "{cmd}"'],
        cwd=str(ROOT),
        creationflags=flags,
        close_fds=True,
    )
    return {"ok": True, "restarting": True}
