"""自动更新：后台下载安装包 → 等旧进程退出 → 静默覆盖安装 → 重新启动。"""

from __future__ import annotations

import contextlib
import os
import subprocess
import sys
import tempfile
import threading
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


def start_download(data_root: Path, url: str, name: str, version: str) -> dict:
    with _lock:
        if _state["status"] == "downloading":
            return dict(_state)
        _state.update(
            status="downloading",
            progress=0.0,
            bytes=0,
            total=0,
            error="",
            path="",
            version=version,
        )
    threading.Thread(
        target=_download, args=(Path(data_root), url, name, version), daemon=True
    ).start()
    return state()


def _download(data_root: Path, url: str, name: str, version: str) -> None:
    dest_dir = data_root / "updates"
    dest = dest_dir / (name or f"rspipe-{version}-setup.exe")
    tmp = dest.with_suffix(".part")
    try:
        dest_dir.mkdir(parents=True, exist_ok=True)
        req = urllib.request.Request(url, headers={"User-Agent": "rspipe-updater"})
        done = 0
        with urllib.request.urlopen(req, timeout=30) as r, tmp.open("wb") as f:
            total = int(r.headers.get("Content-Length") or 0)
            _set(total=total)
            while True:
                chunk = r.read(CHUNK)
                if not chunk:
                    break
                f.write(chunk)
                done += len(chunk)
                _set(bytes=done, progress=(done / total) if total else 0.0)
        tmp.replace(dest)  # 完整下完才替换，避免半包
        _set(status="ready", path=str(dest), progress=1.0)
    except Exception as exc:
        with contextlib.suppress(Exception):
            tmp.unlink(missing_ok=True)
        _set(status="error", error=f"{type(exc).__name__}: {exc}")


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
        'del "%~f0"\r\n'
    )
    bat.write_text(text, encoding="mbcs", newline="")
    return bat


def apply(data_root: Path) -> dict:
    """启动独立批处理：等本进程退出后静默安装并重启；随后本进程自行退出。"""
    st = state()
    exe = installed_exe()
    if exe is None:
        return {"ok": False, "error": "当前不是安装版，无法自动安装"}
    if st["status"] != "ready":
        return {"ok": False, "error": "安装包尚未下载完成"}
    setup = Path(st["path"])
    if not setup.exists():
        return {"ok": False, "error": "安装包不存在，请重新下载"}

    bat = _write_bat(setup, exe, os.getpid())
    flags = 0
    if hasattr(subprocess, "DETACHED_PROCESS"):
        flags |= subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    # 脱离作业对象，父进程退出后批处理才能继续把安装跑完
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
    _set(status="installing")
    threading.Timer(0.8, lambda: os._exit(0)).start()
    return {"ok": True, "restarting": True}
