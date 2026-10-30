"""匀色与镶嵌单测。"""

from __future__ import annotations

import numpy as np
import pytest

from rspipe.errors import ColorBalanceError, MosaicError
from rspipe.process.color import (
    balance_collection,
    balance_scene,
    band_stats,
    choose_reference,
    match_histogram,
    match_quantile,
)
from rspipe.process.mosaic import MosaicSource, distance_weight, mosaic


# ── 直方图匹配 ────────────────────────────────────────────────────────────────
def test_match_histogram_shifts_mean_to_reference():
    rng = np.random.default_rng(0)
    ref = rng.normal(0.18, 0.03, (64, 64)).astype("float32")
    src = rng.normal(0.30, 0.08, (64, 64)).astype("float32")
    out = match_histogram(src, ref)
    assert abs(out.mean() - ref.mean()) < 0.01
    # 形状不变、有限值
    assert out.shape == src.shape
    assert np.isfinite(out).all()


def test_match_histogram_identity_when_same():
    rng = np.random.default_rng(1)
    x = rng.normal(0.5, 0.1, (32, 32)).astype("float32")
    out = match_histogram(x, x.copy())
    assert abs(out.mean() - x.mean()) < 1e-3


def test_match_histogram_respects_valid_mask():
    rng = np.random.default_rng(2)
    ref = rng.normal(0.2, 0.05, (16, 16)).astype("float32")
    src = rng.normal(0.6, 0.05, (16, 16)).astype("float32")
    mask = np.zeros((16, 16), dtype=bool)
    mask[:8, :] = True
    out = match_histogram(src, ref, valid_mask=mask)
    assert np.isfinite(out).all()


def test_match_histogram_handles_nan():
    src = np.array([[np.nan, 0.3], [0.4, 0.5]], dtype="float32")
    ref = np.array([[0.1, 0.11], [0.12, 0.13]], dtype="float32")
    out = match_histogram(src, ref)
    assert np.isnan(out[0, 0])
    assert np.isfinite(out[1, 0])


def test_match_quantile_linear():
    rng = np.random.default_rng(3)
    ref = rng.normal(0.2, 0.05, (32, 32)).astype("float32")
    src = rng.normal(0.5, 0.1, (32, 32)).astype("float32")
    out = match_quantile(src, ref)
    s02, _s98 = np.percentile(src, [2, 98])
    r02, r98 = np.percentile(ref, [2, 98])
    # 边界分位数应被映射到位
    assert abs(np.percentile(out, 2) - r02) < 0.01
    assert abs(np.percentile(out, 98) - r98) < 0.01
    assert s02 != r02  # 确实发生了变换


def test_band_stats():
    arr = np.linspace(0, 1, 100).reshape(10, 10).astype("float32")
    st = band_stats(arr, "red")
    assert st.band == "red"
    assert st.valid_pixels == 100
    assert 0 <= st.p02 <= st.p50 <= st.p98 <= 1


def test_band_stats_all_nan():
    arr = np.full((5, 5), np.nan, dtype="float32")
    st = band_stats(arr, "x")
    assert st.valid_pixels == 0
    assert np.isnan(st.mean)


# ── 场景级匀色 ────────────────────────────────────────────────────────────────
def test_balance_scene_shape_validation():
    a = np.zeros((3, 8, 8), dtype="float32")
    b = np.zeros((3, 9, 9), dtype="float32")
    with pytest.raises(ColorBalanceError):
        balance_scene(a, b, band_names=["r", "g", "b"])


def test_balance_scene_none_method_is_noop():
    rng = np.random.default_rng(4)
    a = rng.random((2, 8, 8)).astype("float32")
    ref = rng.random((2, 8, 8)).astype("float32")
    out, rep = balance_scene(a, ref, band_names=["r", "g"], method="none")
    assert np.allclose(out, a)
    assert rep == {}


