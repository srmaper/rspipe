"""USGS M2M 客户端（补充路径）。

主路径是 LandsatLook STAC；本客户端用于：
- 需要原始 `.tar` 全量包
- STAC 未覆盖的历史集合 / ARD / Level-3 瓦片产品
- 需要 M2M 独有元数据

依赖可选包 `usgsxplore`（`pip install rspipe[m2m]`）。
"""

from __future__ import annotations

import contextlib
from datetime import datetime
from pathlib import Path
from typing import Any

from rspipe.auth import StaticTokenAuth
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

log = get_logger("rspipe.clients.usgs_m2m")

_M2M_API = "https://m2m.cr.usgs.gov/api/api/json/stable/"


def _require_xplore() -> Any:
    try:
        from usgsxplore import USGS  # type: ignore[import-not-found]
    except ImportError as exc:
        raise CredentialError(
            "usgsxplore is required for the USGS M2M provider; "
            "install with `pip install rspipe[m2m]`"
        ) from exc
    return USGS


class UsgsM2MClient:
    """USGS EarthExplorer M2M API 客户端。"""

    def __init__(self, cfg: ProviderConfig, settings: Settings) -> None:
        self.name = cfg.name
        self.cfg = cfg
        self.settings = settings
        if not settings.usgs_m2m_username or not settings.usgs_m2m_token:
            raise CredentialError(
                "USGS M2M requires USGS_M2M_USERNAME and USGS_M2M_TOKEN "
                "(see .env.example)"
            )
        self._auth = StaticTokenAuth(settings.usgs_m2m_token)
        self._api: Any | None = None

    # ── 连接 ──────────────────────────────────────────────────────────────
    @property
    def api(self) -> Any:
        if self._api is None:
            usgs_cls = _require_xplore()
            self._api = usgs_cls(self.settings.usgs_m2m_username, self.settings.usgs_m2m_token)
        return self._api

    def close(self) -> None:
        if self._api is not None:
            with contextlib.suppress(Exception):
                self._api.logout()
            self._api = None

    # ── 检索 ┲────────────────────────────────────────────────────────────
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
            raise SearchError("usgs_m2m: bbox or geometry required")

        datasets = self.cfg.datasets or ["landsat_ot_c2_l2"]
        scenes: list[SceneMeta] = []
        limit = max_items or 50

        try:
            for dataset in datasets:
                params: dict[str, Any] = {
                    "datasetName": dataset,
                    "maxResults": limit,
                    "startingNumber": 1,
                    "sortOrder": "ASC",
                }
                if bbox is not None:
                    params["spatialFilter"] = {
                        "filterType": "mbr",
                        "lowerLeft": {"latitude": bbox[1], "longitude": bbox[0]},
                        "upperRight": {"latitude": bbox[3], "longitude": bbox[2]},
                    }
                elif geometry is not None:
                    params["spatialFilter"] = {
                        "filterType": "geojson",
                        "geoJson": geometry,
                    }
                temporal: dict[str, Any] = {}
                if start is not None:
                    temporal["startDate"] = start.strftime("%Y-%m-%d")
                if end is not None:
                    temporal["endDate"] = end.strftime("%Y-%m-%d")
                if temporal:
                    params["temporalFilter"] = temporal
                if max_cloud_cover is not None:
                    params["metadataFilter"] = {
                        "filterType": "and",
                        "childFilters": [
                            {
                                "filterType": "between",
                                "fieldId": 20514,  # Cloud Cover
                                "firstValue": "0",
                                "secondValue": str(max_cloud_cover),
                            }
                        ],
                    }

                results = self.api.search(params)
                for r in results or []:
                    scenes.append(self._to_scene(r, dataset))
        except Exception as exc:
            raise SearchError(f"usgs_m2m search failed: {exc}") from exc

        log.info("m2m.search", provider=self.name, matched=len(scenes))
        return scenes

    def _to_scene(self, raw: dict[str, Any], dataset: str) -> SceneMeta:
        entity = raw.get("metadata", {})
        spatial = raw.get("spatialBounds") or {}
        ll = spatial.get("lowerLeft") or {}
        ur = spatial.get("upperRight") or {}
        bbox = (
            float(ll.get("longitude", 0.0)),
            float(ll.get("latitude", 0.0)),
            float(ur.get("longitude", 0.0)),
            float(ur.get("latitude", 0.0)),
        )
        dt_raw = entity.get("Date Acquired") or entity.get("Acquisition Date")
        dt = _parse_date(dt_raw)
        cloud = _to_float(entity.get("Cloud Cover") or entity.get("Image Cloud Cover"))
        level = (
            ProductLevel.L2
            if "l2" in dataset.lower() or "sr" in dataset.lower()
            else ProductLevel.L1
        )
        radiometry = (
            Radiometry.SURFACE_REFLECTANCE if level is ProductLevel.L2 else Radiometry.TOA
        )
        return SceneMeta(
            scene_id=str(raw.get("displayId") or raw.get("entityId") or raw.get("id")),
            provider=self.name,
            collection=dataset,
            datetime=dt,
            bbox=bbox,
            cloud_cover=cloud,
            platform=entity.get("Satellite"),
            sensor_key=_sensor_key(entity.get("Satellite")),
            product_level=level,
            radiometry=radiometry,
            source_format=SourceFormat.TAR,
            properties=dict(entity),
            raw_href=raw.get("productMetadata", {}).get("url"),
        )

    # ── 资产 / 下载 ───────────────────────────────────────────────────────
    def resolve_assets(self, scene: SceneMeta) -> list[AssetRef]:
        if scene.raw_href:
            return [
                AssetRef(
                    key="product",
                    href=scene.raw_href,
                    media_type="application/x-tar",
                    roles=("data",),
                    fetch_mode=FetchMode.DOWNLOAD,
                )
            ]
        return []

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
            raise DownloadError("usgs_m2m: asset has no href")
        return download_file(asset.href, dest, auth=self._auth)


def _sensor_key(satellite: str | None) -> str | None:
    text = (satellite or "").lower()
    if "9" in text or "8" in text:
        return "landsat-8-oli"
    if "7" in text:
        return "landsat-7-etm"
    return None


def _parse_date(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value
    if not value:
        return datetime.utcnow()
    text = str(value).replace("Z", "")
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%Y%m%d"):
        try:
            return datetime.strptime(text[:19], fmt)
        except ValueError:
            continue
    return datetime.utcnow()


def _to_float(value: Any) -> float | None:
    try:
        return float(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


__all__ = ["UsgsM2MClient"]
