"""资产链接签名：Microsoft Planetary Computer 等需要 SAS/预签名的端点。

Planetary Computer 的 Landsat/Sentinel 资产是 Azure Blob URL，
需附带 SAS token 才能访问；签名接口免费且无需账号：

    GET https://planetarycomputer.microsoft.com/api/sas/v1/sign?href=<url>

结果带过期时间，这里做进程级缓存避免重复签名。
"""

from __future__ import annotations

import threading
import time
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

_CACHE: dict[str, str] = {}
_LOCK = threading.Lock()
# 提前多久重新签名（秒）
_SKEW = 120


def needs_signing(href: str) -> bool:
    """判断 href 是否需要签名。"""
    if not href.startswith("https://"):
        return False
    if "?" in href and ("sig=" in href or "se=" in href):
        return False  # 已签名
    host = urlparse(href).netloc
    return any(h in host for h in _PC_HOSTS)


def sign_href(href: str, *, timeout: float = 30.0, max_attempts: int = 5) -> str:
    """返回可用的 href（需要时附 SAS token）。

    无需签名的链接原样返回；已缓存且未过期的签名直接复用。
    签名接口偶发 5xx/超时，做指数退避重试。
    """
    if not needs_signing(href):
        return href

    with _LOCK:
        cached = _CACHE.get(href)
    if cached:
        return cached

    last_exc: Exception | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            resp = httpx.get(PC_SIGN_URL, params={"href": href}, timeout=timeout)
            if resp.status_code >= 500 or resp.status_code == 429:
                raise DownloadError(f"sign HTTP {resp.status_code}")
            resp.raise_for_status()
            payload = resp.json()
            signed = payload.get("href")
            if not signed:
                raise DownloadError("sign response missing href")
            with _LOCK:
                _CACHE[href] = signed
            if attempt > 1:
                log.info("sign.recovered", attempt=attempt)
            log.debug("sign.ok", host=urlparse(href).netloc, expiry=payload.get("msft:expiry"))
            return signed
        except (httpx.HTTPError, ValueError, DownloadError) as exc:
            last_exc = exc
            if attempt < max_attempts:
                wait = min(8.0, 0.5 * (2 ** (attempt - 1)))
                log.warning("sign.retry", attempt=attempt, wait=wait, error=str(exc)[:80])
                time.sleep(wait)

    raise DownloadError(f"SAS signing failed for {href} after {max_attempts} attempts: {last_exc}")


def clear_cache() -> None:
    with _LOCK:
        _CACHE.clear()


__all__ = ["PC_SIGN_URL", "clear_cache", "needs_signing", "sign_href"]
