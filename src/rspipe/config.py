"""配置加载：YAML + .env + 环境变量。"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from rspipe.errors import ConfigError
from rspipe.models import AuthType, FetchMode, ProviderType, SensorSpec


# ── YAML 子模型 ────────────────────────────────────────────────────────────────
class ProviderConfig(BaseModel):
    """configs/providers.yaml 中的一条数据源配置。"""

    name: str = ""
    type: ProviderType
    url: str | None = None
    collections: list[str] = Field(default_factory=list)
    datasets: list[str] = Field(default_factory=list)
    auth: AuthType = AuthType.NONE
    token_url: str | None = None
    base_url: str | None = None
    s3_endpoint: str | None = None
    fetch_mode: FetchMode = FetchMode.DOWNLOAD
    enabled: bool = True


class NormalizeConfig(BaseModel):
    target_dtype: str = "float32"
    target_nodata: float = float("nan")
    target_crs: str | None = None
    target_resolution: float | None = None


class CloudMaskConfig(BaseModel):
    buffer_pixels: int = 3
    max_cloud_cover: float = 30.0
    keep_snow: bool = True
    keep_water: bool = True


class ColorBalanceConfig(BaseModel):
    method: str = "histogram"
    reference: str = "best"
    quantiles: tuple[float, float] = (2.0, 98.0)


class MosaicConfig(BaseModel):
    feather: str = "distance"
    blend_distance: int = 64
    dtype: str = "float32"


class ScheduleConfig(BaseModel):
    cron: dict[str, Any] = Field(default_factory=lambda: {"hour": 2, "minute": 0, "timezone": "UTC"})
    lookback_days: int = 3
    max_scenes_per_run: int = 50
    max_retries: int = 2


class PipelineSection(BaseModel):
    name: str = "default"
    aoi: str | None = None
    start: str | None = None
    end: str | None = None
    max_cloud_cover: float = 30.0
    sensors: list[str] = Field(default_factory=list)
    providers: list[str] = Field(default_factory=list)
    bands: list[str] = Field(default_factory=lambda: ["red", "green", "blue", "nir"])


class StorageSection(BaseModel):
    root: str | None = None


class LoggingSection(BaseModel):
    level: str = "INFO"
    json_logs: bool = Field(default=False, alias="json")

    model_config = {"populate_by_name": True}


# ── 顶层配置 ──────────────────────────────────────────────────────────────────
class PipelineConfig(BaseModel):
    """rspipe 运行时的聚合配置。"""

    pipeline: PipelineSection = Field(default_factory=PipelineSection)
    schedule: ScheduleConfig = Field(default_factory=ScheduleConfig)
    storage: StorageSection = Field(default_factory=StorageSection)
    logging: LoggingSection = Field(default_factory=LoggingSection)

    providers: dict[str, ProviderConfig] = Field(default_factory=dict)
    sensors: dict[str, SensorSpec] = Field(default_factory=dict)

    normalize: NormalizeConfig = Field(default_factory=NormalizeConfig)
    cloud_mask: CloudMaskConfig = Field(default_factory=CloudMaskConfig)
    color_balance: ColorBalanceConfig = Field(default_factory=ColorBalanceConfig)
    mosaic: MosaicConfig = Field(default_factory=MosaicConfig)

    # 运行期解析出的绝对路径
    data_root: Path = Path("data")

    model_config = {"arbitrary_types_allowed": True}

    def provider(self, name: str) -> ProviderConfig:
        try:
            return self.providers[name]
        except KeyError as exc:
            raise ConfigError(f"unknown provider: {name!r}") from exc

    def sensor(self, key: str) -> SensorSpec:
        try:
            return self.sensors[key]
        except KeyError as exc:
            raise ConfigError(f"unknown sensor: {key!r}") from exc

    def enabled_providers(self) -> list[ProviderConfig]:
        wanted = set(self.pipeline.providers)
        return [
            p
            for name, p in self.providers.items()
            if p.enabled and (not wanted or name in wanted)
        ]


# ── 环境变量（.env） ──────────────────────────────────────────────────────────
class Settings(BaseSettings):
    """仅承载密钥与全局开关，不进 YAML。"""

    usgs_m2m_username: str = ""
    usgs_m2m_token: str = ""
    cdse_client_id: str = ""
    cdse_client_secret: str = ""
    cdse_username: str = ""
    cdse_password: str = ""
    rspipe_data_root: str = ""
    rspipe_log_level: str = ""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )


# ── 加载入口 ──────────────────────────────────────────────────────────────────
def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise ConfigError(f"config file not found: {path}")
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise ConfigError(f"invalid YAML in {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ConfigError(f"top-level YAML in {path} must be a mapping")
    return data


def _build_sensors(raw: dict[str, Any]) -> dict[str, SensorSpec]:
    out: dict[str, SensorSpec] = {}
    for key, spec in (raw.get("sensors") or {}).items():
        if not isinstance(spec, dict):
            raise ConfigError(f"sensor {key!r} must be a mapping")
        band_map = spec.get("band_map") or {}
        if not band_map:
            raise ConfigError(f"sensor {key!r} must define band_map")
        vdn = spec.get("valid_dn_range") or [0, 65535]
        rrange = spec.get("reflectance_range") or [0.0, 1.0]
        out[key] = SensorSpec(
            key=key,
            band_map=dict(band_map),
            qa_band=spec.get("qa_band"),
            qa_bits=dict(spec.get("qa_bits") or {}),
            scl_values={int(k): bool(v) for k, v in (spec.get("scl_values") or {}).items()},
            reflectance_scale=float(spec.get("reflectance_scale", 1.0)),
            reflectance_offset=float(spec.get("reflectance_offset", 0.0)),
            valid_dn_range=(int(vdn[0]), int(vdn[1])),
            reflectance_range=(float(rrange[0]), float(rrange[1])),
            alias=tuple(spec.get("alias") or ()),
        )
    return out


def _build_providers(raw: dict[str, Any]) -> dict[str, ProviderConfig]:
    out: dict[str, ProviderConfig] = {}
    for name, spec in (raw.get("providers") or {}).items():
        if not isinstance(spec, dict):
            raise ConfigError(f"provider {name!r} must be a mapping")
        try:
            out[name] = ProviderConfig(name=name, **spec)
        except Exception as exc:
            raise ConfigError(f"invalid provider {name!r}: {exc}") from exc
    return out


def load_pipeline_config(
    config_dir: str | Path = "configs",
    *,
    env_file: str | Path | None = ".env",
) -> PipelineConfig:
    """从 `config_dir` 加载 pipeline / providers / sensors 三份 YAML 并合并环境变量。"""
    cfg_dir = Path(config_dir)
    if not cfg_dir.is_dir():
        raise ConfigError(f"config dir not found: {cfg_dir}")

    pipeline_raw = _read_yaml(cfg_dir / "pipeline.yaml")
    providers_raw = _read_yaml(cfg_dir / "providers.yaml")
    sensors_raw = _read_yaml(cfg_dir / "sensors.yaml")

    if env_file is not None and Path(env_file).exists():
        # 让 pydantic-settings 读取该 .env
        os.environ.setdefault("DOTENV_PATH", str(env_file))

    settings = Settings(_env_file=str(env_file) if env_file and Path(env_file).exists() else None)

    sections: dict[str, Any] = {
        "pipeline": pipeline_raw.get("pipeline", {}),
        "schedule": pipeline_raw.get("schedule", {}),
        "storage": pipeline_raw.get("storage", {}),
        "logging": pipeline_raw.get("logging", {}),        "normalize": sensors_raw.get("normalize", {}),
        "cloud_mask": sensors_raw.get("cloud_mask", {}),
        "color_balance": sensors_raw.get("color_balance", {}),
        "mosaic": sensors_raw.get("mosaic", {}),
    }

    cfg = PipelineConfig(
        **sections,
        providers=_build_providers(providers_raw),
        sensors=_build_sensors(sensors_raw),
    )

    # 数据根目录优先级：storage.root > RSPIPE_DATA_ROOT > ./data
    root = cfg.storage.root or settings.rspipe_data_root or "data"
    cfg.data_root = Path(root).expanduser().resolve()

    if settings.rspipe_log_level:
        cfg.logging.level = settings.rspipe_log_level

    _validate(cfg)
    return cfg


def _validate(cfg: PipelineConfig) -> None:
    for name in cfg.pipeline.providers:
        if name not in cfg.providers:
            raise ConfigError(f"pipeline.providers references unknown provider: {name!r}")
    for key in cfg.pipeline.sensors:
        if key not in cfg.sensors:
            raise ConfigError(f"pipeline.sensors references unknown sensor: {key!r}")
    if cfg.pipeline.aoi and not Path(cfg.pipeline.aoi).exists():
        raise ConfigError(f"AOI file not found: {cfg.pipeline.aoi}")
