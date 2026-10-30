"""STAC 生成与索引单测。"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from rspipe.models import (
    ProductLevel,
    Radiometry,
    SceneMeta,
    SourceFormat,
)
from rspipe.stac.catalog import CatalogPublisher
from rspipe.stac.items import build_item, validate_item
from rspipe.stac.store import StacIndex, record_from_item_dict


def _scene(sid: str = "SCENE_001", cloud: float = 12.5) -> SceneMeta:
    return SceneMeta(
        scene_id=sid,
        provider="earth-search",
        collection="sentinel-2-l2a",
        datetime=datetime(2026, 5, 8, tzinfo=UTC),
        bbox=(116.0, 39.9, 116.1, 40.0),
        cloud_cover=cloud,
        platform="sentinel-2a",
        sensor_key="sentinel-2-msi",
        product_level=ProductLevel.L2,
        radiometry=Radiometry.SURFACE_REFLECTANCE,
        source_format=SourceFormat.COG,
    )


def test_build_item_basics(stack_file: Path):
    item = build_item(scene=_scene(), cog_path=stack_file, collection_id="reflectance")
    assert item.id == "SCENE_001"
    assert item.collection_id == "reflectance"
    assert "data" in item.assets
    assert item.assets["data"].roles == ["data"]
    assert item.properties["eo:cloud_cover"] == 12.5
    assert item.properties["proj:epsg"] == 4326
    assert validate_item(item)


def test_build_item_with_mask(stack_file: Path, qa_file: Path):
    item = build_item(
        scene=_scene(),
        cog_path=stack_file,
        mask_path=qa_file,
        collection_id="reflectance",
    )
    assert "mask" in item.assets
    assert item.assets["mask"].roles == ["data-mask"]


def test_item_has_collection_link(stack_file: Path):
    item = build_item(scene=_scene(), cog_path=stack_file, collection_id="reflectance")
    rels = [lnk.rel for lnk in item.links]
    assert "collection" in rels
    assert validate_item(item)


def test_item_nodata_is_json_safe(stack_file: Path):
    item = build_item(scene=_scene(), cog_path=stack_file, collection_id="c")
    import json

    text = json.dumps(item.to_dict())
    assert "NaN" not in text


def test_catalog_publisher_roundtrip(stack_file: Path, tmp_path: Path):
    pub = CatalogPublisher(tmp_path / "catalog")
    item = build_item(scene=_scene(), cog_path=stack_file, collection_id="reflectance")
    path = pub.add_item(item, collection_id="reflectance")
    assert path.exists()
    assert pub.root_catalog_path.exists()
    assert "reflectance" in pub.collections()
    loaded = pub.load_item("reflectance", "SCENE_001")
    assert loaded.id == "SCENE_001"
    assert len(list(pub.iter_items())) == 1


def test_catalog_accumulates_items(stack_file: Path, tmp_path: Path):
    pub = CatalogPublisher(tmp_path / "catalog")
    for i in range(3):
        it = build_item(
            scene=_scene(f"S{i}"), cog_path=stack_file, collection_id="reflectance"
        )
        pub.add_item(it, collection_id="reflectance")
    assert len(list(pub.iter_items())) == 3
    assert len(list(pub.iter_items("reflectance"))) == 3


def test_stac_index_search_filters(stack_file: Path, tmp_path: Path):
    idx = StacIndex(tmp_path / "idx.sqlite")
    for i, cloud in enumerate([5.0, 25.0, 50.0]):
        it = build_item(
            scene=_scene(f"S{i}", cloud=cloud),
            cog_path=stack_file,
            collection_id="reflectance",
        )
        idx.upsert(record_from_item_dict(it.to_dict(), href=f"/x/{it.id}.json"))

    all_hits = idx.search(limit=100)
    assert len(all_hits) == 3

    cloudy = idx.search(max_cloud_cover=10)
    assert len(cloudy) == 1
    assert cloudy[0].cloud_cover == 5.0

    # bbox 相交判定
    inside = idx.search(bbox=(116.01, 39.91, 116.09, 39.99))
    assert len(inside) == 3
    outside = idx.search(bbox=(120.0, 30.0, 121.0, 31.0))
    assert len(outside) == 0


def test_stac_index_datetime_range(stack_file: Path, tmp_path: Path):
    idx = StacIndex(tmp_path / "idx.sqlite")
    it = build_item(scene=_scene(), cog_path=stack_file, collection_id="c")
    idx.upsert(record_from_item_dict(it.to_dict(), href="h"))
    assert len(idx.search(start="2026-01-01", end="2026-12-31")) == 1
    assert len(idx.search(start="2027-01-01")) == 0


def test_stac_index_upsert_idempotent(stack_file: Path, tmp_path: Path):
    idx = StacIndex(tmp_path / "idx.sqlite")
    it = build_item(scene=_scene(), cog_path=stack_file, collection_id="c")
    rec = record_from_item_dict(it.to_dict(), href="h")
    idx.upsert(rec)
    idx.upsert(rec)
    assert idx.stats()["total"] == 1


def test_stac_index_delete(stack_file: Path, tmp_path: Path):
    idx = StacIndex(tmp_path / "idx.sqlite")
    it = build_item(scene=_scene(), cog_path=stack_file, collection_id="c")
    idx.upsert(record_from_item_dict(it.to_dict(), href="h"))
    assert idx.delete("c", "SCENE_001") is True
    assert idx.stats()["total"] == 0
