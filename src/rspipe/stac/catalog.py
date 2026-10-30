"""STAC Catalog / Collection 构建与静态目录发布。"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pystac

from rspipe.errors import StacError
from rspipe.logging import get_logger

log = get_logger("rspipe.stac.catalog")


def make_collection(
    collection_id: str,
    *,
    title: str | None = None,
    description: str | None = None,
    license: str = "proprietary",
    extent_bbox: Sequence[float] | None = None,
    extent_interval: Sequence[str] | None = None,
    providers: Sequence[dict[str, Any]] | None = None,
    keywords: Sequence[str] | None = None,
    extra_fields: dict[str, Any] | None = None,
) -> pystac.Collection:
    """创建 STAC Collection。"""
    bbox = list(extent_bbox) if extent_bbox else [-180.0, -90.0, 180.0, 90.0]
    interval = (
        list(extent_interval)
        if extent_interval
        else [[None, None]]
    )
    spatial = pystac.SpatialExtent(bboxes=[bbox])
    temporal = pystac.TemporalExtent(intervals=[[_parse(s) for s in interval[0]]])
    extent = pystac.Extent(spatial=spatial, temporal=temporal)

    return pystac.Collection(
        id=collection_id,
        title=title or collection_id,
        description=description or f"rspipe processed collection {collection_id}",
        license=license,
        extent=extent,
        providers=[pystac.Provider(**p) for p in (providers or [])] or None,
        keywords=list(keywords) if keywords else None,
        extra_fields=extra_fields or {},
    )


def _parse(value: Any) -> datetime | None:
    if value in (None, "null"):
        return None
    if isinstance(value, datetime):
        return value
    text = str(value).replace("Z", "+00:00")
    dt = datetime.fromisoformat(text)
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt


def make_catalog(
    catalog_id: str = "rspipe",
    *,
    title: str | None = None,
    description: str | None = None,
    root_dir: Path | None = None,
) -> pystac.Catalog:
    cat = pystac.Catalog(
        id=catalog_id,
        title=title or "rspipe catalog",
        description=description or "Locally published STAC catalog produced by rspipe",
    )
    return cat


class CatalogPublisher:
    """管理静态 STAC 目录：根 catalog + N 个 collection + items。

    目录布局（pystac `normalize_hrefs` 风格）：

        catalog/
          catalog.json
          {collection}/
            {collection}.json
            {item_id}.json
    """

    def __init__(self, root_dir: Path, *, catalog_id: str = "rspipe") -> None:
        self.root_dir = Path(root_dir)
        self.catalog_id = catalog_id
        self.root_dir.mkdir(parents=True, exist_ok=True)

    @property
    def root_catalog_path(self) -> Path:
        return self.root_dir / "catalog.json"

    def collection_path(self, collection_id: str) -> Path:
        return self.root_dir / collection_id / f"{collection_id}.json"

    def item_path(self, collection_id: str, item_id: str) -> Path:
        return self.root_dir / collection_id / f"{item_id}.json"

    def ensure_collection(
        self,
        collection_id: str,
        **kwargs: Any,
    ) -> pystac.Collection:
        """确保 Collection 存在并落盘；返回对象。"""
        path = self.collection_path(collection_id)
        if path.exists():
            try:
                return pystac.Collection.from_file(str(path))
            except Exception:
                log.warning("stac.collection.reload-failed", id=collection_id)

        col = make_collection(collection_id, **kwargs)
        self._save_collection(col)
        return col

    def _save_collection(self, col: pystac.Collection) -> None:
        col_path = self.collection_path(col.id)
        col_path.parent.mkdir(parents=True, exist_ok=True)
        col.save_object(include_self_link=True, dest_href=str(col_path))

    def add_item(
        self,
        item: pystac.Item,
        *,
        collection_id: str | None = None,
    ) -> Path:
        """把 Item 写入目录并返回其 json 路径。"""
        cid = collection_id or item.collection_id
        if not cid:
            raise StacError(f"item {item.id} has no collection_id")

        self.ensure_collection(cid)
        item_path = self.item_path(cid, item.id)
        item_path.parent.mkdir(parents=True, exist_ok=True)
        item.save_object(include_self_link=True, dest_href=str(item_path))
        self.reindex_root()
        log.info("stac.item.saved", id=item.id, collection=cid, path=str(item_path))
        return item_path

    def reindex_root(self) -> None:
        """重建根 catalog.json，链接到全部 collection。"""
        catalog = make_catalog(self.catalog_id)
        for col_file in sorted(self.root_dir.glob("*/*.json")):
            if col_file.stem == col_file.parent.name:
                try:
                    col = pystac.Collection.from_file(str(col_file))
                except Exception:
                    continue
                catalog.add_child(col)
        catalog.save_object(
            include_self_link=True, dest_href=str(self.root_catalog_path)
        )

    def load_item(self, collection_id: str, item_id: str) -> pystac.Item:
        path = self.item_path(collection_id, item_id)
        if not path.exists():
            raise StacError(f"item not found: {path}")
        return pystac.Item.from_file(str(path))

    def iter_items(self, collection_id: str | None = None) -> Iterable[pystac.Item]:
        pattern = f"{collection_id}/*.json" if collection_id else "*/*.json"
        for path in sorted(self.root_dir.glob(pattern)):
            if path.stem == path.parent.name:
                continue  # collection 自身文件
            try:
                yield pystac.Item.from_file(str(path))
            except Exception:
                log.warning("stac.item.load-failed", path=str(path))

    def collections(self) -> list[str]:
        return sorted(
            p.stem for p in self.root_dir.glob("*/") if (p / f"{p.name}.json").exists()
        )


__all__ = ["CatalogPublisher", "make_catalog", "make_collection"]
