"""合成栅格工具：单测 / 冒烟用，无需网络与凭证。"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

import numpy as np
import rasterio
from rasterio.transform import from_origin


def make_single_band(
    path: Path,
    *,
    data: np.ndarray | None = None,
    width: int = 64,
    height: int = 64,
    crs: str = "EPSG:4326",
    origin: tuple[float, float] = (116.0, 40.0),
    res: float = 0.001,
    dtype: str = "uint16",
    nodata: float | None = 0.0,
    scale: float = 1.0,
    offset: float = 0.0,
    seed: int = 0,
) -> Path:
    """写出一个单波段 GeoTIFF，默认内容是可复现的随机反射率 DN。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    if data is None:
        rng = np.random.default_rng(seed)
        base = rng.integers(2000, 12000, size=(height, width)).astype(dtype)
        data = base
    transform = from_origin(origin[0], origin[1], res, res)
    profile = {
        "driver": "GTiff",
        "height": int(data.shape[0]),
        "width": int(data.shape[1]),
        "count": 1,
        "dtype": str(data.dtype),
        "crs": crs,
        "transform": transform,
        "nodata": nodata,
        "compress": "deflate",
        "tiled": True,
        "blockxsize": 32,
        "blockysize": 32,
    }
    with rasterio.open(path, "w", **profile) as dst:
        dst.write(data, 1)
        if scale != 1.0 or offset != 0.0:
            dst.update_tags(scale=str(scale), offset=str(offset))
    return path


def make_multiband(
    path: Path,
    *,
    count: int = 4,
    width: int = 64,
    height: int = 64,
    crs: str = "EPSG:4326",
    origin: tuple[float, float] = (116.0, 40.0),
    res: float = 0.001,
    dtype: str = "float32",
    nodata: float | None = float("nan"),
    band_names: Sequence[str] | None = None,
    seed: int = 0,
    fill: float = 0.15,
) -> Path:
    """写出一个多波段 GeoTIFF，值为 fill ± 少量噪声。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(seed)
    arr = (fill + rng.normal(0, 0.02, size=(count, height, width))).astype(dtype)
    transform = from_origin(origin[0], origin[1], res, res)
    profile = {
        "driver": "GTiff",
        "height": height,
        "width": width,
        "count": count,
        "dtype": dtype,
        "crs": crs,
        "transform": transform,
        "nodata": nodata,
        "compress": "deflate",
        "tiled": True,
        "blockxsize": 32,
        "blockysize": 32,
    }
    with rasterio.open(path, "w", **profile) as dst:
        dst.write(arr)
        if band_names:
            for i, name in enumerate(band_names, start=1):
                dst.set_band_description(i, name)
    return path


def make_qa_pixel(
    path: Path,
    *,
    width: int = 64,
    height: int = 64,
    crs: str = "EPSG:4326",
    origin: tuple[float, float] = (116.0, 40.0),
    res: float = 0.001,
    cloud_fraction: float = 0.15,
    seed: int = 0,
) -> Path:
    """写出 Landsat QA_PIXEL 风格掩膜：bit3=cloud, bit4=shadow, bit5=snow, bit6=water。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(seed)
    qa = np.zeros((height, width), dtype="uint16")
    n = height * width
    flat = qa.reshape(-1)
    idx = rng.choice(n, size=int(n * cloud_fraction), replace=False)
    flat[idx] |= 1 << 3  # cloud
    idx2 = rng.choice(n, size=int(n * 0.05), replace=False)
    flat[idx2] |= 1 << 4  # shadow
    qa = flat.reshape(height, width)
    transform = from_origin(origin[0], origin[1], res, res)
    profile = {
        "driver": "GTiff",
        "height": height,
        "width": width,
        "count": 1,
        "dtype": "uint16",
        "crs": crs,
        "transform": transform,
        "nodata": None,
        "compress": "deflate",
        "tiled": True,
        "blockxsize": 32,
        "blockysize": 32,
    }
    with rasterio.open(path, "w", **profile) as dst:
        dst.write(qa, 1)
    return path


def make_scl(
    path: Path,
    *,
    width: int = 64,
    height: int = 64,
    crs: str = "EPSG:32650",
    origin: tuple[float, float] = (500000.0, 4500000.0),
    res: float = 10.0,
    cloud_fraction: float = 0.15,
    seed: int = 0,
) -> Path:
    """写出 Sentinel-2 SCL 风格分类层。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(seed)
    scl = np.full((height, width), 4, dtype="uint8")  # vegetation
    n = height * width
    flat = scl.reshape(-1)
    for value, frac in ((8, cloud_fraction / 2), (9, cloud_fraction / 2), (3, 0.05), (10, 0.03)):
        k = int(n * frac)
        if k:
            flat[rng.choice(n, size=k, replace=False)] = value
    scl = flat.reshape(height, width)
    transform = from_origin(origin[0], origin[1], res, res)
    profile = {
        "driver": "GTiff",
        "height": height,
        "width": width,
        "count": 1,
        "dtype": "uint8",
        "crs": crs,
        "transform": transform,
        "nodata": None,
        "compress": "deflate",
        "tiled": True,
        "blockxsize": 32,
        "blockysize": 32,
    }
    with rasterio.open(path, "w", **profile) as dst:
        dst.write(scl, 1)
    return path


def make_mtl(
    path: Path,
    *,
    bands: Sequence[int] = (2, 3, 4, 5),
    mult: float = 2.75e-05,
    add: float = -0.2,
) -> Path:
    """写出精简版 Landsat MTL.txt。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = ["GROUP = LEVEL1_PROCESSING_RECORD"]
    for b in bands:
        lines.append(f"  REFLECTANCE_MULT_BAND_{b} = {mult}")
        lines.append(f"  REFLECTANCE_ADD_BAND_{b} = {add}")
    lines.append("END_GROUP = LEVEL1_PROCESSING_RECORD")
    lines.append("END")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path
