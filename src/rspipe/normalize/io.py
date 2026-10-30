"""栅格堆叠读写：多波段资产 -> 单一多波段 GeoTIFF。"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import rasterio
from rasterio.transform import Affine
from rasterio.warp import Resampling, calculate_default_transform, reproject

from rspipe.errors import NormalizeError
from rspipe.logging import get_logger

log = get_logger("rspipe.normalize.io")


@dataclass(slots=True)
class GridSpec:
    """目标网格：CRS / 分辨率 / 尺寸 / 变换。"""

    crs: Any
    transform: Affine
    width: int
    height: int
    nodata: float | None = None


def open_any(path: str | Path, *, env: dict[str, Any] | None = None) -> Any:
    """打开本地或 /vsi 远程栅格。"""
    return rasterio.open(str(path))


def read_band(path: str | Path, band: int = 1) -> tuple[np.ndarray, dict[str, Any]]:
    """读单波段，返回 (数组, profile)。"""
    try:
        with rasterio.open(str(path)) as src:
            arr = src.read(band)
            profile = src.profile.copy()
            return arr, dict(profile)
    except rasterio.errors.RasterioError as exc:
        raise NormalizeError(f"cannot read {path}: {exc}") from exc


def stack_bands(
    paths: Sequence[str | Path],
    out_path: Path,
    *,
    nodata: float | None = None,
    dtype: str = "float32",
    window: Any | None = None,
) -> Path:
    """把 N 个单波段栅格堆叠成一个多波段 GeoTIFF。

    所有输入必须共享同一 CRS / transform / 尺寸；否则请先 `reproject_to_grid`。
    """
    if not paths:
        raise NormalizeError("stack_bands: empty input list")

    out_path.parent.mkdir(parents=True, exist_ok=True)

    profiles: list[dict[str, Any]] = []
    arrays: list[np.ndarray] = []
    for p in paths:
        arr, prof = read_band(p)
        if window is not None:
            arr = arr[
                int(window.row_off) : int(window.row_off) + int(window.height),
                int(window.col_off) : int(window.col_off) + int(window.width),
            ]
        arrays.append(arr)
        profiles.append(prof)

    ref = profiles[0]
    for i, prof in enumerate(profiles[1:], start=1):
        if prof["height"] != ref["height"] or prof["width"] != ref["width"]:
            raise NormalizeError(
                f"stack_bands: band {i} shape {(prof['height'], prof['width'])} "
                f"!= {(ref['height'], ref['width'])}"
            )
        if prof["transform"] != ref["transform"]:
            raise NormalizeError(f"stack_bands: band {i} transform mismatch")

    stack = np.stack(arrays, axis=0)
    profile = {
        "driver": "GTiff",
        "height": int(ref["height"]),
        "width": int(ref["width"]),
        "count": len(arrays),
        "dtype": dtype,
        "crs": ref.get("crs"),
        "transform": ref.get("transform"),
        "nodata": nodata,
        "compress": "deflate",
        "tiled": True,
        "blockxsize": 512,
        "blockysize": 512,
    }
    with rasterio.open(out_path, "w", **profile) as dst:
        dst.write(stack.astype(dtype))
        dst.update_tags(BAND_COUNT=str(len(arrays)))
    log.info("stack.done", out=str(out_path), bands=len(arrays))
    return out_path


def reproject_to_grid(
    src_path: str | Path,
    dst_path: Path,
    *,
    crs: Any,
    resolution: float | None = None,
    resampling: Resampling = Resampling.bilinear,
    nodata: float | None = None,
    dtype: str = "float32",
) -> Path:
    """把源栅格重投影到目标 CRS / 分辨率。"""
    dst_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with rasterio.open(str(src_path)) as src:
            res = resolution or max(abs(src.transform.a), abs(src.transform.e))
            transform, width, height = calculate_default_transform(
                src.crs,
                crs,
                src.width,
                src.height,
                *src.bounds,
                resolution=res,
            )
            profile = src.profile.copy()
            profile.update(
                crs=crs,
                transform=transform,
                width=width,
                height=height,
                dtype=dtype,
                nodata=nodata,
                driver="GTiff",
                compress="deflate",
                tiled=True,
            )
            with rasterio.open(dst_path, "w", **profile) as dst:
                for i in range(1, src.count + 1):
                    reproject(
                        source=rasterio.band(src, i),
                        destination=rasterio.band(dst, i),
                        src_transform=src.transform,
                        src_crs=src.crs,
                        dst_transform=transform,
                        dst_crs=crs,
                        resampling=resampling,
                        src_nodata=src.nodata,
                        dst_nodata=nodata,
                    )
    except rasterio.errors.RasterioError as exc:
        raise NormalizeError(f"reproject failed for {src_path}: {exc}") from exc

    log.info("reproject.done", out=str(dst_path), crs=str(crs), res=resolution)
    return dst_path


def write_array(
    arr: np.ndarray,
    out_path: Path,
    *,
    transform: Affine,
    crs: Any,
    nodata: float | None = None,
    dtype: str | None = None,
    band_names: Sequence[str] | None = None,
    compress: str = "deflate",
) -> Path:
    """把 (bands, H, W) 或 (H, W) 数组写为 GeoTIFF。"""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if arr.ndim == 2:
        arr = arr[np.newaxis, ...]
    if arr.ndim != 3:
        raise NormalizeError(f"write_array: expected 2D or 3D array, got shape {arr.shape}")

    dt = dtype or str(arr.dtype)
    profile = {
        "driver": "GTiff",
        "height": int(arr.shape[1]),
        "width": int(arr.shape[2]),
        "count": int(arr.shape[0]),
        "dtype": dt,
        "crs": crs,
        "transform": transform,
        "nodata": nodata,
        "compress": compress,
        "tiled": True,
        "blockxsize": 512,
        "blockysize": 512,
    }
    with rasterio.open(out_path, "w", **profile) as dst:
        dst.write(arr.astype(dt))
        if band_names:
            for i, name in enumerate(band_names, start=1):
                dst.set_band_description(i, name)
    return out_path


def read_stack(path: str | Path) -> tuple[np.ndarray, dict[str, Any]]:
    """读取多波段为 (bands, H, W) 数组 + profile。"""
    try:
        with rasterio.open(str(path)) as src:
            return src.read(), dict(src.profile)
    except rasterio.errors.RasterioError as exc:
        raise NormalizeError(f"cannot read {path}: {exc}") from exc


__all__ = [
    "GridSpec",
    "open_any",
    "read_band",
    "read_stack",
    "reproject_to_grid",
    "stack_bands",
    "write_array",
]
