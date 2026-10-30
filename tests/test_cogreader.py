"""COG 窗口读取器与产品画像单测。"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pytest

from rspipe.errors import DownloadError
from rspipe.ingest.cogreader import CogReader, TiffInfo
from rspipe.models import ProductLevel, ProductProfile, Radiometry, SceneMeta, SourceFormat
from rspipe.normalize.profile import (
    plan_steps,
    plan_summary,
    profile_from_scene,
)
from synthetic import make_single_band


# ── 产品画像与步骤规划 ────────────────────────────────────────────────────────
def _profile(**kw) -> ProductProfile:
    base = {
        "source_format": SourceFormat.COG,
        "product_level": ProductLevel.L2,
        "radiometry": Radiometry.SURFACE_REFLECTANCE,
    }
    base.update(kw)
    return ProductProfile(**base)


def test_profile_flags_cog_source():
    p = _profile()
    assert p.needs_unpack is False
    assert p.needs_radiometric_normalize is False
    assert p.needs_cog_convert is False
    assert p.needs_harmonize is True


def test_profile_flags_safe_dn():
    p = _profile(
        source_format=SourceFormat.SAFE,
        product_level=ProductLevel.L1,
        radiometry=Radiometry.DN,
    )
    assert p.needs_unpack is True
    assert p.needs_radiometric_normalize is True
    assert p.needs_cog_convert is True


def test_plan_steps_includes_cog_convert_for_cog_source():
    plans = plan_steps(_profile())
    names = [p.name for p in plans]
    # COG 源也要产出规范化 COG
    assert "cog_convert" in names
    assert "harmonize" in names
    assert "unpack" not in names
    assert "radiometric_normalize" not in names


def test_plan_steps_dn_safe_needs_full_chain():
    plans = plan_steps(
        _profile(
            source_format=SourceFormat.TAR,
            radiometry=Radiometry.DN,
            product_level=ProductLevel.L1,
        )
    )
    names = [p.name for p in plans]
    assert "unpack" in names
    assert "radiometric_normalize" in names
    assert "cog_convert" in names
    assert "harmonize" not in names


def test_plan_steps_optional_flags():
    plans = plan_steps(_profile(), want_reproject=True, want_mosaic=True)
    names = [p.name for p in plans]
    assert "reproject" in names
    assert "mosaic" in names
    assert "cloud_mask" in names
    assert "stac_publish" in names


def test_plan_summary_is_readable():
    plans = plan_steps(_profile())
    s = plan_summary(plans)
    assert "->" in s
    assert "stac_publish" in s


def test_profile_from_scene_reads_assets():
    scene = SceneMeta(
        scene_id="x",
        provider="p",
        collection="landsat-c2l2-sr",
        datetime=datetime(2026, 1, 1, tzinfo=UTC),
        bbox=(0, 0, 1, 1),
        source_format=SourceFormat.COG,
        product_level=ProductLevel.L2,
        radiometry=Radiometry.SURFACE_REFLECTANCE,
    )
    p = profile_from_scene(scene)
    assert p.source_format is SourceFormat.COG
    assert p.has_qa is False


# ── COG 读取器 ────────────────────────────────────────────────────────────────
def test_cogreader_rejects_non_tiff(tmp_path: Path):
    bad = tmp_path / "bad.tif"
    bad.write_bytes(b"<html>not a tiff</html>")
    with pytest.raises(DownloadError):
        CogReader(str(bad))


def test_cogreader_reads_local_tiff(tmp_path: Path):
    """用本地单波段 tiled GeoTIFF 验证解析与窗口读取（真实源为单波段 COG）。"""
    from rspipe.ingest.cogreader import CogReader

    path = make_single_band(tmp_path / "one.tif", width=64, height=64, seed=11)
    with CogReader(str(path)) as r:
        info = r.info
        assert info.width == 64
        assert info.height == 64
        assert info.tile_width > 0
        arr = r.read_window(0, 0, 16, 16)
        assert arr.shape == (16, 16)
        assert arr.dtype == info.dtype
        # 窗口越界自动裁剪
        assert r.read_window(60, 60, 100, 100).shape == (4, 4)
        assert r.read_window(0, 0, 0, 0).shape == (0, 0)


def test_window_for_bbox_clamps_to_raster():
    info = TiffInfo(
        byte_order="<",
        width=100,
        height=100,
        tile_width=256,
        tile_height=256,
        bits_per_sample=8,
        sample_format=1,
        samples_per_pixel=1,
        compression=1,
        predictor=1,
        planar_config=1,
        transform=(10.0, 0.0, 0.0, 0.0, -10.0, 1000.0),
    )
    reader = CogReader.__new__(CogReader)
    reader.info = info
    reader._tile_cache = {}
    col, row, w, h = reader.window_for_bbox((-1000, -1000, 2000, 2000))
    assert col == 0 and row == 0
    assert w == 100 and h == 100


def test_undo_predictor2_roundtrip():
    from rspipe.ingest.cogreader import _undo_predictor2

    # 构造：真值 -> 差分
    true = np.arange(16, dtype="uint16").reshape(4, 4)
    diff = np.diff(true, axis=1, prepend=np.zeros((4, 1), dtype='uint16'))
    restored = _undo_predictor2(diff)
    assert np.array_equal(restored, true)
