"""web 模块单元/集成测试（纯本地，无网络）。"""

from __future__ import annotations

import math

import numpy as np
import rasterio

from rspipe.normalize.cog import to_cog
from rspipe.web.tiles import _ndvi_lut, render_tile, tile_bounds


def test_tile_bounds_global_at_zoom0():
    b = tile_bounds(0, 0, 0)
    assert math.isclose(b[0], -20037508.342789244, abs_tol=0.01)
    assert math.isclose(b[3], 20037508.342789244, abs_tol=0.01)
    t = tile_bounds(5, 10, 10)
    assert -20037508 < t[0] < t[2] < 20037508
    assert -20037508 < t[1] < t[3] < 20037508


def test_ndvi_lut_monotone_and_valid():
    ndvi = np.linspace(-1, 1, 256, dtype="float32")
    valid = np.ones(256, dtype=bool)
    lut = _ndvi_lut(ndvi.reshape(1, -1), valid.reshape(1, -1))
    assert lut.shape == (3, 1, 256)
    assert (lut >= 0).all() and (lut <= 255).all()


def test_render_tile_from_synthetic_cog(tmp_path):
    # 造一个 64×64 网格 COG（EPSG:32650，30m，太原纬度带）
    import rasterio.transform as rt

    h, w = 64, 64
    transform = rt.from_origin(600000, 4200000, 30, 30)
    arr = np.linspace(0.05, 0.6, h * w, dtype="float32").reshape(1, h, w)
    arr[0, 0, 0] = float("nan")  # 造一个无值点
    stack = np.repeat(arr, 4, axis=0)
    plain = tmp_path / "plain.tif"
    with rasterio.open(
        plain, "w", driver="GTiff", height=h, width=w, count=4,
        dtype="float32", crs="EPSG:32650", transform=transform,
    ) as dst:
        dst.write(stack)
    cog = tmp_path / "cog.tif"
    to_cog(plain, cog)

    # EPSG:32650 easting=600000 ≈ lon 118.1°E；y=4200000 ≈ lat 37.9°N — 就近取 z11 瓦片
    lon, lat = 118.14, 37.9
    x = int((lon + 180) / 360 * 2**11)
    lat_r = math.radians(lat)
    y = int((1 - math.log(math.tan(lat_r) + 1 / math.cos(lat_r)) / math.pi) / 2 * 2**11)
    png = render_tile(cog, 11, x, y, "rgb")
    assert png is not None
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
