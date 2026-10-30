"""STAC Item 生成：由处理产物构建带 eo/proj/view 扩展的 Item。

注意：pystac 1.15 起扩展拆分为独立包（`pystac-ext-eo` 等）。
这里只用核心 `pystac` API，扩展字段直接写入 `properties`，
扩展 schema URI 写入 `stac_extensions`，以保持对 pystac 版本的兼容。
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pystac
import rasterio

from rspipe.errors import StacError
from rspipe.logging import get_logger
from rspipe.models import SceneMeta

log = get_logger("rspipe.stac.items")

# 扩展 schema URI（STAC 1.x 通用）
EXT_EO = "https://stac-extensions.github.io/eo/v1.1.0/schema.json"
EXT_PROJ = "https://stac-extensions.github.io/projection/v1.1.0/schema.json"
EXT_VIEW = "https://stac-extensions.github.io/view/v1.0.0/schema.json"

MEDIA_COG = "image/tiff; application=geotiff; profile=cloud-optimized"
MEDIA_GTIF = "image/tiff; application=geotiff"
MEDIA_MASK = "image/tiff; application=geotiff"
MEDIA_JSON = "application/json"
MEDIA_JPEG = "image/jpeg"


def _to_utc(dt: datetime) -> datetime:
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt.astimezone(UTC)


def _bbox_to_geometry(bbox: tuple[float, float, float, float]) -> dict[str, Any]:
    minx, miny, maxx, maxy = bbox
    return {
        "type": "Polygon",
        "coordinates": [
            [
                [minx, miny],
                [maxx, miny],
                [maxx, maxy],
                [minx, maxy],
                [minx, miny],
            ]
        ],
    }


def _read_raster_meta(path: str | Path) -> dict[str, Any]:
    with rasterio.open(str(path)) as src:
        return {
            "crs": src.crs,
            "transform": src.transform,
            "shape": (src.height, src.width),
            "count": src.count,
            "dtype": src.dtypes[0],
            "bounds": tuple(src.bounds),
            "nodata": src.nodata,
            "descriptions": list(src.descriptions),
        }


def build_item(
    *,
    scene: SceneMeta,
    cog_path: Path,
    mask_path: Path | None = None,
    thumbnail_path: Path | None = None,
    extra_assets: dict[str, tuple[Path, str, Sequence[str]]] | None = None,
    collection_id: str | None = None,
    cog_href: str | None = None,
    mask_href: str | None = None,
    item_id: str | None = None,
    properties_extra: dict[str, Any] | None = None,
) -> pystac.Item:
    """构建 STAC Item。

    `cog_href` / `mask_href` 为资产对外可访问的 href；省略则用本地绝对路径。
    """
    if not Path(cog_path).exists():
        raise StacError(f"COG not found: {cog_path}")

    meta = _read_raster_meta(cog_path)
    bbox = tuple(meta["bounds"]) if meta["bounds"] else scene.bbox
    if len(bbox) != 4:
        bbox = scene.bbox
    bbox_t = (float(bbox[0]), float(bbox[1]), float(bbox[2]), float(bbox[3]))
    geometry = scene.geometry or _bbox_to_geometry(bbox_t)

    dt = _to_utc(scene.datetime)
    iid = item_id or scene.scene_id

    properties: dict[str, Any] = {
        "datetime": dt.isoformat().replace("+00:00", "Z"),
        "eo:cloud_cover": scene.cloud_cover,
        "platform": scene.platform,
        "instruments": scene.properties.get("instruments"),
        "proj:epsg": meta["crs"].to_epsg() if meta["crs"] else None,
        "proj:shape": list(meta["shape"]),
        "proj:transform": list(meta["transform"])[:6],
        "rspi:source_provider": scene.provider,
        "rspi:source_collection": scene.collection,
        "rspi:source_scene_id": scene.scene_id,
        "rspi:product_level": scene.product_level.value,
        "rspi:radiometry": scene.radiometry.value,
    }
    if properties_extra:
        properties.update(properties_extra)
    properties = {k: v for k, v in properties.items() if v is not None}

    item = pystac.Item(
        id=iid,
        geometry=geometry,
        bbox=list(bbox_t),
        datetime=dt,
        properties=properties,
        collection=collection_id,
    )
    item.stac_extensions = [EXT_EO, EXT_PROJ]

    # STAC 规范要求：带 `collection` 字段的 Item 必须同时有 rel=collection 的 link
    if collection_id:
        item.add_link(
            pystac.Link(
                rel=pystac.RelType.COLLECTION,
                target=f"{collection_id}.json",
                media_type=pystac.MediaType.JSON,
                title=collection_id,
            )
        )

    # 主 COG 资产
    href = cog_href or str(Path(cog_path).resolve())
    item.add_asset(
        "data",
        pystac.Asset(
            href=href,
            media_type=MEDIA_COG,
            roles=["data"],
            title="Analysis-ready surface reflectance (COG)",
            extra_fields={
                "raster:bands": [
                    {
                        "name": d or f"b{i}",
                        "data_type": meta["dtype"],
                        "nodata": (
                            None
                            if meta["nodata"] is None or not np.isfinite(meta["nodata"])
                            else float(meta["nodata"])
                        ),
                    }
                    for i, d in enumerate(meta["descriptions"], start=1)
                ],
            },
        ),
    )

    if mask_path is not None and Path(mask_path).exists():
        item.add_asset(
            "mask",
            pystac.Asset(
                href=mask_href or str(Path(mask_path).resolve()),
                media_type=MEDIA_MASK,
                roles=["data-mask"],
                title="Cloud / cloud-shadow mask",
            ),
        )

    if thumbnail_path is not None and Path(thumbnail_path).exists():
        item.add_asset(
            "thumbnail",
            pystac.Asset(
                href=str(Path(thumbnail_path).resolve()),
                media_type=MEDIA_JPEG,
                roles=["thumbnail"],
                title="Preview",
            ),
        )

    if extra_assets:
        for key, (path, media, roles) in extra_assets.items():
            if Path(path).exists():
                item.add_asset(
                    key,
                    pystac.Asset(
                        href=str(Path(path).resolve()),
                        media_type=media,
                        roles=list(roles),
                    ),
                )

    log.info("stac.item.built", id=iid, assets=len(item.assets))
    return item


def validate_item(item: pystac.Item) -> bool:
    """校验 Item 是否符合 STAC 规范；失败返回 False。"""
    try:
        from pystac.validation import validate_all

        validate_all(item)
        return True
    except Exception as exc:
        log.warning("stac.item.invalid", id=item.id, error=str(exc))
        return False


def item_summary(item: pystac.Item) -> dict[str, Any]:
    return {
        "id": item.id,
        "collection": item.collection_id,
        "datetime": item.datetime.isoformat() if item.datetime else None,
        "bbox": item.bbox,
        "assets": list(item.assets),
        "extensions": item.stac_extensions,
    }


__all__ = [
    "EXT_EO",
    "EXT_PROJ",
    "EXT_VIEW",
    "MEDIA_COG",
    "MEDIA_GTIF",
    "build_item",
    "item_summary",
    "validate_item",
]
