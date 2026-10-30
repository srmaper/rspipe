"""领域模型：场景、资产、产品画像、传感器规格。"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Literal


class ProviderType(enum.StrEnum):
    STAC = "stac"
    USGS_M2M = "usgs_m2m"
    CDSE_ODATA = "cdse_odata"


class AuthType(enum.StrEnum):
    NONE = "none"
    OAUTH2 = "oauth2"
    M2M_TOKEN = "m2m_token"
    BASIC = "basic"


class FetchMode(enum.StrEnum):
    """资产获取方式。"""

    VSI = "vsi"  # rasterio vsicurl / vsis3，云端按需读取，不落盘
    DOWNLOAD = "download"  # 完整下载到 raw/
    WINDOWED = "windowed"  # 按 AOI 窗口下载（COG 优化读 / HTTP Range）


class SourceFormat(enum.StrEnum):
    COG = "cog"
    GEOTIFF = "geotiff"
    SAFE = "safe"
    TAR = "tar"
    NETCDF = "netcdf"


class ProductLevel(enum.StrEnum):
    L1 = "l1"
    L2 = "l2"
    L3 = "l3"


class Radiometry(enum.StrEnum):
    DN = "dn"
    TOA = "toa"
    SURFACE_REFLECTANCE = "surface_reflectance"
    OTHER = "other"


@dataclass(slots=True)
class SensorSpec:
    """传感器规格：波段映射、QA 定义、兜底定标系数。"""

    key: str
    band_map: dict[str, str]
    qa_band: str | None = None
    qa_bits: dict[str, int] = field(default_factory=dict)
    scl_values: dict[int, bool] = field(default_factory=dict)
    reflectance_scale: float = 1.0
    reflectance_offset: float = 0.0
    valid_dn_range: tuple[int, int] = (0, 65535)
    reflectance_range: tuple[float, float] = (0.0, 1.0)
    alias: tuple[str, ...] = ()

    def band(self, common: str) -> str:
        """通用波段名 -> 产品波段名。"""
        try:
            return self.band_map[common]
        except KeyError as exc:
            raise KeyError(f"sensor {self.key!r} has no band {common!r}") from exc


@dataclass(slots=True)
class AssetRef:
    """STAC item 资产的本地/远程引用。"""

    key: str
    href: str
    media_type: str | None = None
    roles: tuple[str, ...] = ()
    fetch_mode: FetchMode = FetchMode.DOWNLOAD
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def is_raster(self) -> bool:
        mt = (self.media_type or "").lower()
        return "tif" in mt or "tiff" in mt or "cog" in mt or "geotiff" in mt

    @property
    def is_data(self) -> bool:
        return "data" in self.roles or self.is_raster


@dataclass(slots=True)
class SceneMeta:
    """一景影像的统一描述，来自 STAC item 或原生 API。"""

    scene_id: str
    provider: str
    collection: str
    datetime: datetime
    bbox: tuple[float, float, float, float]  # minx, miny, maxx, maxy (EPSG:4326)
    geometry: dict[str, Any] | None = None
    cloud_cover: float | None = None
    platform: str | None = None
    sensor_key: str | None = None  # 对应 configs/sensors.yaml 的 key
    tier: str | None = None        # Landsat 数据级别（T1/T2）；非 Landsat 为 None
    product_level: ProductLevel = ProductLevel.L2
    radiometry: Radiometry = Radiometry.SURFACE_REFLECTANCE
    source_format: SourceFormat = SourceFormat.COG
    assets: list[AssetRef] = field(default_factory=list)
    properties: dict[str, Any] = field(default_factory=dict)
    raw_href: str | None = None  # 原始归档（.SAFE / .tar）位置

    def asset_by_role(self, *roles: str) -> AssetRef | None:
        for a in self.assets:
            if any(r in a.roles for r in roles):
                return a
        return None

    def asset_by_key(self, key: str) -> AssetRef | None:
        for a in self.assets:
            if a.key == key:
                return a
        return None

    def band_assets(self) -> dict[str, AssetRef]:
        """返回波段资产映射，key 为产品波段名（如 B4 / B04）。"""
        out: dict[str, AssetRef] = {}
        for a in self.assets:
            if a.is_data:
                out[a.key] = a
        return out


@dataclass(slots=True)
class ProductProfile:
    """产品画像：由它推导出流水线步骤序列。"""

    source_format: SourceFormat = SourceFormat.COG
    product_level: ProductLevel = ProductLevel.L2
    radiometry: Radiometry = Radiometry.SURFACE_REFLECTANCE
    has_qa: bool = False
    crs_known: bool = True

    @property
    def needs_unpack(self) -> bool:
        return self.source_format in (SourceFormat.SAFE, SourceFormat.TAR)

    @property
    def needs_radiometric_normalize(self) -> bool:
        return self.radiometry is Radiometry.DN

    @property
    def needs_cog_convert(self) -> bool:
        return self.source_format is not SourceFormat.COG

    @property
    def needs_harmonize(self) -> bool:
        return self.source_format is SourceFormat.COG


@dataclass(slots=True)
class StepResult:
    """单个流水线步骤的执行结果。"""

    name: str
    status: Literal["ok", "skipped", "failed"]
    outputs: list[Path] = field(default_factory=list)
    elapsed_s: float = 0.0
    message: str = ""
    metrics: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class SceneResult:
    """一景影像端到端处理结果。"""

    scene_id: str
    profile: ProductProfile
    steps: list[StepResult] = field(default_factory=list)
    cog_path: Path | None = None
    mask_path: Path | None = None
    stac_item_path: Path | None = None
    mosaic_path: Path | None = None
    status: Literal["ok", "partial", "failed"] = "ok"
    error: str | None = None
    # AOI 质量指标（由 _step_cloud_mask 采集）
    aoi_cloud_cover: float | None = None   # AOI 窗口内云+云影占比 0..1
    valid_fraction: float | None = None    # 去云后有效像元占比 0..1

    @property
    def failed_steps(self) -> list[str]:
        return [s.name for s in self.steps if s.status == "failed"]
