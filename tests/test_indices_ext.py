"""新增指数的数学与选择逻辑：SAR/热红外缩放、TVDI 干湿边、VCI/距平、历史年份、景选择。"""

from __future__ import annotations

from datetime import date, datetime
from types import SimpleNamespace

import numpy as np
import pytest

from rspipe.web import analysis, timeseries


def test_collection_from_scene_id():
    assert analysis.collection_from_scene_id("S1A_IW_GRDH_1SDV_20240730_rtc") == "sentinel-1-rtc"
    assert analysis.collection_from_scene_id("LC09_L2SP_123032_20240705_02_T1") == "landsat-c2-l2"
    assert analysis.collection_from_scene_id("S2C_52TES_20261006_0_L2A") == "sentinel-2-l2a"


def test_scale_band_thermal_zero_is_nodata():
    dn = np.array([0, 20000, 43000], dtype="float32")
    out = analysis._scale_band("lwir11", dn, "landsat-c2-l2")
    assert np.isnan(out[0])
    assert out[1] == pytest.approx(20000 * 0.00341802 + 149.0, rel=1e-6)  # ≈ 217.36 K
    assert 280 < out[2] - 273.15 < 330 or out[2] > 273


def test_scale_band_sar_is_linear_untouched():
    dn = np.array([0.0, 0.05, 0.2], dtype="float32")
    out = analysis._scale_band("vv", dn, "sentinel-1-rtc")
    assert np.isnan(out[0])
    assert out[1] == pytest.approx(0.05)
    assert out[2] == pytest.approx(0.2)


def test_scale_band_landsat_and_s2_reflectance():
    ls = analysis._scale_band("red", np.array([10000.0], dtype="float32"), "landsat-c2-l2")
    assert ls[0] == pytest.approx(10000 * 2.75e-05 - 0.2, rel=1e-5)
    s2 = analysis._scale_band("red", np.array([2500.0], dtype="float32"), "sentinel-2-l2a")
    assert s2[0] == pytest.approx(0.25)


def test_tvdi_recovers_dryness_on_synthetic_triangle():
    rng = np.random.default_rng(0)
    n = 40000
    ndvi = rng.uniform(0.1, 0.8, n)
    d = rng.uniform(0.0, 1.0, n)  # 0 湿润 → 1 干旱
    base = 320.0 - 30.0 * ndvi
    lst = base - 4.0 + 10.0 * d  # 湿边 base-4，干边 base+6
    tvdi, params = analysis.tvdi_array(ndvi.reshape(200, 200), lst.reshape(200, 200))
    assert params is not None and params["bins_used"] >= 4
    t = tvdi.ravel()
    mid = (d > 0.2) & (d < 0.8)
    assert np.nanmax(np.abs(t[mid] - d[mid])) < 0.15
    assert np.all((t[np.isfinite(t)] >= 0) & (t[np.isfinite(t)] <= 1))


def test_tvdi_insufficient_pixels_returns_none():
    ndvi = np.full((5, 5), 0.5, dtype="float32")
    lst = np.full((5, 5), 300.0, dtype="float32")
    arr, params = analysis.tvdi_array(ndvi, lst)
    assert params is None and np.all(np.isnan(arr))


def test_otsu_splits_bimodal_distribution():
    rng = np.random.default_rng(1)
    water = rng.normal(-14.0, 1.0, 60000)
    land = rng.normal(-7.0, 1.5, 40000)
    thr = analysis.otsu_threshold(np.concatenate([water, land]))
    assert thr is not None and -12.5 < thr < -9.0
    frac = float((np.concatenate([water, land]) < thr).mean())
    assert 0.55 < frac < 0.65


def test_otsu_rejects_tiny_or_flat_samples():
    assert analysis.otsu_threshold(np.linspace(-20, -5, 100)) is None
    assert analysis.otsu_threshold(np.full(50000, -12.0)) is None


def test_vci_and_anomaly_known_values():
    hist = np.array([[[0.2]], [[0.4]], [[0.6]]], dtype="float32")  # 3 年，1 像元
    tgt = np.array([[0.5]], dtype="float32")
    vci, anom = timeseries.vci_anomaly(hist, tgt)
    assert vci[0, 0] == pytest.approx(75.0, abs=1e-3)  # (0.5-0.2)/(0.6-0.2)*100
    assert anom[0, 0] == pytest.approx(0.1, abs=1e-6)  # 0.5 - mean(0.4)


def test_vci_masks_non_vegetation():
    hist = np.array([[[0.2]], [[0.4]], [[0.6]]], dtype="float32")
    vci, anom = timeseries.vci_anomaly(hist, np.array([[0.02]], dtype="float32"))
    assert np.isnan(vci[0, 0]) and np.isnan(anom[0, 0])


def test_vci_requires_min_history():
    hist = np.array([[[0.2]], [[np.nan]], [[0.6]]], dtype="float32")
    vci, anom = timeseries.vci_anomaly(hist, np.array([[0.5]], dtype="float32"))
    assert np.isnan(vci[0, 0]) and np.isnan(anom[0, 0])


def test_history_years_window():
    assert timeseries.history_years(2024, 8, 2017) == list(range(2017, 2024))
    assert timeseries.history_years(2024, 3, 1985) == [2021, 2022, 2023]
    assert timeseries.history_years(2018, 8, 2017) == [2017]


def test_same_season_handles_leap_day():
    start, end = timeseries.same_season(date(2024, 2, 29), 2023)
    # 2023 非闰年，2/29 退到 2/28，前后各 16 天
    assert start == date(2023, 2, 12) and end == date(2023, 3, 16)


def test_pick_scene_prefers_full_cover_then_cloud():
    aoi = (116.0, 39.0, 116.1, 39.1)

    def sc(sid, bbox, cloud):
        return SimpleNamespace(
            scene_id=sid,
            bbox=bbox,
            cloud_cover=cloud,
            datetime=datetime(2021, 7, 10, 3, 0),
            collection="sentinel-2-l2a",
        )

    partial = sc("partial", (116.05, 39.0, 116.2, 39.1), 1.0)  # 覆盖约一半
    full_cloudy = sc("full_cloudy", (115.9, 38.9, 116.2, 39.2), 25.0)
    full_clear = sc("full_clear", (115.9, 38.9, 116.2, 39.2), 3.0)

    class Client:
        def search(self, **kwargs):
            assert kwargs["collections"] == ["sentinel-2-l2a"]
            return [partial, full_cloudy, full_clear]

    best = timeseries.pick_scene(Client(), "sentinel-2-l2a", aoi, date(2024, 7, 10), 2021)
    assert best.scene_id == "full_clear"
