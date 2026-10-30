"""Copernicus Data Space Ecosystem (CDSE) OData / S3 客户端（补充路径）。

主路径是 CDSE STAC；本客户端用于：
- 需要完整 `.SAFE` 产品（含全部 granule / QI 数据）
- OData 独有元数据字段
- 走 S3 高速通道下载

认证：OAuth2 client_credentials（CDSE_CLIENT_ID / CDSE_CLIENT_SECRET）。
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

import httpx

from rspipe.auth import AuthLike, TokenProvider
from rspipe.config import ProviderConfig, Settings
from rspipe.errors import CredentialError, DownloadError, SearchError
from rspipe.ingest.downloader import download_file
from rspipe.ingest.fetcher import fetch_asset
from rspipe.logging import get_logger
from rspipe.models import (
    AssetRef,
    FetchMode,
    ProductLevel,
    Radiometry,
    SceneMeta,
    SourceFormat,
)

log = get_logger("rspipe.clients.cdse_odata")

DEFAULT_ODATA = "https://catalogue.dataspace.copernicus.eu/odata/v1"
DEFAULT_S3 = "https://eodata.dataspace.copernicus.eu"
DEFAULT_TOKEN_URL = (
    "https://identity.dataspace.copernicus.eu/auth/realms/CDSE"
    "/protocol/openid-connect/token"
)

# CDSE OData 产品类型 -> 内部画像
_PRODUCT_PROFILE: dict[str, tuple[ProductLevel, Radiometry, SourceFormat]] = {
    "S2MSI2A": (ProductLevel.L2, Radiometry.SURFACE_REFLECTANCE, SourceFormat.SAFE),
    "S2MSI1C": (ProductLevel.L1, Radiometry.TOA, SourceFormat.SAFE),
    "S2MSIL2A": (ProductLevel.L2, Radiometry.SURFACE_REFLECTANCE, SourceFormat.SAFE),
    "S2MSIL1C": (ProductLevel.L1, Radiometry.TOA, SourceFormat.SAFE),
}


class CdseODataClient:
    """CDSE OData 检索 + OData/S3 下载。"""

    def __init__(self, cfg: ProviderConfig, settings: Settings) -> None:
        self.name = cfg.name
        self.cfg = cfg
        self.settings = settings
        if not settings.cdse_client_id or not settings.cdse_client_secret:
            raise CredentialError(
                "CDSE OData requires CDSE_CLIENT_ID and CDSE_CLIENT_SECRET "
                "(see .env.example)"
            )
        self.base_url = (cfg.base_url or DEFAULT_ODATA).rstrip("/")
        self.s3_endpoint = (cfg.s3_endpoint or DEFAULT_S3).rstrip("/")
        self._auth: AuthLike = TokenProvider(
            token_url=cfg.token_url or DEFAULT_TOKEN_URL,
            client_id=settings.cdse_client_id,
            client_secret=settings.cdse_client_secret,
        )

    def close(self) -> None:
        return None

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
    ) -> list[SceneMeta]:
        if bbox is None and geometry is None:
            raise SearchError("cdse_odata: bbox or geometry required")

        filters: list[str] = []
        if start is not None:
            filters.append(f"ContentDate/Start ge {start.isoformat()}Z")
        if end is not None:
            filters.append(f"ContentDate/Start le {end.isoformat()}Z")
        if max_cloud_cover is not None:
            filters.append(f"Attributes/OData.CSC.DoubleAttribute/any(a: a/Name eq 'cloudCover' and a/Value le {max_cloud_cover})")

        # 产品类型过滤（来自 cfg.collections 里的 OData ProductType）
        product_types = [c for c in self.cfg.collections if c.upper().startswith("S2")]
        if product_types:
            ors = " or ".join(f"contains(Name,'{t}')" for t in product_types)
            filters.append(f"({ors})")

        if bbox is not None:
            minx, miny, maxx, maxy = bbox
            filters.append(
                "OData.CSC.Intersects(area=geography'SRID=4326;"
                f"POLYGON(({minx} {miny},{maxx} {miny},{maxx} {maxy},{minx} {maxy},{minx} {miny}))')"
            )
        elif geometry is not None and geometry.get("type") == "Polygon":
            coords = geometry["coordinates"][0]
            pts = ",".join(f"{c[0]} {c[1]}" for c in coords)
            filters.append(
                f"OData.CSC.Intersects(area=geography'SRID=4326;POLYGON(({pts}))')"
            )

        params: dict[str, Any] = {
            "$filter": " and ".join(filters) if filters else None,
            "$orderby": "ContentDate/Start desc",
            "$top": max_items or 50,
            "$expand": "Attributes",
        }
        params = {k: v for k, v in params.items() if v is not None}

        try:
            with httpx.Client(timeout=60.0, follow_redirects=True) as client:
                headers = self._auth.auth_header()
                resp = client.get(f"{self.base_url}/Products", params=params, headers=headers)
                resp.raise_for_status()
                payload = resp.json()
        except httpx.HTTPError as exc:
            raise SearchError(f"cdse_odata search failed: {exc}") from exc

        items = payload.get("value", [])
        scenes = [self._to_scene(it) for it in items]
        log.info("cdse.search", provider=self.name, matched=len(scenes))
        return scenes

    def _to_scene(self, raw: dict[str, Any]) -> SceneMeta:
        name = str(raw.get("Name", ""))
        product_type = str(raw.get("ProductType", "")).upper()
        geo = raw.get("Footprint") or {}
        coords = self._parse_geography(geo.get("geography") or geo.get("Geography"))
        bbox = _bbox_from_polygon(coords) if coords else (0.0, 0.0, 0.0, 0.0)

        content_date = raw.get("ContentDate") or {}
        dt = _parse_date(content_date.get("Start"))

        cloud = _attr(raw.get("Attributes"), "cloudCover")
        level, radiometry, source_format = _PRODUCT_PROFILE.get(
            product_type, (ProductLevel.L2, Radiometry.SURFACE_REFLECTANCE, SourceFormat.SAFE)
        )
        product_id = str(raw.get("Id", ""))
        return SceneMeta(
            scene_id=name or product_id,
            provider=self.name,
            collection=product_type or "cdse",
            datetime=dt,
            bbox=bbox,
            cloud_cover=cloud,
            platform=_attr(raw.get("Attributes"), "platformName") or "sentinel-2",
            sensor_key="sentinel-2-msi",
            product_level=level,
            radiometry=radiometry,
            source_format=source_format,
            properties=dict(raw),
            raw_href=f"{self.base_url}/Products({product_id})/$value",
        )

    @staticmethod
    def _parse_geography(text: Any) -> list[tuple[float, float]] | None:
        if not text or not isinstance(text, str):
            return None
        try:
            inner = text[text.index("POLYGON((") + 9 : text.rindex("))")]
        except (ValueError, IndexError):
            return None
        pts: list[tuple[float, float]] = []
        for pair in inner.split(","):
            bits = pair.strip().split()
            if len(bits) >= 2:
                pts.append((float(bits[0]), float(bits[1])))
        return pts or None

    # ── 资产 / 下载 ───────────────────────────────────────────────────────
    def resolve_assets(self, scene: SceneMeta) -> list[AssetRef]:
        if not scene.raw_href:
            return []
        return [
            AssetRef(
                key="product",
                href=scene.raw_href,
                media_type="application/zip",
                roles=("data",),
                fetch_mode=FetchMode.DOWNLOAD,
            )
        ]

    def fetch(
        self,
        asset: AssetRef,
        dest: Path,
        *,
        mode: FetchMode | None = None,
        bbox: tuple[float, float, float, float] | None = None,
    ) -> Path:
        if mode is FetchMode.VSI:
            return fetch_asset(asset, dest, auth=self._auth, mode=mode, bbox=bbox)
        if not asset.href:
            raise DownloadError("cdse_odata: asset has no href")
        return download_file(asset.href, dest, auth=self._auth)

    def s3_uri(self, scene: SceneMeta) -> str:
        """返回产品的 S3 路径（配合 `boto3` 走 S3 通道下载）。"""
        name = scene.scene_id
        return f"s3://eodata/Sentinel-2/MSI/L2A/{scene.datetime:%Y/%m/%d}/{name}.SAFE"


def _attr(attrs: Any, name: str) -> Any:
    if not isinstance(attrs, list):
        return None
    for a in attrs:
        if isinstance(a, dict) and a.get("Name") == name:
            return a.get("Value")
    return None


def _parse_date(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value
    if not value:
        return datetime.utcnow()
    text = str(value).replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(text)
        return dt.replace(tzinfo=None) if dt.tzinfo is None else dt
    except ValueError:
        return datetime.utcnow()


def _bbox_from_polygon(pts: list[tuple[float, float]]) -> tuple[float, float, float, float]:
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    return (min(xs), min(ys), max(xs), max(ys))


__all__ = ["DEFAULT_ODATA", "DEFAULT_S3", "DEFAULT_TOKEN_URL", "CdseODataClient"]
