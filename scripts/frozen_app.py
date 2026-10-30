"""打包入口：rspipe.exe 启动本地 Web 平台并自动打开浏览器。

开发态：python scripts/frozen_app.py
打包态：PyInstaller 生成 rspipe.exe（onedir），资源位于 _internal。
"""

from __future__ import annotations

import os
import socket
import sys
import threading
import webbrowser
from pathlib import Path


def _bundle_root() -> Path:
    if getattr(sys, "frozen", False):
        exe_dir = Path(sys.executable).resolve().parent
        if (exe_dir / "web" / "dist").exists() or (exe_dir / "configs").exists():
            return exe_dir
        return Path(getattr(sys, "_MEIPASS", exe_dir))
    return Path(__file__).resolve().parents[1]


def _work_root(bundle: Path) -> Path:
    if not getattr(sys, "frozen", False):
        return bundle
    base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA") or str(Path.home())
    return Path(base) / "rspipe"


def _free_port(preferred: int = 8000) -> int:
    for port in (preferred, 8001, 8002, 8080, 8888):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                s.bind(("127.0.0.1", port))
            except OSError:
                continue
            return port
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def main() -> int:
    bundle = _bundle_root()
    work = _work_root(bundle)

    for sub in ("data", "logs", "data/index", "data/raw", "data/derived", "data/analysis"):
        (work / sub).mkdir(parents=True, exist_ok=True)

    os.environ.setdefault("RSPIPE_ROOT", str(bundle))
    os.environ.setdefault("RSPIPE_DATA_ROOT", str(work / "data"))
    os.chdir(bundle)

    port = _free_port()
    url = f"http://127.0.0.1:{port}"

    print("=" * 56)
    print("  rspipe 遥感影像平台")
    print(f"  地址: {url}")
    print(f"  数据目录: {work}")
    print("  关闭本窗口即停止服务")
    print("=" * 56)
    print()

    threading.Timer(2.5, lambda: webbrowser.open(url)).start()

    import uvicorn

    from rspipe.web.app import app

    uvicorn.run(app, host="127.0.0.1", port=port, log_level="info")
    return 0


if __name__ == "__main__":
    sys.exit(main())
