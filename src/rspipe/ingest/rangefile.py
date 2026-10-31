"""基于 HTTP Range 的可 seek 远程文件对象。

让 rasterio / GDAL 通过普通 Python file-like 读取远程 COG，
只按需拉取字节区间——绕开 Windows 上 GDAL `/vsicurl/` 的 schannel TLS 故障，
同时避免整文件下载。

    remote = RangeFile("https://.../B04.tif")
    with rasterio.open(remote) as src:
        data = src.read(1, window=...)   # 只取窗口命中的块

实现：块级缓存（默认 256 KiB）+ httpx 连接池。
"""

from __future__ import annotations

import contextlib
import io
import os
import threading
from collections import OrderedDict
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path
from typing import Any

import httpx

from rspipe.errors import DownloadError, JobCancelledError
from rspipe.logging import get_logger

log = get_logger("rspipe.ingest.rangefile")

DEFAULT_BLOCK = 1 << 18  # 256 KiB
DEFAULT_CACHE_BLOCKS = 512  # 128 MiB 上限
_TIMEOUT = 120.0

# 单连接经常被限速（实测 14 KB/s），并发才是吞吐来源。
# 全局连接上限防止多波段并发把对端打挂。
MAX_CONNECTIONS = max(4, int(os.environ.get("RSPIPE_MAX_CONNECTIONS", "32")))
DEFAULT_PREFETCH = int(os.environ.get("RSPIPE_PREFETCH_BLOCKS", "8"))

_CONN_SEM = threading.BoundedSemaphore(MAX_CONNECTIONS)
_POOL = ThreadPoolExecutor(max_workers=MAX_CONNECTIONS, thread_name_prefix="rsrange")

# Range 字节流进度钩子（web 任务条用）：fn(nbytes)
progress_hook = None


def set_progress_hook(fn) -> None:
    global progress_hook
    progress_hook = fn