def test_balance_collection_reference_not_adjusted():
    rng = np.random.default_rng(5)
    arrays = {
        "ref": rng.random((2, 16, 16)).astype("float32"),
        "other": rng.random((2, 16, 16)).astype("float32") + 0.3,
    }
    out, report = balance_collection(arrays, band_names=["r", "g"], reference_id="ref")
    assert np.allclose(out["ref"], arrays["ref"])
    assert report.reference_id == "ref"
    assert "other" in out


def test_choose_reference_modes():
    scenes = [
        {"scene_id": "a", "cloud_cover": 10.0, "valid_fraction": 0.5},
        {"scene_id": "b", "cloud_cover": 2.0, "valid_fraction": 0.4},
        {"scene_id": "c", "cloud_cover": 5.0, "valid_fraction": 0.9},
    ]
    assert choose_reference(scenes, mode="best") == "b"
    assert choose_reference(scenes, mode="first") == "a"
    assert choose_reference(scenes, mode="best", target_id="c") == "c"
    with pytest.raises(ColorBalanceError):
        choose_reference(scenes, mode="best", target_id="zzz")


# ── 镶嵌 ──────────────────────────────────────────────────────────────────────
def _two_sources():
    a = np.full((2, 32, 32), 0.2, dtype="float32")
    b = np.full((2, 32, 32), 0.5, dtype="float32")
    ma = np.zeros((32, 32), dtype=bool)
    ma[:, :20] = True
    mb = np.zeros((32, 32), dtype=bool)
    mb[:, 12:] = True
    return [MosaicSource("a", a, ma, 1.0), MosaicSource("b", b, mb, 0.9)]


def test_distance_weight_edge_to_center():
    mask = np.zeros((21, 21), dtype=bool)
    mask[5:16, 5:16] = True  # 11x11 中心块
    w = distance_weight(mask, blend_distance=4)
    assert w.shape == mask.shape
    # 中心权重高、边缘低
    assert w[10, 10] > w[5, 5]
    # 有效区外为 0
    assert w[0, 0] == 0.0


def test_distance_weight_empty_mask():
    w = distance_weight(np.zeros((4, 4), dtype=bool), blend_distance=2)
    assert (w == 0).all()


def test_mosaic_distance_blends_in_overlap():
    res = mosaic(_two_sources(), feather="distance", blend_distance=8)
    assert res.data.shape == (2, 32, 32)
    # 重叠区 (col 12..20) 值应介于 0.2 与 0.5 之间
    overlap = res.data[0, 16, 15]
    assert 0.2 < overlap < 0.5
    # 独占区取各自值
    assert abs(res.data[0, 16, 2] - 0.2) < 1e-5
    assert abs(res.data[0, 16, 30] - 0.5) < 1e-5


def test_mosaic_first_mode_priority():
    res = mosaic(_two_sources(), feather="first")
    # 先到先得：重叠区用 a 的 0.2
    assert abs(res.data[0, 16, 15] - 0.2) < 1e-5


def test_mosaic_mean_mode():
    res = mosaic(_two_sources(), feather="mean", blend_distance=1)
    overlap = res.data[0, 16, 15]
    assert 0.2 < overlap < 0.5


def test_mosaic_coverage_counts():
    res = mosaic(_two_sources(), feather="distance", blend_distance=4)
    assert res.coverage.max() == 2  # 重叠区 2 景
    assert res.coverage.min() >= 1


def test_mosaic_shape_mismatch_raises():
    a = np.zeros((2, 8, 8), dtype="float32")
    b = np.zeros((2, 9, 9), dtype="float32")
    m = np.ones((8, 8), dtype=bool)
    m2 = np.ones((9, 9), dtype=bool)
    with pytest.raises(MosaicError):
        mosaic([MosaicSource("a", a, m), MosaicSource("b", b, m2)])


def test_mosaic_empty_raises():
    with pytest.raises(MosaicError):
        mosaic([])


def test_mosaic_unknown_feather_raises():
    with pytest.raises(MosaicError):
        mosaic(_two_sources(), feather="bogus")
