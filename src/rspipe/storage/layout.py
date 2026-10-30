"""本地数据目录布局。

data/
  raw/{provider}/{scene_id}/          原始下载（.SAFE / .tar / 原始 tif）
  staging/{scene_id}/                 中间产物（解包 / 归一化 / 掩膜）
  derived/cog/                        COG 产物
  derived/balanced/                   匀色后产物
  derived/mosaic/{aoi}/{date}/        镶嵌产物
  catalog/                            STAC 静态目录
  index/catalog.sqlite                检索索引
  state/watermark.sqlite              增量水位线
  state/runs.sqlite                   运行记录
  logs/
"""

from __future__ import annotations

from pathlib import Path

from rspipe.errors import ConfigError

CATALOG_DIR = "catalog"
INDEX_DIR = "index"
STATE_DIR = "state"
LOGS_DIR = "logs"
RAW_DIR = "raw"
STAGING_DIR = "staging"
DERIVED_DIR = "derived"


class DataLayout:
    """以 `data_root` 为根的目录布局，负责创建与路径解析。"""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)

    # ── 根目录 ────────────────────────────────────────────────────────────
    @property
    def raw(self) -> Path:
        return self.root / RAW_DIR

    @property
    def staging(self) -> Path:
        return self.root / STAGING_DIR

    @property
    def derived(self) -> Path:
        return self.root / DERIVED_DIR

    @property
    def cog_dir(self) -> Path:
        return self.derived / "cog"

    @property
    def balanced_dir(self) -> Path:
        return self.derived / "balanced"

    @property
    def catalog(self) -> Path:
        return self.root / CATALOG_DIR

    @property
    def index_dir(self) -> Path:
        return self.root / INDEX_DIR

    @property
    def state_dir(self) -> Path:
        return self.root / STATE_DIR

    @property
    def logs_dir(self) -> Path:
        return self.root / LOGS_DIR

    @property
    def catalog_index(self) -> Path:
        return self.index_dir / "catalog.sqlite"

    @property
    def watermark_db(self) -> Path:
        return self.state_dir / "watermark.sqlite"

    @property
    def runs_db(self) -> Path:
        return self.state_dir / "runs.sqlite"

    # ── 具体产物路径 ──────────────────────────────────────────────────────
    def raw_scene_dir(self, provider: str, scene_id: str) -> Path:
        return self.raw / provider / scene_id

    def staging_scene_dir(self, scene_id: str) -> Path:
        return self.staging / scene_id

    def cog_path(self, scene_id: str) -> Path:
        return self.cog_dir / f"{scene_id}.tif"

    def mask_path(self, scene_id: str) -> Path:
        return self.staging_scene_dir(scene_id) / f"{scene_id}.mask.tif"

    def balanced_path(self, scene_id: str) -> Path:
        return self.balanced_dir / f"{scene_id}.balanced.tif"

    def mosaic_dir(self, aoi_id: str, date: str) -> Path:
        return self.derived / "mosaic" / aoi_id / date

    def mosaic_path(self, aoi_id: str, date: str, ext: str = "tif") -> Path:
        return self.mosaic_dir(aoi_id, date) / f"mosaic.{ext}"

    # ── STAC 目录布局（pystac normalize_hrefs 风格）──────────────────────
    def stac_collection_dir(self, collection: str) -> Path:
        return self.catalog / collection

    def stac_item_path(self, collection: str, scene_id: str) -> Path:
        return self.stac_collection_dir(collection) / f"{scene_id}.json"

    # ── 生命周期 ──────────────────────────────────────────────────────────
    def ensure(self) -> None:
        """创建全部基础目录。"""
        for p in (
            self.raw,
            self.staging,
            self.cog_dir,
            self.balanced_dir,
            self.catalog,
            self.index_dir,
            self.state_dir,
            self.logs_dir,
        ):
            p.mkdir(parents=True, exist_ok=True)

    def ensure_scene(self, provider: str, scene_id: str) -> Path:
        d = self.raw_scene_dir(provider, scene_id)
        d.mkdir(parents=True, exist_ok=True)
        return d

    def validate_root(self) -> None:
        if not self.root.exists():
            raise ConfigError(f"data root does not exist: {self.root}")


def build_layout(root: str | Path) -> DataLayout:
    """创建并返回布局对象。"""
    layout = DataLayout(root)
    layout.ensure()
    return layout
