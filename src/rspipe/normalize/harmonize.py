"""波段与单位统一（源已是 COG 时的轻量归一化）。

- 统一为 float32 反射率（0..1）
- 按请求的通用波段名重排
- 写入标准波段描述
- 处理 nodata / 值域裁剪
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

import numpy as np

from rspipe.errors import NormalizeError
from rspipe.logging import get_logger
from rspipe.models import Radiometry, SensorSpec
from rspipe.normalize.io import read_stack, write_array
from rspipe.normalize.reflectance import ScalingSet

log = get_logger("rspipe.normalize.harmonize")

# 常见整数反射率定标（若未提供 ScalingSet）
_INT_REFLECTANCE_SCALE = {
    "uint8": 1.0 / 255.0,
    "uint16": 1.0 / 10000.0,
    "int16": 1.0 / 10000.0,
}


def harmonize(
    src_path: str | Path,
    dst_path: Path,
    *,
    band_names: Sequence[str],
    scaling: ScalingSet | None = None,
    spec: SensorSpec | None = None,
    radiometry: Radiometry = Radiometry.SURFACE_REFLECTANCE,
    clip: tuple[float, float] = (0.0, 1.0),
    band_order: Sequence[str] | None = None,
) -> Path:
    """把源栅格统一为 float32 反射率多波段 GeoTIFF。

    `band_names` 是输出波段描述（顺序与源波段一致）。
    `band_order` 指定输出重排（用 `band_names` 的值），省略则保持原序。
    """
    arr, profile = read_stack(src_path)
    if arr.ndim != 3:
        raise NormalizeError(f"harmonize: expected multi-band raster, got {arr.shape}")

    if len(band_names) != arr.shape[0]:
        raise NormalizeError(
            f"harmonize: band_names={len(band_names)} != raster bands={arr.shape[0]}"
        )

    # 重排
    if band_order:
        index = []
        for name in band_order:
            if name not in band_names:
                raise NormalizeError(f"harmonize: band {name!r} not in {list(band_names)}")
            index.append(band_names.index(name))
        arr = arr[index, ...]
        band_names = [band_names[i] for i in index]

    out = np.empty(arr.shape, dtype="float32")
    nodata_in = profile.get("nodata")

    for i, name in enumerate(band_names):
        band = arr[i].astype("float32")
        # 应用定标
        if scaling is not None:
            sc = scaling.for_band(name)
            band = band * sc.scale + sc.offset
        elif arr.dtype != np.float32 and arr.dtype != np.float64:
            band = band * _INT_REFLECTANCE_SCALE.get(str(arr.dtype), 1.0)

        # nodata -> nan
        if nodata_in is not None:
            band = np.where(arr[i] == nodata_in, np.nan, band)
        # 0 常为填充
        if radiometry is not Radiometry.OTHER:
            band = np.where(arr[i] == 0, np.nan, band)

        # 值域裁剪
        if clip is not None:
            lo, hi = clip
            if spec is not None:
                lo, hi = spec.reflectance_range
            band = np.clip(band, lo, hi)

        out[i] = band

    transform = profile.get("transform")
    crs = profile.get("crs")
    if transform is None or crs is None:
        raise NormalizeError("harmonize: source missing transform/crs")

    write_array(
        out,
        dst_path,
        transform=transform,
        crs=crs,
        nodata=float("nan"),
        dtype="float32",
        band_names=band_names,
    )
    log.info("harmonize.done", out=str(dst_path), bands=len(band_names), dtype="float32")
    return dst_path


def normalize_dn(
    src_path: str | Path,
    dst_path: Path,
    *,
    band_names: Sequence[str],
    scaling: ScalingSet,
    clip: tuple[float, float] | None = None,
    band_order: Sequence[str] | None = None,
) -> Path:
    """DN -> 反射率 / 辐亮度的辐射归一化。

    与 `harmonize` 的区别：这里是必做定标（DN 源），不做整数缩放兜底。
    """
    arr, profile = read_stack(src_path)
    if arr.ndim != 3:
        raise NormalizeError(f"normalize_dn: expected multi-band, got {arr.shape}")
    if len(band_names) != arr.shape[0]:
        raise NormalizeError(
            f"normalize_dn: band_names={len(band_names)} != raster bands={arr.shape[0]}"
        )

    if band_order:
        index = [band_names.index(n) for n in band_order]
        arr = arr[index, ...]
        band_names = [band_names[i] for i in index]

    nodata_in = profile.get("nodata")
    out = np.empty(arr.shape, dtype="float32")
    for i, name in enumerate(band_names):
        band = arr[i].astype("float32")
        sc = scaling.for_band(name)
        band = band * sc.scale + sc.offset
        if nodata_in is not None:
            band = np.where(arr[i] == nodata_in, np.nan, band)
        if clip is not None:
            band = np.clip(band, clip[0], clip[1])
        out[i] = band

    write_array(
        out,
        dst_path,
        transform=profile["transform"],
        crs=profile["crs"],
        nodata=float("nan"),
        dtype="float32",
        band_names=band_names,
    )
    log.info("normalize_dn.done", out=str(dst_path), bands=len(band_names))
    return dst_path


__all__ = ["harmonize", "normalize_dn"]
