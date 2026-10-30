"""归一化与 COG 单测。"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import rasterio

from rspipe.models import SensorSpec
from rspipe.normalize.cog import to_cog, validate_cog
from rspipe.normalize.harmonize import harmonize, normalize_dn
from rspipe.normalize.io import read_stack, stack_bands
from rspipe.normalize.reflectance import (
    BandScaling,
    ScalingSet,
    resolve_scaling,
    scaling_from_spec,
)


def test_stack_bands_shapes(band_files: list[Path], tmp_path: Path):
    out = tmp_path / "stack.tif"
    stack_bands(band_files, out)
    arr, profile = read_stack(out)
    assert arr.shape == (4, 64, 64)
    assert profile["count"] == 4


def test_stack_bands_rejects_empty(tmp_path: Path):
    import pytest

    from rspipe.errors import NormalizeError

    with pytest.raises(NormalizeError):
        stack_bands([], tmp_path / "x.tif")


def test_to_cog_produces_valid_layout(stack_file: Path, tmp_path: Path):
    out = tmp_path / "out.tif"
    to_cog(stack_file, out, band_names=["blue", "green", "red", "nir"])
    assert out.exists()
    assert validate_cog(out)
    with rasterio.open(str(out)) as src:
        assert src.descriptions == ("blue", "green", "red", "nir")
        assert src.driver == "GTiff"
        assert src.profile.get("tiled") is True


def test_normalize_dn_applies_scaling(tmp_path: Path):
    data = np.full((1, 8, 8), 10000, dtype="uint16")
    src = tmp_path / "dn.tif"
    with rasterio.open(
        src,
        "w",
        driver="GTiff",
        height=8,
        width=8,
        count=1,
        dtype="uint16",
        crs="EPSG:4326",
        transform=rasterio.transform.from_origin(0, 1, 0.1, 0.1),
    ) as ds:
        ds.write(data)

    scaling = ScalingSet({"B2": BandScaling("B2", scale=2.75e-05, offset=-0.2)})
    out = tmp_path / "norm.tif"
    normalize_dn(src, out, band_names=["B2"], scaling=scaling)
    with rasterio.open(str(out)) as ds:
        arr = ds.read(1)
    # 10000 * 2.75e-05 - 0.2 = 0.075
    assert abs(float(arr[0, 0]) - 0.075) < 1e-5


def test_harmonize_float_input_passthrough(tmp_path: Path, stack_file: Path):
    out = tmp_path / "harm.tif"
    harmonize(
        stack_file,
        out,
        band_names=["blue", "green", "red", "nir"],
        scaling=None,
        clip=(0.0, 1.0),
    )
    arr, _ = read_stack(out)
    assert arr.dtype == np.float32
    assert arr.shape[0] == 4
    assert np.nanmin(arr) >= 0.0
    assert np.nanmax(arr) <= 1.0


def test_harmonize_band_reorder(tmp_path: Path, stack_file: Path):
    out = tmp_path / "harm2.tif"
    harmonize(
        stack_file,
        out,
        band_names=["blue", "green", "red", "nir"],
        scaling=None,
        band_order=["nir", "red"],
    )
    with rasterio.open(str(out)) as ds:
        assert ds.descriptions == ("nir", "red")


def test_scaling_set_for_band_loose_match():
    s = ScalingSet({"B4": BandScaling("B4", 2.0, 0.1)})
    assert s.for_band("b4").scale == 2.0
    assert s.for_band("B04").scale == 2.0
    assert s.for_band("4").scale == 2.0


def test_scaling_from_spec():
    spec = SensorSpec(key="x", band_map={}, reflectance_scale=0.5, reflectance_offset=-1.0)
    s = scaling_from_spec(spec, ["red", "nir"])
    assert s.for_band("red").scale == 0.5
    assert s.for_band("nir").offset == -1.0
    assert s.source.startswith("sensor-default")


def test_resolve_scaling_falls_back_to_spec(tmp_path: Path):
    spec = SensorSpec(key="x", band_map={}, reflectance_scale=0.25, reflectance_offset=0.0)
    s = resolve_scaling(spec=spec, bands=["red"], mtl_path=tmp_path / "nope.txt")
    assert s.source.startswith("sensor-default")


def test_resolve_scaling_prefers_mtl(tmp_path: Path):
    mtl = tmp_path / "MTL.txt"
    mtl.write_text(
        "GROUP = L1\n  REFLECTANCE_MULT_BAND_4 = 0.0001\n  REFLECTANCE_ADD_BAND_4 = -0.05\nEND\n",
        encoding="utf-8",
    )
    spec = SensorSpec(key="x", band_map={}, reflectance_scale=9.9, reflectance_offset=9.9)
    s = resolve_scaling(spec=spec, bands=["B4"], mtl_path=mtl)
    assert s.source == str(mtl)
    assert abs(s.for_band("B4").scale - 0.0001) < 1e-12
    assert abs(s.for_band("B4").offset - (-0.05)) < 1e-12


def test_scaling_from_stac_ignores_raster_offset_when_boa_offset_applied():
    """earth-search PB>=04 S2: 资产 raster:bands 带 offset=-0.1，但像素已剔除偏移，必须忽略。"""
    import logging
    from datetime import datetime

    from rspipe.models import AssetRef, SceneMeta, SensorSpec
    from rspipe.normalize.reflectance import scaling_from_stac

    logging.disable(logging.CRITICAL)
    try:
        spec = SensorSpec(key="s2", band_map={}, reflectance_scale=1e-4)
        scene = SceneMeta(
            scene_id="S2C_49SFB_20261011_0_L2A",
            provider="earth-search",
            collection="sentinel-2-l2a",
            datetime=datetime(2026, 10, 11),
            bbox=(112.15, 37.55, 112.90, 38.20),
            properties={"earthsearch:boa_offset_applied": True},
            assets=[
                AssetRef(
                    key="red",
                    href="https://example.com/B04.tif",
                    media_type="image/tiff; application=geotiff; profile=cloud-optimized",
                    roles=("data",),
                    extra={"raster:bands": [{"scale": 0.0001, "offset": -0.1, "nodata": 0}]},
                )
            ],
        )
        s = scaling_from_stac(scene, spec, ["red"])
        assert s is not None and s.source == "stac-raster-bands"
        assert s.by_band["red"].scale == 0.0001
        assert s.by_band["red"].offset == 0.0

        scene.properties = {}
        s2 = scaling_from_stac(scene, spec, ["red"])
        assert s2 is not None
        assert s2.by_band["red"].offset == -0.1  # 无标志时保持资产标注
    finally:
        logging.disable(logging.NOTSET)
