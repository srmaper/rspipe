from __future__ import annotations

import time
from pathlib import Path

from rspipe.cache import DiskCache, NullCache, get_cache, reset_caches


def test_disk_cache_roundtrip(tmp_path: Path) -> None:
    cache = DiskCache(tmp_path / "c.sqlite", ttl=3600)
    cache.put("k", {"v": 0.25, "note": "云量"})
    assert cache.get("k") == {"v": 0.25, "note": "云量"}
    assert cache.get("missing") is None
    cache.close()


def test_disk_cache_expiry(tmp_path: Path) -> None:
    cache = DiskCache(tmp_path / "c.sqlite", ttl=0.01)
    cache.put("k", [1, 2, 3])
    assert cache.get("k") == [1, 2, 3]
    time.sleep(0.05)
    assert cache.get("k") is None
    cache.close()


def test_disk_cache_overwrite_and_purge(tmp_path: Path) -> None:
    cache = DiskCache(tmp_path / "c.sqlite", ttl=3600)
    cache.put("k", 1)
    cache.put("k", 2)
    assert cache.get("k") == 2
    cache.purge()
    assert cache.get("k") == 2  # 未过期，purge 不会删
    cache.close()


def test_null_cache_is_noop() -> None:
    cache = NullCache()
    cache.put("k", 1)
    assert cache.get("k") is None


def test_get_cache_reuses_instance(tmp_path: Path) -> None:
    reset_caches()
    path = tmp_path / "reuse.sqlite"
    first = get_cache(path, ttl=60)
    first.put("k", "v")
    second = get_cache(path)
    assert second.get("k") == "v"
    assert second is first
    reset_caches()
