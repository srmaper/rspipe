"""HTTP 下载：断点续传、指数退避重试、完整性校验。"""

from __future__ import annotations

from pathlib import Path

import httpx
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from rspipe.auth import AuthLike, NoAuth
from rspipe.errors import DownloadError
from rspipe.logging import get_logger

log = get_logger("rspipe.ingest.download")

_CHUNK = 1 << 20  # 1 MiB


class _RetryableDownloadError(Exception):
    """内部可重试异常。"""


@retry(
    reraise=True,
    stop=stop_after_attempt(5),
    wait=wait_exponential(multiplier=1, min=1, max=30),
    retry=retry_if_exception_type((_RetryableDownloadError, httpx.TransportError)),
)
def _get_with_retries(
    client: httpx.Client,
    url: str,
    *,
    headers: dict[str, str],
    start: int = 0,
) -> httpx.Response:
    if start > 0:
        headers = {**headers, "Range": f"bytes={start}-"}
    resp = client.get(url, headers=headers, follow_redirects=True)
    if resp.status_code in (429, 500, 502, 503, 504):
        raise _RetryableDownloadError(f"HTTP {resp.status_code} for {url}")
    return resp


def download_file(
    url: str,
    dest: Path,
    *,
    auth: AuthLike | None = None,
    timeout: float = 120.0,
    expected_size: int | None = None,
    resume: bool = True,
) -> Path:
    """把 `url` 下载到 `dest`，支持断点续传。

    已存在且大小匹配时直接返回（幂等）。
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    auth = auth or NoAuth()

    if dest.exists() and expected_size is not None and dest.stat().st_size == expected_size:
        log.info("download.skip", dest=str(dest), reason="size-match")
        return dest

    start = dest.stat().st_size if (resume and dest.exists()) else 0
    if start:
        log.info("download.resume", dest=str(dest), start=start)

    headers = dict(auth.auth_header())
    tmp = dest.with_suffix(dest.suffix + ".part")

    try:
        with httpx.Client(timeout=timeout, follow_redirects=True) as client:
            resp = _get_with_retries(client, url, headers=headers, start=start)
            if resp.status_code == 416:
                # Range 不满足：本地可能已完整
                if dest.exists():
                    return dest
                start = 0
                resp = _get_with_retries(client, url, headers=headers, start=0)
            if resp.status_code not in (200, 206):
                raise DownloadError(f"HTTP {resp.status_code} for {url}")

            mode = "ab" if start else "wb"
            total = start
            with tmp.open(mode) as fh:
                for chunk in resp.iter_bytes(_CHUNK):
                    fh.write(chunk)
                    total += len(chunk)
    except httpx.HTTPError as exc:
        raise DownloadError(f"download failed for {url}: {exc}") from exc

    if expected_size is not None and total != expected_size:
        tmp.unlink(missing_ok=True)
        raise DownloadError(f"size mismatch for {url}: got {total}, expected {expected_size}")

    tmp.replace(dest)
    log.info("download.done", dest=str(dest), bytes=total)
    return dest


def head_size(url: str, *, auth: AuthLike | None = None, timeout: float = 30.0) -> int | None:
    """探测远端资源大小；失败返回 None。"""
    auth = auth or NoAuth()
    try:
        with httpx.Client(timeout=timeout, follow_redirects=True) as client:
            resp = client.head(url, headers=auth.auth_header())
            if resp.status_code >= 400:
                return None
            cl = resp.headers.get("content-length")
            return int(cl) if cl and cl.isdigit() else None
    except httpx.HTTPError:
        return None
