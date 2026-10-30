"""STAC 元数据管理：Item / Collection 构建、静态目录发布、SQLite 索引。"""

from __future__ import annotations

from rspipe.stac.catalog import CatalogPublisher, make_catalog, make_collection
from rspipe.stac.items import EXT_EO, EXT_PROJ, EXT_VIEW, build_item, item_summary, validate_item
from rspipe.stac.store import IndexRecord, StacIndex, record_from_item_dict

__all__ = [
    "EXT_EO",
    "EXT_PROJ",
    "EXT_VIEW",
    "CatalogPublisher",
    "IndexRecord",
    "StacIndex",
    "build_item",
    "item_summary",
    "make_catalog",
    "make_collection",
    "record_from_item_dict",
    "validate_item",
]
