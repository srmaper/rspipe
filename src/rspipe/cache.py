"""磁盘缓存：STAC 检索结果与 AOI 云量实测值。

为什么需要：
- STAC 检索一次 1~3 s，同一 AOI 反复检索（切页面、改波段）完全没必要重打；
- AOI 实测云量要现读 QA COG，单景 5~8 s，更不该重复；
- 进程内存缓存重启即失效，所以落 sqlite。

位置：`<data_root>/index/search_cache.sqlite`（data_root 取 `RSPIPE_DATA_ROOT`，默认 `./data`）。
读写失败时静默降级为「不缓存」，不影响主流程。
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

DEFAULT_TTL = float(os.environ.get("RSPIPE_CACHE_TTL", str(24 * 3600)))


def default_path() -> Path:
    root = os.environ.get("RSPIPE_DATA_ROOT")
    base = Path(root) if root else Path("data")
    return base / "index" / "search_cache.sqlite"


class DiskCache:
    """极简 key-value 缓存（JSON 值 + 创建时间）。"""

    def __init__(self, path: Path | str, ttl: float = DEFAULT_TTL) -> None:
        self.path = Path(path)
        self.ttl = ttl
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False, timeout=10)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, body TEXT, created REAL)"
        )
        self._conn.commit()

    def get(self, key: str) -> Any | None:
        with self._lock:
            row = self._conn.execute("SELECT body, created FROM kv WHERE key=?", (key,)).fetchone()
        if not row:
            return None
        body, created = row
        if self.ttl > 0 and time.time() - created > self.ttl:
            return None
        try:
            return json.loads(body)
        except ValueError:
            return None

    def put(self, key: str, value: Any) -> None:
        body = json.dumps(value, ensure_ascii=False, default=str)
        with self._lock:
            self._conn.execute(
                "INSERT INTO kv(key, body, created) VALUES(?,?,?) "
                "ON CONFLICT(key) DO UPDATE SET body=excluded.body, created=excluded.created",
                (key, body, time.time()),
            )
            self._conn.commit()

    def purge(self) -> None:
        if self.ttl <= 0:
            return
        with self._lock:
            self._conn.execute("DELETE FROM kv WHERE created < ?", (time.time() - self.ttl,))
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()


class NullCache:
    """缓存不可用时的空实现（只读文件系统等）。"""

    path: Path | None = None
    ttl = 0.0

    def get(self, key: str) -> Any | None:
        return None

    def put(self, key: str, value: Any) -> None:
        return None

    def purge(self) -> None:
        return None

    def close(self) -> None:
        return None


_CACHES: dict[str, DiskCache | NullCache] = {}
_CACHES_LOCK = threading.Lock()


def get_cache(path: Path | str | None = None, ttl: float | None = None) -> DiskCache | NullCache:
    """按路径复用同一个连接；打不开就返回 NullCache。"""
    target = Path(path) if path is not None else default_path()
    key = str(target.resolve())
    with _CACHES_LOCK:
        cache = _CACHES.get(key)
        if cache is not None:
            return cache
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            cache = DiskCache(target, ttl if ttl is not None else DEFAULT_TTL)
        except Exception:
            cache = NullCache()
        _CACHES[key] = cache
        return cache


def reset_caches() -> None:
    """测试用：关闭并清空实例表。"""
    with _CACHES_LOCK:
        for cache in _CACHES.values():
            cache.close()
        _CACHES.clear()


__all__ = ["DEFAULT_TTL", "DiskCache", "NullCache", "default_path", "get_cache", "reset_caches"]
