"""资产获取分发：vsi / download / windowed 三种模式。"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import rasterio
from rasterio.windows import from_bounds

from rspipe.auth import AuthLike, NoAuth
from rspipe.errors import DownloadError
from rspipe.ingest.downloader import download_file
from rspipe.logging import get_logger
from rspipe.models import AssetRef, FetchMode

log = get_logger("rspipe.ingest.fetch")

_TIFF_SUFFIXES = (".tif", ".tiff", ".cog")

# VSI 可用性探测缓存：同一 href 只探测一次（GDAL 打开远程 COG 要读 IFD，代价高）
_VSI_PROBE_CACHE: dict[str, bool] = {}

# GDAL 读远程 COG 的推荐参数：小分块 + 有限重试，配合本地代理做窗口化 Range 读取
GDAL_REMOTE_ENV: dict[str, Any] = {
    "CPL_VSIL_CURL_CHUNK_SIZE": "65536",
    "GDAL_HTTP_MAX_RETRY": "2",
    "GDAL_HTTP_TIMEOUT": "30",
    "GDAL_HTTP_CONNECTTIMEOUT": "15",
    "GDAL_HTTP_MULTIRANGE": "YES",
    "CPL_VSIL_CURL_ALLOWED_EXTENSIONS": ".tif,.tiff,.TIF,.json,.xml,.txt",
}


def to_vsi(href: str, *, via_proxy: bool = True) -> str:
    """把 http(s)/s3 href 转成 rasterio 可打开的 VSI 路径。

    `via_proxy=True`（默认）时经本地 Range 代理转发，
    规避 Windows 上 GDAL vsicurl 的 schannel TLS 故障，
    同时保留 COG 窗口化 Range 读取能力。
    """
    if href.startswith("/vsi"):
        return href
    parsed = urlparse(href)
    if parsed.scheme in ("http", "https"):
        if via_proxy:
            try:
                from rspipe.ingest.httpproxy import get_proxy

                return get_proxy().vsi_path(href)
            except Exception:
                log.warning("proxy.unavailable.fallback", href=href)
        return f"/vsicurl/{href}"
    if parsed.scheme == "s3":
        return f"/vsis3/{parsed.netloc}{parsed.path}"
    return href


def is_remote(href: str) -> bool:
    return urlparse(href).scheme in ("http", "https", "s3")


def fetch_asset(
    asset: AssetRef,
    dest: Path | str,
    *,
    auth: AuthLike | None = None,
    mode: FetchMode | None = None,
    bbox: tuple[float, float, float, float] | None = None,
    timeout: float = 120.0,
) -> str:
    """按 `mode` 获取资产，返回可被 rasterio 打开的路径字符串。

    注意：VSI 路径（`/vsicurl/...`）必须以 `str` 传递，
    不能被 `pathlib.Path` 包裹——Windows 上 Path 会拆坏 URL。

    - `vsi`      : 返回 `/vsicurl/...`（不落盘）
    - `download` : 完整下载到 `dest`
    - `windowed` : 按 bbox 窗口裁剪后落盘（COG 走优化读）
    """
    mode = mode or asset.fetch_mode
    auth = auth or NoAuth()

    # 需要 SAS/预签名的端点（如 Planetary Computer）先签名
    href = _signed(asset.href)

    if mode is FetchMode.VSI:
        # 远程 http(s)：原样返回 URL，交由 CogReader 做按需瓦片读取
        # （Windows 上 GDAL vsicurl 的 TLS 不可用，见 ingest/rangefile.py）
        return href

    dest_path = Path(dest)
    if mode is FetchMode.DOWNLOAD:
        if not is_remote(href):
            return href
        return str(download_file(href, dest_path, auth=auth, timeout=timeout))

    if mode is FetchMode.WINDOWED:
        return _fetch_windowed(
            AssetRef(
                key=asset.key,
                href=href,
                media_type=asset.media_type,
                roles=asset.roles,
                fetch_mode=asset.fetch_mode,
                extra=asset.extra,
            ),
            dest_path,
            bbox=bbox,
            auth=auth,
            timeout=timeout,
        )

    raise DownloadError(f"unsupported fetch mode: {mode}")


def _signed(href: str) -> str:
    """必要时对 href 签名；失败则原样返回（公开端点本就不需要）。"""
    try:
        from rspipe.ingest.signing import needs_signing, sign_href

        return sign_href(href) if needs_signing(href) else href
    except Exception:
        return href


def _vsi_openable(
    vsi_path: str,
    *,
    auth: AuthLike | None = None,
    cache_key: str | None = None,
) -> bool:
    """探测远程 COG 能否经 VSI 打开（结果按 `cache_key` 缓存）。

    Windows 上 GDAL 的 schannel 常因证书吊销检查失败
    （CERT_TRUST_REVOCATION_STATUS_UNKNOWN），此时返回 False
    让调用方回落到 Python HTTP 下载（httpx 走系统证书链，正常）。
    经本地 Range 代理转发时（`to_vsi` 默认）通常可正常打开。
    """
    if cache_key and cache_key in _VSI_PROBE_CACHE:
        return _VSI_PROBE_CACHE[cache_key]

    opts: dict[str, Any] = dict(GDAL_REMOTE_ENV)
    if auth is not None:
        headers = dict(auth.auth_header())
        if headers:
            opts["GDAL_HTTP_HEADERS"] = "\r\n".join(f"{k}: {v}" for k, v in headers.items())
    ok = False
    try:
        with rasterio.Env(**opts), rasterio.open(vsi_path) as src:
            _ = (src.width, src.height, src.count)
        ok = True
    except Exception:
        ok = False
    if cache_key:
        _VSI_PROBE_CACHE[cache_key] = ok
    return ok


def _fetch_windowed(
    asset: AssetRef,
    dest: Path,
    *,
    bbox: tuple[float, float, float, float] | None,
    auth: AuthLike,
    timeout: float,
) -> str:
    """按 bbox 窗口裁剪。无 bbox 时退化为完整下载。"""
    if bbox is None:
        log.warning("windowed.no-bbox", asset=asset.key, fallback="download")
        return fetch_asset(asset, dest, auth=auth, mode=FetchMode.DOWNLOAD, timeout=timeout)

    src_path = to_vsi(asset.href) if is_remote(asset.href) else asset.href
    dest.parent.mkdir(parents=True, exist_ok=True)

    vsicurl_opts: dict[str, Any] = dict(GDAL_REMOTE_ENV)
    if is_remote(asset.href):
        headers = dict(auth.auth_header())
        if headers:
            vsicurl_opts["GDAL_HTTP_HEADERS"] = "\r\n".join(f"{k}: {v}" for k, v in headers.items())

    try:
        with rasterio.Env(**vsicurl_opts), rasterio.open(src_path) as src:
            # bbox 为 EPSG:4326，源可能是投影坐标；用 bounds 交集判断
            window = _window_for_bbox(src, bbox)
            if window is None:
                log.info("windowed.no-overlap", asset=asset.key, bbox=bbox)
                return _write_empty_like(src, dest)

            data = src.read(window=window)
            transform = src.window_transform(window)
            profile = src.profile.copy()
            profile.update(
                height=int(window.height),
                width=int(window.width),
                transform=transform,
                driver="GTiff",
                compress="deflate",
            )
            with rasterio.open(dest, "w", **profile) as dst:
                dst.write(data)
    except rasterio.errors.RasterioError as exc:
        raise DownloadError(f"windowed read failed for {asset.href}: {exc}") from exc

    log.info("windowed.done", asset=asset.key, dest=str(dest))
    return str(dest)


def _window_for_bbox(src: Any, bbox: tuple[float, float, float, float]) -> Any | None:
    """在源 CRS 下计算 bbox 对应的窗口；无交集返回 None。"""
    minx, miny, maxx, maxy = bbox
    # 假设 bbox 是 EPSG:4326，源 CRS 可能不同；仅当源是 EPSG:4326 或投影坐标时直接比 bounds
    src_bounds = src.bounds
    if not (
        minx < src_bounds.right
        and maxx > src_bounds.left
        and miny < src_bounds.top
        and maxy > src_bounds.bottom
    ):
        return None
    try:
        window = from_bounds(minx, miny, maxx, maxy, transform=src.transform)
    except Exception:
        return None
    window = window.round_offsets().round_lengths()
    if window.width <= 0 or window.height <= 0:
        return None
    return window


def _write_empty_like(src: Any, dest: Path) -> str:
    profile = src.profile.copy()
    profile.update(height=1, width=1, driver="GTiff", compress="deflate")
    import numpy as np

    nodata = src.nodata if src.nodata is not None else 0
    with rasterio.open(dest, "w", **profile) as dst:
        dst.write(np.full((src.count, 1, 1), nodata, dtype=src.dtypes[0]))
    return str(dest)


def open_raster(path: str | Path, *, auth: AuthLike | None = None) -> Any:
    """统一打开本地 / 远程栅格的上下文管理器入口。"""
    p = str(path)
    env_opts: dict[str, Any] = dict(GDAL_REMOTE_ENV)
    if p.startswith("/vsi") and auth is not None:
        headers = dict(auth.auth_header())
        if headers:
            env_opts["GDAL_HTTP_HEADERS"] = "\r\n".join(f"{k}: {v}" for k, v in headers.items())
    return rasterio.Env(**env_opts)


__all__ = [
    "fetch_asset",
    "is_remote",
    "open_raster",
    "to_vsi",
]
