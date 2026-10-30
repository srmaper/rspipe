"""云掩膜：Landsat QA_PIXEL 位解码 / Sentinel-2 SCL 类别解码。

输出统一为 bool 掩膜：True = 有效（晴空 / 保留），False = 剔除。
掩膜同时写为 GeoTIFF，作为 STAC `data-mask` asset。
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
from rasterio.features import geometry_mask
from scipy.ndimage import binary_dilation

from rspipe.errors import CloudMaskError
from rspipe.logging import get_logger
from rspipe.models import SensorSpec

log = get_logger("rspipe.process.clouds")


@dataclass(slots=True)
class MaskResult:
    """掩膜结果与统计。"""

    mask: np.ndarray  # bool, True = keep
    cloud_fraction: float
    shadow_fraction: float
    snow_fraction: float
    water_fraction: float
    invalid_fraction: float
    meta: dict[str, Any] = field(default_factory=dict)

    @property
    def clear_fraction(self) -> float:
        return float(self.mask.mean()) if self.mask.size else 0.0


# ── Landsat QA_PIXEL ─────────────────────────────────────────────────────────
# C2 QA_PIXEL 位定义
LS_QA_BITS: dict[str, int] = {
    "fill": 0,
    "dilated_cloud": 1,
    "cirrus": 2,
    "cloud": 3,
    "cloud_shadow": 4,
    "snow": 5,
    "water": 6,
}


def decode_qa_pixel(
    qa: np.ndarray,
    *,
    drop: Iterable[str] = ("fill", "dilated_cloud", "cirrus", "cloud", "cloud_shadow"),
    keep_snow: bool = True,
    keep_water: bool = True,
    bits: dict[str, int] | None = None,
) -> tuple[np.ndarray, dict[str, float]]:
    """Landsat QA_PIXEL -> bool 掩膜 + 各类占比。"""
    bits = bits or LS_QA_BITS
    # 位运算必须整型；float 输入（来自 float32 归一化读取）先转回整型
    qa_arr = np.asarray(qa)
    if qa_arr.dtype.kind == "f":
        qa_arr = np.where(np.isfinite(qa_arr), qa_arr, 0).astype("uint16")
    elif qa_arr.dtype.kind not in "iub":
        qa_arr = qa_arr.astype("uint16")
    qa_arr = qa_arr.astype("uint32", copy=False)
    qa = qa_arr
    drop_set = set(drop)
    if not keep_snow:
        drop_set.add("snow")
    if not keep_water:
        drop_set.add("water")

    invalid = np.zeros(qa.shape, dtype=bool)
    fracs: dict[str, float] = {}
    for name, bit in bits.items():
        if bit >= 16:
            continue
        hit = (qa & (1 << bit)) != 0
        fracs[f"{name}_fraction"] = float(hit.mean())
        if name in drop_set:
            invalid |= hit

    mask = ~invalid
    fracs["cloud_fraction"] = float(((qa & (1 << bits.get("cloud", 3))) != 0).mean())
    fracs["shadow_fraction"] = float(((qa & (1 << bits.get("cloud_shadow", 4))) != 0).mean())
    fracs["snow_fraction"] = float(((qa & (1 << bits.get("snow", 5))) != 0).mean())
    fracs["water_fraction"] = float(((qa & (1 << bits.get("water", 6))) != 0).mean())
    fracs["invalid_fraction"] = float(invalid.mean())
    return mask, fracs


# ── Sentinel-2 SCL ───────────────────────────────────────────────────────────
# SCL 默认保留类别：2 dark, 4 vegetation, 5 not-vegetated, 6 water, 11 snow/ice
SCL_DEFAULT_KEEP: dict[int, bool] = {
    0: False,  # no data
    1: False,  # saturated or defective
    2: True,  # dark area
    3: False,  # cloud shadow
    4: True,  # vegetation
    5: True,  # not vegetated
    6: True,  # water
    7: False,  # unclassified
    8: False,  # cloud medium
    9: False,  # cloud high
    10: False,  # thin cirrus
    11: True,  # snow / ice
}

_SCL_CLOUD = (8, 9, 10)
_SCL_SHADOW = (3,)
_SCL_SNOW = (11,)
_SCL_WATER = (6,)
_SCL_INVALID = (0, 1, 7)


def decode_scl(
    scl: np.ndarray,
    *,
    keep: dict[int, bool] | None = None,
) -> tuple[np.ndarray, dict[str, float]]:
    """SCL -> bool 掩膜 + 各类占比。"""
    arr = np.asarray(scl)
    if arr.dtype.kind == "f":
        arr = np.where(np.isfinite(arr), arr, 0)
    scl = arr.astype("int16")
    keep = keep or SCL_DEFAULT_KEEP

    mask = np.zeros(scl.shape, dtype=bool)
    for value, ok in keep.items():
        mask[scl == value] = ok

    def frac(values: Sequence[int]) -> float:
        return float(np.isin(scl, list(values)).mean())

    fracs = {
        "cloud_fraction": frac(_SCL_CLOUD),
        "shadow_fraction": frac(_SCL_SHADOW),
        "snow_fraction": frac(_SCL_SNOW),
        "water_fraction": frac(_SCL_WATER),
        "invalid_fraction": frac(_SCL_INVALID),
    }
    fracs["clear_fraction"] = float(mask.mean())
    return mask, fracs


# ── 形态学缓冲 ───────────────────────────────────────────────────────────────
def dilate_mask(mask: np.ndarray, radius: int = 3) -> np.ndarray:
    """对掩膜做方形膨胀缓冲（半径 radius 像元）。

    对「保留」掩膜做膨胀会扩大保留区；这里对**剔除区**膨胀后再取反，
    实现云边缘缓冲。
    """
    if radius <= 0:
        return mask
    invalid = ~mask
    structure = np.ones((2 * radius + 1, 2 * radius + 1), dtype=bool)
    invalid = binary_dilation(invalid, structure=structure)
    return ~invalid


# ── 顶层入口 ─────────────────────────────────────────────────────────────────
def build_mask(
    qa: np.ndarray,
    *,
    kind: str = "qa_pixel",
    spec: SensorSpec | None = None,
    buffer_pixels: int = 3,
    keep_snow: bool = True,
    keep_water: bool = True,
    drop: Iterable[str] | None = None,
    keep_scl: dict[int, bool] | None = None,
) -> MaskResult:
    """按 `kind` 生成掩膜。`kind` ∈ {qa_pixel, scl, fmask, none}。"""
    kind = (kind or "qa_pixel").lower()

    if kind in ("none", "off", "disable"):
        mask = np.ones(qa.shape[:2] if qa.ndim >= 2 else qa.shape, dtype=bool)
        return MaskResult(
            mask=mask,
            cloud_fraction=0.0,
            shadow_fraction=0.0,
            snow_fraction=0.0,
            water_fraction=0.0,
            invalid_fraction=0.0,
            meta={"kind": "none"},
        )

    if kind in ("scl", "scene_classification"):
        keep = keep_scl or spec.scl_values if spec is not None and spec.scl_values else keep_scl
        mask, fracs = decode_scl(qa, keep=keep)
    elif kind in ("qa_pixel", "qa", "landsat_qa", "fmask"):
        bits = spec.qa_bits if spec is not None and spec.qa_bits else LS_QA_BITS
        default_drop = ("fill", "dilated_cloud", "cirrus", "cloud", "cloud_shadow")
        mask, fracs = decode_qa_pixel(
            qa,
            drop=drop or default_drop,
            keep_snow=keep_snow,
            keep_water=keep_water,
            bits=bits,
        )
    else:
        raise CloudMaskError(f"unsupported mask kind: {kind!r}")

    if buffer_pixels > 0:
        mask = dilate_mask(mask, buffer_pixels)
        fracs["clear_fraction"] = float(mask.mean())

    return MaskResult(
        mask=mask,
        cloud_fraction=fracs.get("cloud_fraction", 0.0),
        shadow_fraction=fracs.get("shadow_fraction", 0.0),
        snow_fraction=fracs.get("snow_fraction", 0.0),
        water_fraction=fracs.get("water_fraction", 0.0),
        invalid_fraction=fracs.get("invalid_fraction", 0.0),
        meta={"kind": kind, "buffer_pixels": buffer_pixels, **fracs},
    )


def apply_mask(arr: np.ndarray, mask: np.ndarray, *, nodata: float = float("nan")) -> np.ndarray:
    """把掩膜应用到 (bands, H, W) 或 (H, W) 数组，剔除区置 nodata。"""
    out = arr.astype("float32", copy=True)
    if out.ndim == 3:
        out[:, ~mask] = nodata
    else:
        out[~mask] = nodata
    return out


def mask_to_u8(mask: np.ndarray, *, clear: int = 255, invalid: int = 0) -> np.ndarray:
    """bool 掩膜 -> uint8（用于写出 GeoTIFF）。"""
    return np.where(mask, clear, invalid).astype("uint8")


def write_mask(
    mask: np.ndarray,
    out_path: Path,
    *,
    transform: Any,
    crs: Any,
    clear: int = 255,
    invalid: int = 0,
) -> Path:
    """把掩膜写为 uint8 GeoTIFF。"""
    import rasterio

    out_path.parent.mkdir(parents=True, exist_ok=True)
    data = mask_to_u8(mask, clear=clear, invalid=invalid)
    profile = {
        "driver": "GTiff",
        "height": int(mask.shape[0]),
        "width": int(mask.shape[1]),
        "count": 1,
        "dtype": "uint8",
        "crs": crs,
        "transform": transform,
        "nodata": invalid,
        "compress": "deflate",
        "tiled": True,
        "blockxsize": 512,
        "blockysize": 512,
    }
    with rasterio.open(out_path, "w", **profile) as dst:
        dst.write(data, 1)
        dst.update_tags(
            CLEAR=str(clear),
            INVALID=str(invalid),
            DESCRIPTION="cloud/cloud-shadow mask",
        )
    log.info("mask.write", out=str(out_path), clear=float(mask.mean()))
    return out_path


def mask_from_geometry(
    shape: tuple[int, int],
    geometry: dict[str, Any],
    transform: Any,
    *,
    invert: bool = False,
) -> np.ndarray:
    """按几何生成掩膜（AOI 内/外），用于镶嵌时限定输出范围。"""
    return geometry_mask(
        [geometry],
        out_shape=shape,
        transform=transform,
        invert=not invert,
        all_touched=True,
    )


__all__ = [
    "LS_QA_BITS",
    "SCL_DEFAULT_KEEP",
    "MaskResult",
    "apply_mask",
    "build_mask",
    "decode_qa_pixel",
    "decode_scl",
    "dilate_mask",
    "mask_from_geometry",
    "mask_to_u8",
    "write_mask",
]
