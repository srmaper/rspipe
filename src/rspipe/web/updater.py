"""自动更新：后台下载安装包 → 等旧进程退出 → 静默覆盖安装 → 重新启动。"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

_lock = threading.Lock()
_state: dict = {
    "status": "idle",  # idle | downloading | ready | installing | error
    "progress": 0.0,
    "bytes": 0,
    "total": 0,
    "error": "",
    "path": "",
    "version": "",
    "retries": 0,
}

CHUNK = 256 * 1024


def state() -> dict:
    with _lock:
        return dict(_state)


def _set(**kw) -> None:
    with _lock:
        _state.update(kw)


def installed_exe() -> Path | None:
    """冻结版且装在 Inno 目录（同目录有 unins000.exe）才支持免交互自动安装。"""
    if not getattr(sys, "frozen", False):
        return None
    exe = Path(sys.executable)
    if (exe.parent / "unins000.exe").exists():
        return exe
    return None


def can_auto_install() -> bool:
    return installed_exe() is not None


def start_download(data_root: Path, urls: str | list[str], name: str, version: str) -> dict:
    """urls：优先尝试的地址列表（API 资源地址 → 浏览器下载地址）。"""
    with _lock:
        if _state["status"] == "downloading":
            return dict(_state)
        if (
            _state["status"] == "ready"
            and _state["version"] == version
            and _state["path"]
            and Path(_state["path"]).exists()
        ):
            return dict(_state)  # 已经下好了，不重复下载
        _state.update(
            status="downloading",
            progress=0.0,
            bytes=0,
            total=0,
            error="",
            path="",
            version=version,
            retries=0,
        )
    if isinstance(urls, str):
        urls = [urls]
    threading.Thread(
        target=_download,
        args=(Path(data_root), [u for u in urls if u], name, version),
        daemon=True,
    ).start()
    return state()


def _fetch(url: str, tmp: Path) -> None:
    """断点续传到 tmp（GitHub 支持 Range，网络抖动也不用从头下）。"""
    done = tmp.stat().st_size if tmp.exists() else 0
    headers = {"User-Agent": "rspipe-updater"}
    if "/releases/assets/" in url:
        # 走 API 资源地址时必须声明 octet-stream，否则返回 JSON 元数据
        headers["Accept"] = "application/octet-stream"
    if done:
        headers["Range"] = f"bytes={done}-"
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=60) as r:
        if r.status == 200:  # 服务端不支持续传，重来
            done = 0
        total = done + int(r.headers.get("Content-Length") or 0)
        _set(total=total, bytes=done)
        with tmp.open("ab" if done else "wb") as f:
            while True:
                chunk = r.read(CHUNK)
                if not chunk:
                    break
                f.write(chunk)
                done += len(chunk)
                _set(bytes=done, progress=(done / total) if total else 0.0)


def _download(data_root: Path, urls: list[str], name: str, version: str) -> None:
    dest_dir = data_root / "updates"
    dest = dest_dir / (name or f"rspipe-{version}-setup.exe")
    tmp = dest.with_suffix(".part")
    dest_dir.mkdir(parents=True, exist_ok=True)
    last: Exception | None = None
    retries = 0
    deadline = time.time() + 600  # 网络抖动时最多重试 10 分钟（断点续传）
    while time.time() < deadline:
        url = urls[retries % len(urls)]
        try:
            _fetch(url, tmp)
            if tmp.stat().st_size <= 0:
                raise RuntimeError("下载得到空文件")
            tmp.replace(dest)  # 完整下完才替换，避免半包
            _set(status="ready", path=str(dest), progress=1.0)
            return
        except Exception as exc:
            last = exc
            if isinstance(exc, urllib.error.HTTPError) and exc.code in (403, 404):
                retries += 1
                continue
            retries += 1
            _set(retries=retries)  # 保持 downloading，前端显示“重试中”
            time.sleep(min(20, 3 * retries))
    _set(
        status="error",
        error=f"{type(last).__name__}: {last}" if last else "下载失败",
    )


def _write_bat(setup: Path, app_exe: Path, pid: int) -> Path:
    bat = Path(tempfile.gettempdir()) / "rspipe_apply_update.bat"
    text = (
        "@echo off\r\n"
        "setlocal\r\n"
        ":wait\r\n"
        f'tasklist /FI "PID eq {pid}" 2>nul | find "{pid}" >nul\r\n'
        "if not errorlevel 1 (\r\n"
        "  ping -n 2 127.0.0.1 >nul\r\n"
        "  goto wait\r\n"
        ")\r\n"
        f'start "" /wait "{setup}" /VERYSILENT /SUPPRESSMSGBOXES /NORESTART\r\n'
        f'start "" "{app_exe}"\r\n'
        "schtasks /Delete /TN rspipe_update_apply /F >nul 2>&1\r\n"
        'del "%~f0"\r\n'
    )
    bat.write_text(text, encoding="mbcs", newline="")
    return bat


def _no_window() -> int:
    return getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)


def _run_via_schtasks(bat: Path) -> bool:
    """用计划任务启动批处理：独立于本进程树，父进程退出也不会被带走。"""
    name = "rspipe_update_apply"
    try:
        create = subprocess.run(
            [
                "schtasks",
                "/Create",
                "/TN",
                name,
                "/TR",
                f'"{bat}"',
                "/SC",
                "ONCE",
                "/ST",
                "23:59",
                "/F",
                "/RL",
                "LIMITED",
            ],
            capture_output=True,
            timeout=20,
            creationflags=_no_window(),
        )
        if create.returncode != 0:
            return False
        run = subprocess.run(
            ["schtasks", "/Run", "/TN", name],
            capture_output=True,
            timeout=20,
            creationflags=_no_window(),
        )
        return run.returncode == 0
    except Exception:
        return False


def _spawn_bat(bat: Path) -> None:
    flags = 0
    if hasattr(subprocess, "DETACHED_PROCESS"):
        flags |= subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    breakaway = getattr(subprocess, "CREATE_BREAKAWAY_FROM_JOB", 0x01000000)
    try:
        subprocess.Popen(
            ["cmd.exe", "/c", str(bat)],
            cwd=str(bat.parent),
            creationflags=flags | breakaway,
            close_fds=True,
        )
    except OSError:
        subprocess.Popen(
            ["cmd.exe", "/c", str(bat)],
            cwd=str(bat.parent),
            creationflags=flags,
            close_fds=True,
        )


def apply(data_root: Path) -> dict:
    """启动独立批处理：等本进程退出后静默安装并重启；随后本进程自行退出。"""
    st = state()
    exe = installed_exe()
    if exe is None:
        return {"ok": False, "error": "当前不是安装版，无法自动安装"}
    setup = Path(st["path"]) if st["path"] else None
    if st["status"] != "ready" or setup is None or not setup.exists():
        return {"ok": False, "error": "安装包尚未下载完成，请稍候重试"}

    bat = _write_bat(setup, exe, os.getpid())
    if not _run_via_schtasks(bat):
        _spawn_bat(bat)
    _set(status="installing")
    threading.Timer(0.8, lambda: os._exit(0)).start()
    return {"ok": True, "restarting": True}
