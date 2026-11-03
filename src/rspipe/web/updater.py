"""自动更新：检查 → 用户确认后下载 → 用户确认后静默安装 → 重启。

状态会落盘到 data/updates/state.json，程序重启后仍能识别已下载的安装包，
重试时不会重复下载。
"""

from __future__ import annotations

import json
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
_root: Path | None = None
_state: dict = {
    "status": "idle",  # idle | downloading | ready | installing | error
    "stage": "",  # 给前端显示当前阶段
    "progress": 0.0,
    "bytes": 0,
    "total": 0,
    "speed": 0,  # B/s
    "error": "",
    "path": "",
    "version": "",
    "retries": 0,
}

CHUNK = 256 * 1024


def set_data_root(data_root: Path) -> None:
    """由 app 启动时调用一次，之后状态可以落盘/恢复。"""
    global _root
    _root = Path(data_root)
    _restore()


def _state_file() -> Path | None:
    return (_root / "updates" / "state.json") if _root else None


def _persist() -> None:
    f = _state_file()
    if f is None:
        return
    try:
        f.parent.mkdir(parents=True, exist_ok=True)
        keep = {k: _state[k] for k in ("status", "path", "version", "total", "bytes", "progress")}
        f.write_text(json.dumps(keep, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


def _restore() -> None:
    f = _state_file()
    if f is None or not f.exists():
        return
    try:
        data = json.loads(f.read_text(encoding="utf-8"))
    except Exception:
        return
    path = Path(data.get("path") or "")
    if data.get("status") in ("ready", "installing") and path.is_file():
        with _lock:
            _state.update(
                status="ready",
                stage="安装包已就绪",
                path=str(path),
                version=data.get("version") or "",
                total=int(data.get("total") or path.stat().st_size),
                bytes=int(data.get("bytes") or path.stat().st_size),
                progress=1.0,
            )
    else:
        with _lock:
            _state.update(status="idle", stage="", path="", version="", progress=0.0)


def state() -> dict:
    with _lock:
        return dict(_state)


def _set(**kw) -> None:
    with _lock:
        _state.update(kw)
    _persist()


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


def _pkg_path(name: str, version: str) -> Path | None:
    if _root is None:
        return None
    return _root / "updates" / (name or f"rspipe-{version}-setup.exe")


def existing_package(version: str, name: str, expect_size: int = 0) -> Path | None:
    """本地是否已有该版本的完整安装包（大小对得上）。"""
    p = _pkg_path(name, version)
    if p is None or not p.is_file():
        return None
    if expect_size and p.stat().st_size != expect_size:
        return None
    return p


def start_download(
    data_root: Path, urls: str | list[str], name: str, version: str, expect_size: int = 0
) -> dict:
    """用户确认后才调用。已有完整包则直接置为 ready，不重复下载。"""
    if isinstance(urls, str):
        urls = [urls]
    urls = [u for u in urls if u]

    have = existing_package(version, name, expect_size)
    if have is not None:
        _set(
            status="ready",
            stage="安装包已就绪",
            path=str(have),
            version=version,
            total=have.stat().st_size,
            bytes=have.stat().st_size,
            progress=1.0,
            error="",
        )
        return state()

    with _lock:
        if _state["status"] == "downloading" and _state["version"] == version:
            return dict(_state)
        _state.update(
            status="downloading",
            stage="正在下载安装包",
            progress=0.0,
            bytes=0,
            total=expect_size,
            speed=0,
            error="",
            path="",
            version=version,
            retries=0,
        )
    threading.Thread(
        target=_download, args=(Path(data_root), urls, name, version, expect_size), daemon=True
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
    t0 = time.time()
    base = done
    with urllib.request.urlopen(req, timeout=60) as r:
        if r.status == 200:  # 服务端不支持续传，重来
            done = 0
            base = 0
        total = done + int(r.headers.get("Content-Length") or 0)
        _set(total=total, bytes=done)
        with tmp.open("ab" if done else "wb") as f:
            while True:
                chunk = r.read(CHUNK)
                if not chunk:
                    break
                f.write(chunk)
                done += len(chunk)
                elapsed = max(0.2, time.time() - t0)
                speed = int((done - base) / elapsed)
                _set(bytes=done, progress=(done / total) if total else 0.0, speed=speed)


def _download(data_root: Path, urls: list[str], name: str, version: str, expect_size: int) -> None:
    dest = _pkg_path(name, version)
    if dest is None:
        _set(status="error", stage="下载失败", error="数据目录未初始化")
        return
    tmp = dest.with_suffix(".part")
    dest.parent.mkdir(parents=True, exist_ok=True)
    last: Exception | None = None
    retries = 0
    deadline = time.time() + 600  # 网络抖动时最多重试 10 分钟（断点续传）
    while time.time() < deadline:
        url = urls[retries % len(urls)]
        try:
            _fetch(url, tmp)
            size = tmp.stat().st_size
            if size <= 0:
                raise RuntimeError("下载得到空文件")
            if expect_size and size != expect_size:
                raise RuntimeError(f"安装包大小不符（{size} != {expect_size}）")
            tmp.replace(dest)  # 完整下完才替换，避免半包
            _set(
                status="ready",
                stage="安装包已就绪",
                path=str(dest),
                progress=1.0,
                bytes=size,
                total=size,
                speed=0,
                error="",
            )
            return
        except Exception as exc:
            last = exc
            if isinstance(exc, urllib.error.HTTPError) and exc.code in (403, 404):
                retries += 1
                continue
            retries += 1
            _set(retries=retries, stage=f"下载中断，第 {retries} 次重试…")
            time.sleep(min(20, 3 * retries))
    _set(
        status="error",
        stage="下载失败",
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
    """用户确认后安装：启动独立批处理，等本进程退出后静默安装并重启。"""
    st = state()
    exe = installed_exe()
    if exe is None:
        return {"ok": False, "error": "当前不是安装版，无法自动安装"}
    setup = Path(st["path"]) if st["path"] else None
    if st["status"] != "ready" or setup is None or not setup.exists():
        return {"ok": False, "error": "安装包未就绪，请先下载"}

    bat = _write_bat(setup, exe, os.getpid())
    if not _run_via_schtasks(bat):
        _spawn_bat(bat)
    _set(status="installing", stage="正在安装…")
    threading.Timer(0.8, lambda: os._exit(0)).start()
    return {"ok": True, "restarting": True}
