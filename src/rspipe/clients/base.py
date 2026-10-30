"""ProviderClient 抽象协议。"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from rspipe.models import AssetRef, FetchMode, SceneMeta


@runtime_checkable
class ProviderClient(Protocol):
    """数据源客户端统一接口。

    实现方：`StacClient`（主路径）、`UsgsM2MClient` / `CdseODataClient`（补充路径）。
    """

    name: str

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
        """按空间/时间/云量检索景。"""
        ...

    def resolve_assets(self, scene: SceneMeta) -> list[AssetRef]:
        """展开景的资产列表（波段 COG / QA / 缩略图 / 原始归档）。"""
        ...

    def fetch(
        self,
        asset: AssetRef,
        dest: Path,
        *,
        mode: FetchMode | None = None,
        bbox: tuple[float, float, float, float] | None = None,
    ) -> Path:
        """把资产落到本地（或返回可被 rasterio 打开的 VSI 路径）。"""
        ...

    def close(self) -> None:
        """释放连接。"""
        ...
