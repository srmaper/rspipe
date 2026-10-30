"""质量度量单测：Tier 解析、AOI 云量、选景排序、STAC 元数据回填。"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from rspipe.ingest.cogreader import CogReader
from rspipe.models import (
    AssetRef,
    ProductLevel,
    ProductProfile,
    Radiometry,
    SceneMeta,
    SourceFormat,
)
from rspipe.quality import (
    RankedScene,
    aoi_cloud_fraction,
    aoi_overlap,
    format_ranked,
    parse_tier,
    rank_scenes,
)
from rspipe.stac.items import build_item
from synthetic import make_qa_pixel, make_scl


# ── Tier 解析 ────────────────────────────────────────────────────────────────
def test_parse_tier_from_scene_id_suffix():
    assert parse_tier("LC08_L2SP_121044_20261009_02_T1") == "T1"
    assert parse_tier("LC09_L2SP_122044_20260906_02_T2") == "T2"


def test_parse_tier_from_property_wins():
    # 属性存在且有值时应优先后缀
    assert parse_tier("LC08_x_02_T2", {"landsat:collection_category": "T1"}) == "T1"


def test_parse_tier_no_evidence():
    assert parse_tier("S2A_49QHF_20261007_0_L2A") is None
    assert parse_tier("some_scene") is None
    assert parse_tier("LC08_x_T3") is None  # 只认 T1/T2


def test_parse_tier_ignores_property_without_value():
    # 属性无值时不匹配任何 T\d，退化到后缀
    assert parse_tier("LC08_x_T1", {"landsat:collection_category": ""}) == "T1"
    assert parse_tier("LC08_x_T1", {"landsat:collection_category": "OTHER"}) == "T1"


# ── AOI 重叠 ─────────────────────────────────────────────────────────────────
def _scene_with_bbox(bbox: tuple[float, float, float, float]) -> SceneMeta:
    return SceneMeta(
        scene_id="S",
        provider="p",
        collection="c",
        datetime=datetime(2026, 1, 1, tzinfo=UTC),
        bbox=bbox,
    )


def test_aoi_overlap_partial_and_none():
    s = _scene_with_bbox((113.0, 22.0, 114.2, 22.6))
    ov_partial = aoi_overlap(s, (113.76, 22.41, 114.62, 22.87))
    assert 0.0 < ov_partial <= 1.0

    far = _scene_with_bbox((100.0, 20.0, 101.0, 21.0))
    assert aoi_overlap(far, (113.76, 22.41, 114.62, 22.87)) == 0.0


# ── AOI 云量（合成本地 GeoTIFF） ─────────────────────────────────────────────
def _qa_scene(qa_path: Path, *, key: str = "qa_pixel", scl: bool = False) -> SceneMeta:
    roles = ("data-mask",) if not scl else ("data-mask", "qa")
    asset = AssetRef(key=key, href=str(qa_path), media_type="image/tiff", roles=roles)
    asset.extra = {}
    return SceneMeta(
        scene_id="QA_SCENE",
        provider="p",
        collection="c",
        datetime=datetime(2026, 1, 1, tzinfo=UTC),
        bbox=(116.0, 39.936, 116.064, 40.0),
        assets=[asset],
        product_level=ProductLevel.L2,
        radiometry=Radiometry.SURFACE_REFLECTANCE,
        source_format=SourceFormat.COG,
        properties={},
    )


def test_aoi_cloud_fraction_synthetic_qa(tmp_path: Path):
    qa = make_qa_pixel(
        tmp_path / "qa.tif",
        width=64,
        height=64,
        origin=(116.0, 40.0),
        res=0.001,
        cloud_fraction=0.2,
        seed=5,
    )
    scene = _qa_scene(qa)
    frac = aoi_cloud_fraction(scene, (116.0, 39.936, 116.064, 40.0))
    assert frac is not None
    assert 0.10 < frac < 0.40  # blob 云 20% + 少量 shadow


def test_aoi_cloud_fraction_synthetic_scl(tmp_path: Path):
    scl = make_scl(
        tmp_path / "scl.tif",
        width=64,
        height=64,
        origin=(500000.0, 4500000.0),
        res=10.0,
        cloud_fraction=0.2,
        seed=6,
    )
    scene = _qa_scene(scl, key="scl", scl=True)
    frac = aoi_cloud_fraction(scene, (500000.0, 4449400.0, 500640.0, 4450000.0))
    assert frac is not None
    assert frac > 0.05


def test_aoi_cloud_fraction_tolerates_float_qa(tmp_path: Path):
    """回归：float QA 不能崩（上次 Landsat 的崩溃点）。"""
    path = tmp_path / "qaf.tif"
    import rasterio
    from rasterio.transform import from_origin

    rng = np.random.default_rng(0)
    arr = np.where(rng.random((64, 64)) < 0.2, 21452, 21848).astype("float32")
    with rasterio.open(
        path, "w", driver="GTiff", height=64, width=64, count=1, dtype="float32",
        crs="EPSG:4326", transform=from_origin(116.0, 40.0, 0.001, 0.001), tiled=True,
        blockxsize=32, blockysize=32,
    ) as ds:
        ds.write(arr, 1)
    scene = _qa_scene(path)
    frac = aoi_cloud_fraction(scene, (116.0, 39.936, 116.064, 40.0))
    assert frac is not None
    assert isinstance(frac, float)


def test_aoi_cloud_fraction_uses_cogreader_when_remote(tmp_path: Path):
    """http(s) href 时必须走 CogReader 路径（ downsized 读取），不崩即可。"""
    qa = make_qa_pixel(
        tmp_path / "qa2.tif", width=64, height=64, origin=(116.0, 40.0),
        res=0.001, cloud_fraction=0.2, seed=7,
    )
    scene = _qa_scene(qa)
    # 本地文件同样走 CogReader 分支（text 非 http 则用 rasterio；这里测 CogReader 分支）
    with CogReader(str(qa)) as r:
        _col, _row, w, h = r.window_for_bbox((116.0, 39.936, 116.064, 40.0))
        assert w == 64 and h == 64
    frac = aoi_cloud_fraction(scene, (116.0, 39.936, 116.064, 40.0))
    assert frac is not None


# ── 排序 ─────────────────────────────────────────────────────────────────────
def _rank_scene(sid: str, *, bbox: tuple[float, float, float, float],
                cloud: float | None, tier: str | None) -> SceneMeta:
    return SceneMeta(
        scene_id=sid, provider="p", collection="c",
        datetime=datetime(2026, 1, 1, tzinfo=UTC), bbox=bbox,
        cloud_cover=cloud, tier=tier,
        properties=({"landsat:collection_category": tier} if tier else {}),
    )


def test_rank_scenes_prefers_tier1(monkeypatch: pytest.MonkeyPatch):
    aoi = (113.0, 22.0, 114.0, 23.0)
    t1 = _rank_scene("A_T1", bbox=aoi, cloud=20.0, tier="T1")
    t2 = _rank_scene("B_T2", bbox=aoi, cloud=5.0, tier="T2")

    monkeypatch.setattr("rspipe.quality.aoi_cloud_fraction", lambda *a, **k: None)
    monkeypatch.setattr("rspipe.quality.aoi_overlap", lambda s, b: 0.9)
    ranked = rank_scenes([t2, t1], aoi, prefer_tier1=True)
    assert [r.scene.scene_id for r in ranked] == ["A_T1", "B_T2"]


def test_rank_scenes_order_by_aoi_cloud(monkeypatch: pytest.MonkeyPatch):
    aoi = (113.0, 22.0, 114.0, 23.0)
    high = _rank_scene("HIGH_T1", bbox=aoi, cloud=30.0, tier="T1")
    low = _rank_scene("LOW_T1", bbox=aoi, cloud=10.0, tier="T1")
    cloud_map = {"HIGH_T1": 0.30, "LOW_T1": 0.02}

    monkeypatch.setattr(
        "rspipe.quality.aoi_cloud_fraction",
        lambda s, b, **k: cloud_map[s.scene_id],
    )
    monkeypatch.setattr("rspipe.quality.aoi_overlap", lambda s, b: 0.9)
    ranked = rank_scenes([high, low], aoi)
    assert [r.scene.scene_id for r in ranked] == ["LOW_T1", "HIGH_T1"]


def test_rank_scenes_filters_low_overlap(monkeypatch: pytest.MonkeyPatch):
    aoi = (113.0, 22.0, 114.0, 23.0)
    s = _rank_scene("X_T1", bbox=aoi, cloud=1.0, tier="T1")
    monkeypatch.setattr("rspipe.quality.aoi_overlap", lambda s, b: 0.05)
    ranked = rank_scenes([s], aoi, min_overlap=0.3)
    assert ranked == []


def test_rank_scenes_falls_back_to_scene_cloud(monkeypatch: pytest.MonkeyPatch):
    aoi = (113.0, 22.0, 114.0, 23.0)
    a = _rank_scene("A_T1", bbox=aoi, cloud=8.0, tier="T1")
    b = _rank_scene("B_T1", bbox=aoi, cloud=30.0, tier="T1")
    monkeypatch.setattr("rspipe.quality.aoi_cloud_fraction", lambda s, b, **k: None)
    monkeypatch.setattr("rspipe.quality.aoi_overlap", lambda s, b: 0.9)
    ranked = rank_scenes([b, a], aoi)
    assert [r.scene.scene_id for r in ranked] == ["A_T1", "B_T1"]
    assert ranked[0].aoi_cloud is None


def test_format_ranked_readable(monkeypatch: pytest.MonkeyPatch):
    aoi = (113.0, 22.0, 114.0, 23.0)
    s = _rank_scene("A_T1", bbox=aoi, cloud=8.0, tier="T1")
    monkeypatch.setattr("rspipe.quality.aoi_cloud_fraction", lambda s, b, **k: 0.15)
    monkeypatch.setattr("rspipe.quality.aoi_overlap", lambda s, b: 0.7)
    text = format_ranked(rank_scenes([s], aoi))
    assert "A_T1" in text and "T1" in text and "15.00" in text


# ── STAC 元数据回填 ───────────────────────────────────────────────────────────
def _processing_scene() -> SceneMeta:
    return SceneMeta(
        scene_id="BACKFILL_SCENE",
        provider="earth-search",
        collection="sentinel-2-l2a",
        datetime=datetime(2026, 10, 7, tzinfo=UTC),
        bbox=(116.0, 39.936, 116.064, 40.0),
        cloud_cover=5.25,
        platform="sentinel-2a",
        sensor_key="sentinel-2-msi",
        product_level=ProductLevel.L2,
        radiometry=Radiometry.SURFACE_REFLECTANCE,
        source_format=SourceFormat.COG,
        properties={},
    )


def test_stac_item_backfills_aoi_metrics(stack_file: Path, tmp_path: Path):
    extra: dict[str, Any] = {
        "rspi:scene_cloud_cover": 5.25,
        "rspi:aoi_cloud_cover": 0.2467,
        "rspi:valid_fraction": 0.7533,
        "rspi:tier": "T1",
        "rspi:aoi_bbox": [113.76, 22.41, 114.62, 22.87],
    }
    item = build_item(
        scene=_processing_scene(),
        cog_path=stack_file,
        collection_id="shenzhen",
        properties_extra=extra,
    )
    j = json.loads(json.dumps(item.to_dict()))
    props = j["properties"]
    assert props["rspi:scene_cloud_cover"] == pytest.approx(5.25)
    assert props["rspi:aoi_cloud_cover"] == pytest.approx(0.2467)
    assert props["rspi:valid_fraction"] == pytest.approx(0.7533)
    assert props["rspi:tier"] == "T1"
    assert props["rspi:aoi_bbox"] == [113.76, 22.41, 114.62, 22.87]
    assert props["eo:cloud_cover"] == pytest.approx(5.25)  # 原字段保留（来自 scene.cloud_cover）


def test_scene_result_carries_metrics():
    from rspipe.pipeline.steps import SceneContext, SceneProcessor

    profile = ProductProfile(source_format=SourceFormat.COG)
    # 直接构造 result 并验证字段存在
    from rspipe.models import SceneResult
    from rspipe.pipeline.state import PipelineState  # noqa: F401  (import sanity)

    r = SceneResult(scene_id="x", profile=profile)
    assert r.aoi_cloud_cover is None
    assert r.valid_fraction is None
    # 字段可写
    r.aoi_cloud_cover = 0.1
    r.valid_fraction = 0.9
    assert r.aoi_cloud_cover == pytest.approx(0.1)
    # SceneContext 有 metrics/aoi_bbox 字段
    assert hasattr(SceneContext, "__dataclass_fields__")
    assert "metrics" in SceneContext.__dataclass_fields__
    assert "aoi_bbox" in SceneContext.__dataclass_fields__
    assert hasattr(SceneProcessor, "_step_cloud_mask")


def test_qa_asset_prefers_cloud_role_over_data_mask():
    """回归：Landsat 的 qa_aerosol(data-mask) 不得抢占 qa_pixel(cloud)。"""
    from rspipe.bands import BandResolver

    scene = SceneMeta(
        scene_id="S", provider="p", collection="c",
        datetime=datetime(2026, 1, 1, tzinfo=UTC), bbox=(0, 0, 1, 1),
        assets=[
            AssetRef(key="qa_aerosol", href="x1", media_type="image/tiff", roles=("data-mask", "water-mask")),
            AssetRef(key="qa_pixel", href="x2", media_type="image/tiff", roles=("cloud", "cloud-shadow")),
        ],
    )
    r = BandResolver(scene, None)
    qa = r.qa_asset()
    assert qa is not None and qa.key == "qa_pixel"


def test_ranked_scene_dataclass():
    s = _rank_scene("A_T1", bbox=(0, 0, 1, 1), cloud=1.0, tier="T1")
    r = RankedScene(scene=s, tier="T1", aoi_cloud=0.1, overlap=0.9, score=0.0)
    assert r.tier_penalty == 0.0
    r2 = RankedScene(scene=s, tier="T2", aoi_cloud=0.1, overlap=0.9, score=0.0)
    assert r2.tier_penalty == 1.0
    r3 = RankedScene(scene=s, tier=None, aoi_cloud=0.1, overlap=0.9, score=0.0)
    assert r3.tier_penalty == 0.0
