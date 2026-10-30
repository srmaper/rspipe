"""资产链接签名：Microsoft Planetary Computer 等需要 SAS/预签名的端点。

Planetary Computer 的 Landsat/Sentinel 资产是 Azure Blob URL，
需附带 SAS token 才能访问；签名接口免费且无需账号：

    GET https://planetarycomputer.microsoft.com/api/sas/v1/sign?href=<url>

注意两点（实测都会踩）：
- token 约 1 小时过期，缓存必须带过期时间，否则重启前一直用旧链接会 403；
- 签名接口有速率限制（并发高会 429），必须限并发并尊重 Retry-After。
"""

from __future__ import annotations

import os
import threading
import time
from datetime import datetime
from urllib.parse import urlparse

import httpx

from rspipe.errors import DownloadError
from rspipe.logging import get_logger

log = get_logger("rspipe.ingest.signing")

PC_SIGN_URL = "https://planetarycomputer.microsoft.com/api/sas/v1/sign"
# 需要签名的主机（Azure Blob，Planetary Computer 托管）
_PC_HOSTS = (
    "blob.core.windows.net",
    "planetarycomputer.microsoft.com",
)

# href -> (signed_href, expires_at_epoch)
_CACHE: dict[str, tuple[str, float]] = {}
_HREF_LOCKS: dict[str, threading.Lock] = {}
_LOCK = threading.Lock()
# 提前多久重新签名（秒）
_SKEW = 120
# 签名接口并发上限：太高会 429，反而更慢
_SIGN_CONCURRENCY = max(1, int(os.environ.get("RSPIPE_SIGN_CONCURRENCY", "4")))
_SIGN_SEM = threading.BoundedSemaphore(_SIGN_CONCURRENCY)


def needs_signing(href: str) -> bool:
    """判断 href 是否需要签名。"""
    if not href.startswith("https://"):
        return False
    if "?" in href and ("sig=" in href or "se=" in href):
        return False  # 已签名
    host = urlparse(href).netloc
    return any(h in host for h in _PC_HOSTS)


def _expiry_of(payload: dict, resp: httpx.Response) -> float:
    """解析签名有效期；解析失败就保守按 30 分钟算。"""
    raw = payload.get("msft:expiry")
    if raw:
        try:
            return datetime.fromisoformat(str(raw).replace("Z", "+00:00")).timestamp()
        except ValueError:
            pass
    return time.time() + 1800.0


def _retry_wait(resp: httpx.Response, attempt: int) -> float:
    """429 优先按服务端 Retry-After，其次指数退避。"""
    if resp.status_code == 429:
        header = resp.headers.get("retry-after", "")
        if header.isdigit():
            return min(30.0, float(header))
    return min(8.0, 0.5 * (2 ** (attempt - 1)))


def _href_lock(href: str) -> threading.Lock:
    with _LOCK:
        lock = _HREF_LOCKS.get(href)
        if lock is None:
            lock = threading.Lock()
            _HREF_LOCKS[href] = lock
        return lock


def sign_href(href: str, *, timeout: float = 30.0, max_attempts: int = 5) -> str:
    """返回可用的 href（需要时加 SAS token）。

    无需签名的链接原样返回；已缓存且未过期的签名直接复用；
    同一 href 的并发签名会合并成一次请求。
    """
    if not needs_signing(href):
        return href

    with _LOCK:
        hit = _CACHE.get(href)
    if hit and hit[1] - _SKEW > time.time():
        return hit[0]

    lock = _href_lock(href)
    with lock:
        # 拿到锁后再查一次：可能别的线程刚签好
        with _LOCK:
            hit = _CACHE.get(href)
        if hit and hit[1] - _SKEW > time.time():
            return hit[0]

        last_exc: Exception | None = None
        for attempt in range(1, max_attempts + 1):
            resp: httpx.Response | None = None
            try:
                with _SIGN_SEM:
                    resp = httpx.get(PC_SIGN_URL, params={"href": href}, timeout=timeout)
                if resp.status_code >= 500 or resp.status_code == 429:
                    raise DownloadError(f"sign HTTP {resp.status_code}")
                resp.raise_for_status()
                payload = resp.json()
                signed = payload.get("href")
                if not signed:
                    raise DownloadError("sign response missing href")
                expires = _expiry_of(payload, resp)
                with _LOCK:
                    _CACHE[href] = (signed, expires)
                if attempt > 1:
                    log.info("sign.recovered", attempt=attempt)
                log.debug("sign.ok", host=urlparse(href).netloc, expiry=payload.get("msft:expiry"))
                return signed
            except (httpx.HTTPError, ValueError, DownloadError) as exc:
                last_exc = exc
                if attempt < max_attempts:
                    wait = _retry_wait(resp, attempt) if resp is not None else 0.5
                    log.warning("sign.retry", attempt=attempt, wait=wait, error=str(exc)[:80])
                    time.sleep(wait)

        raise DownloadError(
            f"SAS signing failed for {href} after {max_attempts} attempts: {last_exc}"
        )


def clear_cache() -> None:
    with _LOCK:
        _CACHE.clear()


__all__ = ["PC_SIGN_URL", "clear_cache", "needs_signing", "sign_href"]
