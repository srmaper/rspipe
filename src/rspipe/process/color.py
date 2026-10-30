"""匀色：直方图匹配、分位数对齐，消除景间色调/亮度差异。

目标：把「待调整」景的每个波段映射到「参考景」的辐射分布上，
使镶嵌后的影像无明显接缝。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Literal

import numpy as np

from rspipe.errors import ColorBalanceError
from rspipe.logging import get_logger

log = get_logger("rspipe.process.color")

Method = Literal["histogram", "quantile", "none"]


@dataclass(slots=True)
class BandStats:
    """单波段统计（用于参考选择与诊断）。"""

    band: str
    mean: float
    std: float
    p02: float
    p50: float
    p98: float
    valid_pixels: int


@dataclass(slots=True)
class ColorReport:
    """匀色结果报告。"""

    reference_id: str
    method: str
    per_band: dict[str, dict[str, float]]
    skipped: list[str]


# ── 基础统计 ─────────────────────────────────────────────────────────────────
def band_stats(arr: np.ndarray, band_name: str = "") -> BandStats:
    """计算单波段有效像元统计（忽略 nan）。"""
    data = arr.astype("float64").ravel()
    data = data[np.isfinite(data)]
    if data.size == 0:
        return BandStats(band_name, float("nan"), float("nan"), float("nan"), float("nan"), float("nan"), 0)
    p02, p50, p98 = np.percentile(data, [2.0, 50.0, 98.0])
    return BandStats(
        band=band_name,
        mean=float(data.mean()),
        std=float(data.std()),
        p02=float(p02),
        p50=float(p50),
        p98=float(p98),
        valid_pixels=int(data.size),
    )


def stack_stats(arr: np.ndarray, band_names: Sequence[str]) -> dict[str, BandStats]:
    if arr.ndim != 3:
        raise ColorBalanceError(f"stack_stats: expected (bands,H,W), got {arr.shape}")
    if len(band_names) != arr.shape[0]:
        raise ColorBalanceError("stack_stats: band_names length mismatch")
    return {name: band_stats(arr[i], name) for i, name in enumerate(band_names)}


# ── 直方图匹配 ───────────────────────────────────────────────────────────────
def _ecdf(data: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """经验 CDF：返回 (排序值, 累积概率)。"""
    values = data[np.isfinite(data)]
    if values.size == 0:
        return np.array([]), np.array([])
    values = np.sort(values)
    cdf = np.arange(1, values.size + 1, dtype="float64") / values.size
    # 去重以便插值
    uniq, idx = np.unique(values, return_index=True)
    return uniq, cdf[idx]


def match_histogram(
    source: np.ndarray,
    reference: np.ndarray,
    *,
    valid_mask: np.ndarray | None = None,
    clip: tuple[float, float] | None = None,
) -> np.ndarray:
    """把 source 的分布映射到 reference（单波段，2D）。

    算法：按经验 CDF 反函数插值。
    """
    src = source.astype("float32", copy=True)
    ref_vals = reference if valid_mask is None else reference[valid_mask]
    src_vals = source if valid_mask is None else source[valid_mask]

    ref_vals = ref_vals[np.isfinite(ref_vals)]
    src_vals = src_vals[np.isfinite(src_vals)]
    if ref_vals.size < 2 or src_vals.size < 2:
        return src

    ref_x, ref_cdf = _ecdf(ref_vals)
    src_x, src_cdf = _ecdf(src_vals)
    if ref_x.size < 2 or src_x.size < 2:
        return src

    # 对 source 的每个像元：先求其在 source CDF 中的位置，再映射到 reference 分位
    finite = np.isfinite(src)
    if not finite.any():
        return src

    flat = src[finite]
    # 求 flat 在 src_x 中的 CDF 值
    positions = np.interp(flat, src_x, src_cdf, left=src_cdf[0], right=src_cdf[-1])
    mapped = np.interp(positions, ref_cdf, ref_x, left=ref_x[0], right=ref_x[-1])
    src[finite] = mapped.astype("float32")

    if clip is not None:
        src = np.clip(src, clip[0], clip[1])
    return src


# ── 分位数对齐 ───────────────────────────────────────────────────────────────
def match_quantile(
    source: np.ndarray,
    reference: np.ndarray,
    *,
    quantiles: tuple[float, float] = (2.0, 98.0),
    valid_mask: np.ndarray | None = None,
) -> np.ndarray:
    """线性分位数拉伸对齐（更稳健、速度更快）。"""
    src = source.astype("float32", copy=True)
    ref_vals = reference if valid_mask is None else reference[valid_mask]
    src_vals = source if valid_mask is None else source[valid_mask]
    ref_vals = ref_vals[np.isfinite(ref_vals)]
    src_vals = src_vals[np.isfinite(src_vals)]
    if ref_vals.size < 2 or src_vals.size < 2:
        return src

    lo_q, hi_q = quantiles
    s_lo, s_hi = np.percentile(src_vals, [lo_q, hi_q])
    r_lo, r_hi = np.percentile(ref_vals, [lo_q, hi_q])
    if not np.isfinite([s_lo, s_hi, r_lo, r_hi]).all():
        return src
    if abs(s_hi - s_lo) < 1e-12:
        return src

    scaled = (src - s_lo) / (s_hi - s_lo) * (r_hi - r_lo) + r_lo
    return scaled.astype("float32")


# ── 场景级匀色 ───────────────────────────────────────────────────────────────
def choose_reference(
    scenes: Sequence[dict[str, Any]],
    *,
    mode: str = "best",
    target_id: str | None = None,
) -> str:
    """挑选参考景 id。

    - `best`  : 云量最低优先，其次有效像元最多
    - `first` : 序列第一景
    - 指定 id
    """
    if not scenes:
        raise ColorBalanceError("choose_reference: empty scene list")
    if target_id:
        for s in scenes:
            if s.get("scene_id") == target_id:
                return target_id
        raise ColorBalanceError(f"choose_reference: target {target_id!r} not found")
    if mode == "first":
        return str(scenes[0]["scene_id"])

    def score(s: dict[str, Any]) -> tuple[float, float]:
        cloud = float(s.get("cloud_cover") if s.get("cloud_cover") is not None else 100.0)
        valid = float(s.get("valid_fraction", 0.0))
        return (cloud, -valid)

    best = sorted(scenes, key=score)[0]
    return str(best["scene_id"])


def balance_scene(
    arr: np.ndarray,
    reference: np.ndarray,
    *,
    band_names: Sequence[str],
    method: Method = "histogram",
    quantiles: tuple[float, float] = (2.0, 98.0),
    valid_mask: np.ndarray | None = None,
    clip: tuple[float, float] | None = None,
) -> tuple[np.ndarray, dict[str, dict[str, float]]]:
    """把 `arr` 匀色到 `reference` 的分布。

    返回 (调整后数组, 逐波段报告)。
    """
    if arr.ndim != 3 or reference.ndim != 3:
        raise ColorBalanceError("balance_scene: expected (bands,H,W)")
    if arr.shape != reference.shape:
        raise ColorBalanceError(
            f"balance_scene: shape mismatch {arr.shape} vs {reference.shape}"
        )
    if method == "none":
        return arr.astype("float32", copy=True), {}

    out = np.empty_like(arr, dtype="float32")
    report: dict[str, dict[str, float]] = {}
    for i, name in enumerate(band_names):
        src_band = arr[i]
        ref_band = reference[i]
        before = band_stats(src_band, name)
        if method == "histogram":
            adjusted = match_histogram(src_band, ref_band, valid_mask=valid_mask, clip=clip)
        elif method == "quantile":
            adjusted = match_quantile(
                src_band, ref_band, quantiles=quantiles, valid_mask=valid_mask
            )
        else:
            raise ColorBalanceError(f"balance_scene: unknown method {method!r}")
        after = band_stats(adjusted, name)
        out[i] = adjusted
        report[name] = {
            "before_mean": before.mean,
            "after_mean": after.mean,
            "ref_mean": band_stats(ref_band, name).mean,
            "before_p50": before.p50,
            "after_p50": after.p50,
            "ref_p50": band_stats(ref_band, name).p50,
        }
    return out, report


def balance_collection(
    arrays: dict[str, np.ndarray],
    *,
    band_names: Sequence[str],
    reference_id: str | None = None,
    method: Method = "histogram",
    quantiles: tuple[float, float] = (2.0, 98.0),
    valid_masks: dict[str, np.ndarray] | None = None,
) -> tuple[dict[str, np.ndarray], ColorReport]:
    """对整个景集合做匀色。

    `arrays` : {scene_id: (bands,H,W)}
    返回 (调整后集合, 报告)。参考景本身不调整。
    """
    if not arrays:
        raise ColorBalanceError("balance_collection: empty")
    ids = list(arrays)
    ref_id = reference_id or ids[0]

    if ref_id not in arrays:
        raise ColorBalanceError(f"balance_collection: reference {ref_id!r} not in collection")

    ref = arrays[ref_id]
    out: dict[str, np.ndarray] = {}
    report: dict[str, dict[str, float]] = {}
    skipped: list[str] = []

    for sid in ids:
        if sid == ref_id:
            out[sid] = arrays[sid].astype("float32", copy=True)
            continue
        mask = (valid_masks or {}).get(sid)
        try:
            adjusted, rep = balance_scene(
                arrays[sid],
                ref,
                band_names=band_names,
                method=method,
                quantiles=quantiles,
                valid_mask=mask,
            )
            out[sid] = adjusted
            report[sid] = {k: v for band in rep.values() for k, v in band.items()}
        except ColorBalanceError as exc:
            log.warning("balance.failed", scene=sid, error=str(exc))
            skipped.append(sid)
            out[sid] = arrays[sid].astype("float32", copy=True)

    log.info("balance.done", reference=ref_id, method=method, scenes=len(out))
    return out, ColorReport(reference_id=ref_id, method=method, per_band=report, skipped=skipped)


__all__ = [
    "BandStats",
    "ColorReport",
    "Method",
    "balance_collection",
    "balance_scene",
    "band_stats",
    "choose_reference",
    "match_histogram",
    "match_quantile",
    "stack_stats",
]
