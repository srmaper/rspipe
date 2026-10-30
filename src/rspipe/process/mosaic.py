"""无缝镶嵌：距离变换羽化 + 质量加权合成。

- 距离变换权重：像元距自身有效区边缘越远，权重越大，实现柔和过渡
- 云掩膜权重：云量高的景权重降低
- 支持 distance / first / mean 三种融合策略
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

import numpy as np
from scipy.ndimage import distance_transform_edt

from rspipe.errors import MosaicError
from rspipe.logging import get_logger

log = get_logger("rspipe.process.mosaic")

Feather = Literal["distance", "first", "mean"]


@dataclass(slots=True)
class MosaicSource:
    """单景参与镶嵌的输入。"""

    scene_id: str
    data: np.ndarray  # (bands, H, W) float32
    mask: np.ndarray  # (H, W) bool, True = 有效
    weight_boost: float = 1.0  # 额外质量因子（如 1 - cloud_fraction）
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class MosaicResult:
    """镶嵌结果。"""

    data: np.ndarray  # (bands, H, W) float32
    weight_sum: np.ndarray  # (H, W) float32
    coverage: np.ndarray  # (H, W) uint8, 参与合成的景数
    valid: np.ndarray  # (H, W) bool
    per_scene_weight: dict[str, float]


# ── 权重计算 ─────────────────────────────────────────────────────────────────
def distance_weight(
    mask: np.ndarray,
    *,
    blend_distance: int = 64,
    power: float = 1.0,
) -> np.ndarray:
    """按「到有效区边缘的距离」生成羽化权重。

    有效区中心权重接近 1，边缘附近衰减到 0，实现接缝羽化。
    """
    if mask.ndim != 2:
        raise MosaicError(f"distance_weight: expected 2D mask, got {mask.shape}")
    valid = mask.astype(bool)
    if not valid.any():
        return np.zeros(mask.shape, dtype="float32")

    # 到最近无效像元的距离（有效区内部）
    dist = distance_transform_edt(valid)
    if blend_distance > 0:
        w = np.clip(dist / float(blend_distance), 0.0, 1.0)
    else:
        w = (dist > 0).astype("float32")
    if power != 1.0:
        w = np.power(w, power)
    return w.astype("float32")


def uniform_weight(mask: np.ndarray) -> np.ndarray:
    return mask.astype("float32")


# ── 镶嵌主流程 ───────────────────────────────────────────────────────────────
def build_weights(
    sources: Sequence[MosaicSource],
    *,
    feather: Feather = "distance",
    blend_distance: int = 64,
    power: float = 1.0,
) -> dict[str, np.ndarray]:
    """为每个源计算 2D 权重图。"""
    out: dict[str, np.ndarray] = {}
    for s in sources:
        if feather == "distance":
            w = distance_weight(s.mask, blend_distance=blend_distance, power=power)
        elif feather in ("first", "mean"):
            w = uniform_weight(s.mask)
        else:
            raise MosaicError(f"unknown feather mode: {feather!r}")
        out[s.scene_id] = w * float(s.weight_boost)
    return out


def mosaic(
    sources: Sequence[MosaicSource],
    *,
    feather: Feather = "distance",
    blend_distance: int = 64,
    power: float = 1.0,
    dtype: str = "float32",
    fill: float = float("nan"),
) -> MosaicResult:
    """把多个已对齐到同一网格的景合成一景。

    所有源必须同 shape。
    """
    if not sources:
        raise MosaicError("mosaic: no sources")
    shape0 = sources[0].data.shape
    for s in sources:
        if s.data.shape != shape0:
            raise MosaicError(
                f"mosaic: {s.scene_id} shape {s.data.shape} != {shape0}"
            )
        if s.mask.shape != shape0[1:]:
            raise MosaicError(f"mosaic: {s.scene_id} mask shape mismatch")

    bands, height, width = shape0
    weights = build_weights(
        sources, feather=feather, blend_distance=blend_distance, power=power
    )

    if feather == "first":
        return _mosaic_first(sources, bands, height, width, dtype, fill)

    acc = np.zeros((bands, height, width), dtype="float64")
    wacc = np.zeros((height, width), dtype="float64")
    coverage = np.zeros((height, width), dtype="uint8")
    per_scene: dict[str, float] = {}

    for s in sources:
        w = weights[s.scene_id]
        valid = s.mask & np.isfinite(s.data[0])
        if not valid.any():
            per_scene[s.scene_id] = 0.0
            continue
        data = np.where(np.isfinite(s.data), s.data, 0.0)
        acc += data * w[np.newaxis, :, :]
        wacc += w
        coverage += valid.astype("uint8")
        per_scene[s.scene_id] = float(w[valid].mean())

    with np.errstate(invalid="ignore", divide="ignore"):
        out = acc / wacc[np.newaxis, :, :]
    out[:, wacc <= 0] = fill
    out = out.astype(dtype)

    valid = wacc > 0
    log.info(
        "mosaic.done",
        sources=len(sources),
        feather=feather,
        coverage_mean=float(coverage.mean()),
    )
    return MosaicResult(
        data=out,
        weight_sum=wacc.astype("float32"),
        coverage=coverage,
        valid=valid,
        per_scene_weight=per_scene,
    )


def _mosaic_first(
    sources: Sequence[MosaicSource],
    bands: int,
    height: int,
    width: int,
    dtype: str,
    fill: float,
) -> MosaicResult:
    """按顺序优先覆盖（先到先得）。"""
    out = np.full((bands, height, width), fill, dtype=dtype)
    wacc = np.zeros((height, width), dtype="float32")
    coverage = np.zeros((height, width), dtype="uint8")
    placed = np.zeros((height, width), dtype=bool)
    per_scene: dict[str, float] = {}

    for s in sources:
        valid = s.mask & np.isfinite(s.data[0]) & ~placed
        if not valid.any():
            per_scene[s.scene_id] = 0.0
            continue
        out[:, valid] = s.data[:, valid].astype(dtype)
        wacc[valid] = 1.0
        coverage[valid] += 1
        placed |= valid
        per_scene[s.scene_id] = float(valid.mean())

    return MosaicResult(
        data=out,
        weight_sum=wacc,
        coverage=coverage,
        valid=placed,
        per_scene_weight=per_scene,
    )


# ── 网格对齐辅助 ─────────────────────────────────────────────────────────────
def warp_to_grid(
    arr: np.ndarray,
    *,
    src_transform: Any,
    src_crs: Any,
    dst_transform: Any,
    dst_crs: Any,
    out_shape: tuple[int, int],
    resampling: Any = None,
    dst_nodata: float = float("nan"),
) -> np.ndarray:
    """把单景重投影到目标网格（镶嵌前对齐）。"""
    from rasterio.warp import Resampling, reproject

    if resampling is None:
        resampling = Resampling.bilinear
    bands = arr.shape[0]
    out = np.full((bands, out_shape[0], out_shape[1]), dst_nodata, dtype="float32")
    for i in range(bands):
        reproject(
            source=arr[i].astype("float32"),
            destination=out[i],
            src_transform=src_transform,
            src_crs=src_crs,
            dst_transform=dst_transform,
            dst_crs=dst_crs,
            resampling=resampling,
            src_nodata=np.nan,
            dst_nodata=dst_nodata,
        )
    return out


def compute_target_grid(
    bounds: Sequence[tuple[float, float, float, float]],
    crs: Any,
    resolution: float,
) -> tuple[Any, int, int]:
    """由多个 bbox 与分辨率推导目标网格 (transform, width, height)。"""
    from rasterio.transform import from_origin

    if not bounds:
        raise MosaicError("compute_target_grid: empty bounds")
    minx = min(b[0] for b in bounds)
    miny = min(b[1] for b in bounds)
    maxx = max(b[2] for b in bounds)
    maxy = max(b[3] for b in bounds)
    width = max(1, int(np.ceil((maxx - minx) / resolution)))
    height = max(1, int(np.ceil((maxy - miny) / resolution)))
    transform = from_origin(minx, maxy, resolution, resolution)
    return transform, width, height


__all__ = [
    "Feather",
    "MosaicResult",
    "MosaicSource",
    "build_weights",
    "compute_target_grid",
    "distance_weight",
    "mosaic",
    "uniform_weight",
    "warp_to_grid",
]
