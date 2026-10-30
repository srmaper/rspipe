"""rspipe —— 遥感卫星数据处理流水线。

覆盖：数据接入、归一化、COG 转换、去云、匀色、无缝镶嵌、STAC 元数据管理与定时更新管道。
"""

from __future__ import annotations

from rspipe.config import PipelineConfig, load_pipeline_config
from rspipe.models import AssetRef, FetchMode, ProductProfile, SceneMeta, SensorSpec

__all__ = [
    "AssetRef",
    "FetchMode",
    "PipelineConfig",
    "ProductProfile",
    "SceneMeta",
    "SensorSpec",
    "__version__",
    "load_pipeline_config",
]

__version__ = "0.1.0"
