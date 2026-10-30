"""STAC 客户端：一套代码对接 LandsatLook / CDSE 等 STAC API。"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pystac
from pystac_client import Client as StacApiClient

from rspipe.auth import AuthLike, NoAuth, StaticTokenAuth, TokenProvider
from rspipe.config import ProviderConfig, Settings
from rspipe.errors import CredentialError, SearchError
from rspipe.logging import get_logger
from rspipe.models import (
    AssetRef,
    FetchMode,
    ProductLevel,
    ProviderType,
    Radiometry,
    SceneMeta,
    SourceFormat,
)

log = get_logger("rspipe.clients.stac")

# 产品级别 / 辐射量的粗粒度推断（按 collection 关键字）
_L2_SR_HINTS = ("l2", "l2a", "sr", "surface", "s2msi2a")
_L1_HINTS = ("l1", "l1c", "toa")
_ST_HINTS = ("-st", "surface_temperature", "st_")
_SAFE_SUFFIXES = (".safe", ".zip", ".sentinel2")
_TAR_SUFFIXES = (".tar", ".tgz", ".tar.gz")
_COG_MEDIA_HINTS = ("cloud-optimized", "cog", "profile=cloud")
_TIFF_MEDIA_HINTS = ("tiff", "tif", "geotiff")


def _build_auth(cfg: ProviderConfig, settings: Settings) -> AuthLike:
    if cfg.auth.value == "none":
        return NoAuth()
    if cfg.auth.value == "m2m_token":
        return StaticTokenAuth(settings.usgs_m2m_token)
    if cfg.auth.value == "oauth2":
        if cfg.auth.value == "oauth2" and not cfg.token_url:
            raise CredentialError(f"provider {cfg.name!r}: oauth2 requires token_url")
        return TokenProvider(
            token_url=cfg.token_url or "",
            client_id=settings.cdse_client_id,
            client_secret=settings.cdse_client_secret,
        )
    if cfg.auth.value == "basic":
        return NoAuth()
    raise CredentialError(f"provider {cfg.name!r}: unsupported auth {cfg.auth.value!r}")


def _guess_source_format(asset_hrefs: list[str], media_types: list[str]) -> SourceFormat:
    joined = " ".join(h.lower() for h in asset_hrefs)
    if any(s in joined for s in _SAFE_SUFFIXES):
        return SourceFormat.SAFE
    if any(s in joined for s in _TAR_SUFFIXES):
        return SourceFormat.TAR
    media = " ".join(m.lower() for m in media_types if m)
    if any(h in media for h in _COG_MEDIA_HINTS):
        return SourceFormat.COG
    if any(h in media for h in _TIFF_MEDIA_HINTS):
        return SourceFormat.GEOTIFF
    return SourceFormat.COG


def _guess_radiometry(collection: str, props: dict[str, Any]) -> Radiometry:
    coll = collection.lower()
    if any(h in coll for h in _ST_HINTS):
        return Radiometry.OTHER
    if any(h in coll for h in _L2_SR_HINTS):
        return Radiometry.SURFACE_REFLECTANCE
    if any(h in coll for h in _L1_HINTS):
        # L1C 通常为 TOA
        return Radiometry.TOA
    if "landsat" in coll and "l2" in coll:
        return Radiometry.SURFACE_REFLECTANCE
    if props.get("landsat:product_id", "").endswith("L1"):
        return Radiometry.TOA
    return Radiometry.SURFACE_REFLECTANCE


def _guess_product_level(collection: str, props: dict[str, Any]) -> ProductLevel:
    coll = collection.lower()
    if any(h in coll for h in _L1_HINTS):
        return ProductLevel.L1
    if any(h in coll for h in _ST_HINTS) or "level-3" in coll or "_l3" in coll:
        return ProductLevel.L3
    if "l2" in coll or "l2a" in coll:
        return ProductLevel.L2
    return ProductLevel.L2


def _guess_sensor_key(platform: str | None, collection: str) -> str | None:
    text = f"{platform or ''} {collection}".lower()
    if "landsat-9" in text or "landsat_9" in text or "lt09" in text:
        return "landsat-8-oli"  # OLI/TIRS 同规格
    if "landsat-8" in text or "landsat_8" in text or "lc08" in text or "landsat_ot" in text:
        return "landsat-8-oli"
    if "landsat-7" in text or "landsat_7" in text or "le07" in text or "etm" in text:
        return "landsat-7-etm"
    if "sentinel-2" in text or "sentinel_2" in text or "s2msi" in text or "s2a" in text:
        return "sentinel-2-msi"
    return None


class StacClient:
    """pystac-client 的薄封装，输出内部 `SceneMeta` / `AssetRef`。"""

    def __init__(self, cfg: ProviderConfig, settings: Settings, *, timeout: float = 60.0) -> None:
        self.name = cfg.name
        self.cfg = cfg
        self.settings = settings
        self.timeout = timeout
        self._auth: AuthLike = _build_auth(cfg, settings)
        self._client: StacApiClient | None = None

    # ── 连接 ──────────────────────────────────────────────────────────────
    @property
    def client(self) -> StacApiClient:
        if self._client is None:
            if not self.cfg.url:
                raise SearchError(f"provider {self.name!r}: missing stac url")
            headers = self._auth.auth_header()
            try:
                self._client = StacApiClient.open(self.cfg.url, headers=headers or None)
            except Exception as exc:
                raise SearchError(f"cannot open STAC API {self.cfg.url}: {exc}") from exc
        return self._client

    def close(self) -> None:
        self._client = None

    # ── 检索 ──────────────────────────────────────────────────────────────
    def search(
        self,
        *,
        bbox: tuple[float, float, float, float] | None = None,
        geometry: dict[str, Any] | None = None,
        start: datetime | None = None,
        end: datetime | None = None,
        max_cloud_cover: float | None = None,
        max_items: int | None = None,
        ids: list[str] | None = None,
    ) -> list[SceneMeta]:
        if bbox is None and geometry is None and not ids:
            raise SearchError("bbox / geometry / ids 至少提供一个")

        dt = self._format_datetime(start, end)
        query: dict[str, Any] = {}
        if max_cloud_cover is not None:
            query["eo:cloud_cover"] = {"lte": max_cloud_cover}

        kwargs: dict[str, Any] = {}
        if self.cfg.collections:
            kwargs["collections"] = self.cfg.collections
        if ids:
            kwargs["ids"] = list(ids)
        elif bbox is not None:
            kwargs["bbox"] = list(bbox)
        elif geometry is not None:
            kwargs["intersects"] = geometry
        if dt:
            kwargs["datetime"] = dt
        if query:
            kwargs["query"] = query
        if max_items is not None:
            kwargs["max_items"] = max_items

        try:
            search = self.client.search(**kwargs)
            items = list(search.items())
        except Exception as exc:
            raise SearchError(f"STAC search failed on {self.name}: {exc}") from exc

        scenes = [self.item_to_scene(it) for it in items]
        log.info("stac.search", provider=self.name, matched=len(scenes))
        return scenes

    @staticmethod
    def _format_datetime(start: datetime | None, end: datetime | None) -> str | None:
        if start is None and end is None:
            return None
        s = (start or datetime(1970, 1, 1, tzinfo=UTC)).strftime("%Y-%m-%dT%H:%M:%SZ")
        e = (end or datetime.now(UTC)).strftime("%Y-%m-%dT%H:%M:%SZ")
        return f"{s}/{e}"

    # ── Item -> SceneMeta ─────────────────────────────────────────────────
    def item_to_scene(self, item: pystac.Item) -> SceneMeta:
        props: dict[str, Any] = dict(item.properties or {})
        collection = (
            item.collection_id or self.cfg.collections[0] if self.cfg.collections else "unknown"
        )
        platform = props.get("platform") or props.get("instruments")
        if isinstance(platform, list):
            platform = platform[0] if platform else None

        hrefs = [a.href for a in item.assets.values()]
        media = [a.media_type or "" for a in item.assets.values()]
        source_format = _guess_source_format(hrefs, media)

        assets = [
            AssetRef(
                key=key,
                href=asset.href,
                media_type=asset.media_type,
                roles=tuple(asset.roles or ()),
                fetch_mode=self.cfg.fetch_mode,
                extra=dict(asset.extra_fields or {}),
            )
            for key, asset in item.assets.items()
        ]

        bbox = tuple(item.bbox) if item.bbox else (0.0, 0.0, 0.0, 0.0)
        if len(bbox) != 4:
            bbox = (0.0, 0.0, 0.0, 0.0)

        dt = item.datetime or datetime.now(UTC)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)

        from rspipe.quality import parse_tier

        return SceneMeta(
            scene_id=item.id,
            provider=self.name,
            collection=collection,
            datetime=dt,
            bbox=bbox,
            geometry=item.geometry,
            cloud_cover=props.get("eo:cloud_cover"),
            platform=platform,
            sensor_key=_guess_sensor_key(platform, f"{collection} {item.id}"),
            tier=parse_tier(item.id, props),
            product_level=_guess_product_level(collection, props),
            radiometry=_guess_radiometry(collection, props),
            source_format=source_format,
            assets=assets,
            properties=props,
            raw_href=self._find_raw_href(item, assets),
        )

    @staticmethod
    def _find_raw_href(item: pystac.Item, assets: list[AssetRef]) -> str | None:
        for a in assets:
            low = a.href.lower()
            if low.endswith(_SAFE_SUFFIXES) or low.endswith(_TAR_SUFFIXES):
                return a.href
        # 退而求其次：roles 含 data 但非 tiff
        for a in assets:
            if "data" in a.roles and not a.is_raster:
                return a.href
        return None

    # ── 资产 ──────────────────────────────────────────────────────────────
    def resolve_assets(self, scene: SceneMeta) -> list[AssetRef]:
        return list(scene.assets)

    def fetch(
        self,
        asset: AssetRef,
        dest: Path,
        *,
        mode: FetchMode | None = None,
        bbox: tuple[float, float, float, float] | None = None,
    ) -> str | Path:
        from rspipe.ingest.fetcher import fetch_asset

        return fetch_asset(
            asset,
            dest,
            auth=self._auth,
            mode=mode or asset.fetch_mode,
            bbox=bbox,
            timeout=self.timeout,
        )


def build_stac_client(cfg: ProviderConfig, settings: Settings) -> StacClient:
    if cfg.type is not ProviderType.STAC:
        raise SearchError(f"provider {cfg.name!r} is not a stac provider")
    return StacClient(cfg, settings)


__all__ = [
    "AuthLike",
    "NoAuth",
    "StacClient",
    "StaticTokenAuth",
    "TokenProvider",
    "build_stac_client",
    "httpx",
]
