"""本地 HTTP Range 代理：绕开 Windows 上 GDAL vsicurl 的 TLS 故障。

问题
----
GDAL `/vsicurl/` 在 Windows 上用 schannel，常因证书吊销检查失败
（`CERT_TRUST_REVOCATION_STATUS_UNKNOWN`），而 Python 的 httpx 走系统证书链正常。

方案
----
起一个本地 HTTP 服务，把 GET/HEAD（含 Range）转发到远端 httpx，
GDAL 改读 `http://127.0.0.1:<port>/<url-encoded-remote>`（明文，无 TLS）。
GDAL 的 COG 窗口化 Range 读取得以保留，只拉取需要的瓦片。

用法
----
    proxy = RangeProxy.start()
    try:
        vsi = proxy.vsi_path("https://example.com/a.tif")
        with rasterio.open(vsi) as src:
            ...
    finally:
        proxy.stop()
"""

from __future__ import annotations

import base64
import contextlib
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, ClassVar
from urllib.parse import unquote, urlparse

import httpx

from rspipe.logging import get_logger

log = get_logger("rspipe.ingest.httpproxy")

_TIMEOUT = 120.0
_CHUNK = 1 << 18  # 256 KiB

# 透传给客户端的响应头
_PASSTHROUGH = {
    "content-type",
    "content-length",
    "content-range",
    "accept-ranges",
    "etag",
    "last-modified",
    "cache-control",
}


class _ProxyHandler(BaseHTTPRequestHandler):
    server_version = "rspipeRangeProxy/1.0"
    protocol_version = "HTTP/1.1"

    # 由 RangeProxy 注入
    allowed_hosts: ClassVar[set[str]] = set()
    # 共享 httpx 连接池：避免每次请求重新 TLS 握手（慢网络下是主要瓶颈）
    shared_client: ClassVar[httpx.Client | None] = None

    def log_message(self, fmt: str, *args: Any) -> None:
        return

    # ── GET / HEAD ────────────────────────────────────────────────────────
    def do_HEAD(self) -> None:
        self._handle(body=False)

    def do_GET(self) -> None:
        self._handle(body=True)

    def _handle(self, *, body: bool) -> None:
        remote = self._decode_target()
        if remote is None:
            self._safe_error(400, "bad target")
            return

        headers: dict[str, str] = {}
        rng = self.headers.get("Range")
        if rng:
            headers["Range"] = rng
        ua = self.headers.get("User-Agent")
        if ua:
            headers["User-Agent"] = ua

        client = self.shared_client or httpx.Client(timeout=_TIMEOUT, follow_redirects=True)
        try:
            with client.stream("GET", remote, headers=headers) as resp:
                if resp.status_code >= 400:
                    self._safe_error(resp.status_code, resp.reason_phrase)
                    return
                self.send_response(resp.status_code)
                for k, v in resp.headers.items():
                    if k.lower() in _PASSTHROUGH:
                        self.send_header(k, v)
                # 告知客户端支持 Range
                if "accept-ranges" not in {k.lower() for k in resp.headers}:
                    self.send_header("Accept-Ranges", "bytes")
                self.send_header("Connection", "close")
                self.end_headers()
                if body:
                    for chunk in resp.iter_bytes(_CHUNK):
                        try:
                            self.wfile.write(chunk)
                        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                            return
        except httpx.HTTPError as exc:
            log.warning("proxy.upstream-error", remote=remote, error=str(exc))
            self._safe_error(502, str(exc)[:120])
        finally:
            if self.shared_client is None:
                client.close()

    def _safe_error(self, code: int, message: str) -> None:
        """发错误响应；客户端已断开时静默忽略（GDAL 常主动取消请求）。"""
        with contextlib.suppress(OSError):
            self.send_error(code, message)

    def _decode_target(self) -> str | None:
        raw = self.path.lstrip("/")
        if not raw:
            return None
        # 形如 <base64url(remote)>.tif，便于通过 GDAL 的扩展名白名单
        token = raw[:-4] if raw.lower().endswith(".tif") else raw
        try:
            target = base64.urlsafe_b64decode(token.encode("ascii")).decode("utf-8")
        except (ValueError, UnicodeError):
            target = unquote(raw)
        parsed = urlparse(target)
        if parsed.scheme not in ("http", "https"):
            return None
        if self.allowed_hosts and parsed.netloc not in self.allowed_hosts:
            return None
        return target


class RangeProxy:
    """本地 HTTP Range 正向代理。"""

    def __init__(self, host: str = "127.0.0.1", port: int = 0) -> None:
        # 共享连接池：复用 TLS 会话，显著降低 Range 请求延迟
        self._client = httpx.Client(
            timeout=httpx.Timeout(_TIMEOUT, connect=30.0),
            follow_redirects=True,
            limits=httpx.Limits(max_connections=16, max_keepalive_connections=8),
        )
        handler = type(
            "_BoundHandler",
            (_ProxyHandler,),
            {"allowed_hosts": set(), "shared_client": self._client},
        )
        self._handler_cls: Any = handler
        self._server = ThreadingHTTPServer((host, port), handler)
        self._server.daemon_threads = True
        self.host = str(self._server.server_address[0])
        self.port = int(self._server.server_address[1])
        self._thread: threading.Thread | None = None

    # ── 生命周期 ──────────────────────────────────────────────────────────
    @classmethod
    def start(cls, **kwargs: Any) -> RangeProxy:
        proxy = cls(**kwargs)
        proxy._thread = threading.Thread(target=proxy._server.serve_forever, daemon=True)
        proxy._thread.start()
        log.info("proxy.start", host=proxy.host, port=proxy.port)
        return proxy

    def stop(self) -> None:
        try:
            self._server.shutdown()
            self._server.server_close()
        except Exception:
            pass
        self._client.close()
        log.info("proxy.stop")

    def __enter__(self) -> RangeProxy:
        return self

    def __exit__(self, *exc: Any) -> None:
        self.stop()

    # ── 映射 ──────────────────────────────────────────────────────────────
    def allow(self, *hosts: str) -> None:
        self._handler_cls.allowed_hosts.update(hosts)

    def url_for(self, remote: str) -> str:
        """远端 URL -> 本地代理 URL。

        用 base64 承载远端地址并以 `.tif` 结尾，确保 GDAL `/vsicurl/`
        的扩展名白名单（`CPL_VSIL_CURL_ALLOWED_EXTENSIONS`）放行，
        从而启用按需 Range 读取而不是整文件拉取。
        """
        token = base64.urlsafe_b64encode(remote.encode("utf-8")).decode("ascii")
        return f"http://{self.host}:{self.port}/{token}.tif"

    def vsi_path(self, remote: str) -> str:
        """远端 URL -> GDAL VSI 路径（经本地代理，明文 HTTP）。"""
        return f"/vsicurl/{self.url_for(remote)}"

    def is_proxied(self, vsi_path: str) -> bool:
        return vsi_path.startswith(f"/vsicurl/http://{self.host}:{self.port}/")


# 模块级单例（惰性启动）
_PROXY: RangeProxy | None = None
_LOCK = threading.Lock()


def get_proxy() -> RangeProxy:
    """获取（必要时启动）进程级代理单例。"""
    global _PROXY
    with _LOCK:
        if _PROXY is None:
            _PROXY = RangeProxy.start()
        return _PROXY


def shutdown_proxy() -> None:
    global _PROXY
    with _LOCK:
        if _PROXY is not None:
            _PROXY.stop()
            _PROXY = None


__all__ = ["RangeProxy", "get_proxy", "shutdown_proxy"]
