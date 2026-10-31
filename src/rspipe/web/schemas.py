"""Web 层数据模型。"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator


class SearchReq(BaseModel):
    bbox: tuple[float, float, float, float] = Field(
        ..., description="minx,miny,maxx,maxy (EPSG:4326)"
    )
    start: str | None = Field(None, description="ISO8601 日期，如 2026-09-20")
    end: str | None = Field(None, description="ISO8601 日期")
    cloud: float = Field(30.0, description="整景云量上限 %")
    providers: list[tuple[str, str]] = Field(
        [
            ("earth-search", "sentinel-2-l2a"),
            ("planetary-computer", "landsat-c2-l2"),
        ],
        description="[(provider, collection), ...]",
    )
    min_overlap: float = 0.2
    cloud_mode: Literal["scene", "aoi"] = Field(
        "scene", description="scene=整景云量（快）；aoi=逐景读 QA 实测 AOI 云量（准，冷检索慢）"
    )

    @field_validator("providers", mode="before")
    @classmethod
    def _norm_providers(cls, v):
        """兼容 [ [a,b] ] 与 [ {provider:a, collection:b} ] 两种形态。"""
        out: list[Any] = []
        for item in v or []:
            if isinstance(item, dict):
                out.append((item.get("provider", ""), item.get("collection", "")))
            else:
                out.append(item)
        return out


class SceneOut(BaseModel):
    scene_id: str
    provider: str
    collection: str
    datetime: str
    cloud_cover: float | None
    aoi_cloud: float | None
    overlap: float
    tier: str | None = None
    footprint: dict[str, Any] | None = None
    bbox: list[float] | None = None
    thumbnail: str | None = None
    has_raw: bool = False
    raw_bounds: list[float] | None = None
    cog_bounds: list[float] | None = None
    has_cog: bool = False


class JobReq(BaseModel):
    type: Literal["raw", "processed"]
    provider: str
    collection: str
    scene_id: str
    aoi_bbox: tuple[float, float, float, float]
    bands: list[str] | None = Field(None, description="raw 下载的波段白名单；None=全波段")


class TileParams(BaseModel):
    channels: Literal["rgb", "false", "ndvi"] = "rgb"