class RangeFile(io.RawIOBase):
    """可 seek 的远程文件（HTTP Range + 块缓存）。"""

    def __init__(
        self,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        block_size: int = DEFAULT_BLOCK,
        max_cached_blocks: int = DEFAULT_CACHE_BLOCKS,
        client: httpx.Client | None = None,
        prefetch: int = DEFAULT_PREFETCH,
        abort_check: Callable[[], bool] | None = None,
    ) -> None:
        self.url = url
        self._is_remote = url.startswith(("http://", "https://"))
        self.headers = dict(headers or {})
        self.block_size = int(block_size)
        self.max_cached_blocks = int(max_cached_blocks)
        self.prefetch = int(prefetch)
        self._abort_check = abort_check
        self._client = client or httpx.Client(
            timeout=httpx.Timeout(connect=15.0, read=30.0, write=30.0, pool=30.0),
            follow_redirects=True,
            limits=httpx.Limits(
                max_connections=MAX_CONNECTIONS, max_keepalive_connections=MAX_CONNECTIONS
            ),
        )
        self._owns_client = client is None
        self._pos = 0
        self._size: int | None = None
        self._cache: OrderedDict[int, bytes] = OrderedDict()
        self._inflight: dict[int, Future] = {}
        self._lock = threading.Lock()
        self._blocks_read = 0
        self._bytes_read = 0

    # ── io.RawIOBase ──────────────────────────────────────────────────────
    def readable(self) -> bool:
        return True

    def seekable(self) -> bool:
        return True

    def writable(self) -> bool:
        return False

    @property
    def closed(self) -> bool:
        return self._owns_client and self._client.is_closed

    def close(self) -> None:
        with self._lock:
            self._inflight.clear()
        if self._owns_client:
            self._client.close()
        self._cache.clear()

    def tell(self) -> int:
        return self._pos

    def seek(self, offset: int, whence: int = io.SEEK_SET) -> int:
        if whence == io.SEEK_SET:
            self._pos = max(0, int(offset))
        elif whence == io.SEEK_CUR:
            self._pos = max(0, self._pos + int(offset))
        elif whence == io.SEEK_END:
            self._pos = max(0, self.size + int(offset))
        else:
            raise ValueError(f"invalid whence: {whence}")
        return self._pos

    def read(self, size: int = -1) -> bytes:
        if size is None or size < 0:
            size = self.size - self._pos
        size = max(0, min(int(size), self.size - self._pos))
        if size == 0:
            return b""
        out = self._read_range(self._pos, self._pos + size)
        self._pos += len(out)
        return out

    def readinto(self, b: Any) -> int:
        data = self.read(len(b))
        b[: len(data)] = data
        return len(data)

    def read_at(self, offset: int, size: int) -> bytes:
        """按绝对偏移读取（不移动文件指针）。"""
        return self._read_range(offset, offset + size)

    # ── 元数据 ────────────────────────────────────────────────────────────
    @property
    def size(self) -> int:
        if self._size is None:
            self._size = self._probe_size()
        return self._size

    def _probe_size(self) -> int:
        if not self._is_remote:
            return Path(self.url).stat().st_size
        try:
            resp = self._client.head(self.url, headers=self.headers)
            if resp.status_code >= 400:
                raise DownloadError(f"HEAD {resp.status_code} for {self.url}")
            cl = resp.headers.get("content-length")
            if cl and cl.isdigit():
                return int(cl)
        except httpx.HTTPError as exc:
            raise DownloadError(f"HEAD failed for {self.url}: {exc}") from exc
        # 退化：用 1 字节 Range 探测
        try:
            resp = self._client.get(self.url, headers={**self.headers, "Range": "bytes=0-0"})
            cr = resp.headers.get("content-range", "")
            if "/" in cr:
                return int(cr.rsplit("/", 1)[1])
        except (httpx.HTTPError, ValueError) as exc:
            raise DownloadError(f"size probe failed for {self.url}: {exc}") from exc
        raise DownloadError(f"cannot determine size of {self.url}")

    # ── 块缓存读取 ────────────────────────────────────────────────────────
    def _read_range(self, start: int, end: int) -> bytes:
        end = min(end, self.size)
        if start >= end:
            return b""
        parts: list[bytes] = []
        first_block = start // self.block_size
        last_block = (end - 1) // self.block_size
        for blk in range(first_block, last_block + 1):
            chunk = self._get_block(blk)
            blk_start = blk * self.block_size
            lo = max(start, blk_start) - blk_start
            hi = min(end, blk_start + len(chunk)) - blk_start
            parts.append(chunk[lo:hi])
        return b"".join(parts)

    def _get_block(self, index: int) -> bytes:
        with self._lock:
            cached = self._cache.get(index)
            if cached is not None:
                self._cache.move_to_end(index)
                return cached
            pending = self._inflight.get(index)

        if pending is not None:
            data = pending.result()
        else:
            data = self._fetch_block(index)
            self._store_block(index, data)

        self._prefetch_after(index)
        return data

    def _store_block(self, index: int, data: bytes) -> None:
        with self._lock:
            self._cache[index] = data
            self._cache.move_to_end(index)
            self._inflight.pop(index, None)
            while len(self._cache) > self.max_cached_blocks:
                self._cache.popitem(last=False)
            self._blocks_read += 1
            self._bytes_read += len(data)
        if progress_hook is not None:
            with contextlib.suppress(Exception):
                progress_hook(len(data), True)

    def _prefetch_after(self, index: int) -> None:
        """顺序读时把后续块丢进线程池，把单连接串行等待变成并发流水。"""
        if not self._is_remote or self.prefetch <= 0:
            return
        size = self.size
        with self._lock:
            for nxt in range(index + 1, index + 1 + self.prefetch):
                if nxt * self.block_size >= size:
                    break
                if nxt in self._cache or nxt in self._inflight:
                    continue
                self._inflight[nxt] = _POOL.submit(self._fetch_and_store, nxt)

    def _fetch_and_store(self, index: int) -> bytes:
        try:
            data = self._fetch_block(index)
        except Exception:
            with self._lock:
                self._inflight.pop(index, None)
            raise
        self._store_block(index, data)
        return data

    def _fetch_block(self, index: int) -> bytes:
        start = index * self.block_size
        end = min(start + self.block_size, self.size) - 1
        if not self._is_remote:
            with Path(self.url).open("rb") as fh:
                fh.seek(start)
                return fh.read(end - start + 1)

        import time as _time

        last_exc: Exception | None = None
        for attempt in range(1, 6):
            try:
                headers = {**self.headers, "Range": f"bytes={start}-{end}"}
                buf = bytearray()
                with _CONN_SEM, self._client.stream("GET", self.url, headers=headers) as resp:
                    if resp.status_code not in (200, 206):
                        last_exc = DownloadError(
                            f"HTTP {resp.status_code} for {self.url} ({start}-{end})"
                        )
                        if resp.status_code < 500 and resp.status_code != 429:
                            raise last_exc
                        continue
                    for chunk in resp.iter_bytes(65536):
                        if self._abort_check is not None and self._abort_check():
                            raise JobCancelledError("job paused")
                        buf.extend(chunk)
                        if progress_hook is not None:
                            with contextlib.suppress(Exception):
                                progress_hook(len(chunk), False)
                data = bytes(buf)
                # 服务端若忽略 Range 返回全量，裁剪到本块
                if resp.status_code == 200 and len(data) > self.block_size:
                    data = data[start : end + 1]
                return data
            except httpx.HTTPError as exc:
                last_exc = exc
                log.warning(
                    "range.retry",
                    url=self.url.rsplit("/", 1)[-1],
                    block=index,
                    attempt=attempt,
                    error=type(exc).__name__,
                )
            if attempt < 5:
                wait = min(10.0, 0.5 * (2 ** (attempt - 1)))
                _time.sleep(wait)
        raise DownloadError(f"range fetch failed for {self.url}: {last_exc}")

    # ── 诊断 ──────────────────────────────────────────────────────────────
    @property
    def stats(self) -> dict[str, Any]:
        return {
            "url": self.url,
            "size": self.size,
            "block_size": self.block_size,
            "blocks_read": self._blocks_read,
            "bytes_read": self._bytes_read,
            "cached_blocks": len(self._cache),
        }

    def __repr__(self) -> str:  # pragma: no cover
        return f"<RangeFile {self.url[:60]} size={self.size}>"


def open_remote(
    url: str,
    *,
    headers: dict[str, str] | None = None,
    block_size: int = DEFAULT_BLOCK,
    prefetch: int = DEFAULT_PREFETCH,
) -> RangeFile:
    """构造 RangeFile（调用方负责 close）。"""
    return RangeFile(url, headers=headers, block_size=block_size, prefetch=prefetch)


__all__ = ["RangeFile", "open_remote"]
