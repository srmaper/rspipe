"""多年同期序列：VCI 与 NDVI 距平。

思路（与 WMO / USDA 的 VCI 定义一致）：
  取目标景同一季节窗口（±WINDOW_DAYS 天）内、历史各年的 NDVI，逐像元统计
    VCI     = (NDVI_t − NDVI_min_hist) / (NDVI_max_hist − NDVI_min_hist) × 100
    NDVI 距平 = NDVI_t − mean(NDVI_hist)
  历史年份至少 MIN_HISTORY 年有效，否则该像元不输出。

历史景的 NDVI 写到 analysis/_series/<目标景>/<历史景>/，不混入分析历史列表。
"""

from __future__ import annotations

import json
import warnings
from datetime import date, datetime, timedelta
from pathlib import Path

import numpy as np

from rspipe.web.analysis import (
    INDICES,
    SERIES_INDICES,
    _copy_sources,
    collection_from_scene_id,
    compute_scene_index,
)

WINDOW_DAYS = 16
MIN_HISTORY = 3
DEFAULT_YEARS = 8
MAX_CLOUD = 30.0
# 各数据族可用的最早年份（历史序列不会越过这一年）
EARLIEST_YEAR = {"sentinel-2-l2a": 2017, "landsat-c2-l2": 1985}
# 历史景需要下载的近红外波段名（Landsat 的 NIR 资产叫 nir08）
NIR_KEY = {"sentinel-2-l2a": "nir", "landsat-c2-l2": "nir08"}


def history_years(target_year: int, n_years: int, earliest: int) -> list[int]:
    """目标年之前的 n_years 个年份（不含目标年），不早于 earliest。"""
    last = target_year - 1
    first = max(earliest, last - max(1, n_years) + 1)
    return list(range(first, last + 1))


def same_season(target: date, year: int) -> tuple[date, date]:
    """把目标日期平移到 year 年，返回 ±WINDOW_DAYS 的窗口。"""
    try:
        center = target.replace(year=year)
    except ValueError:  # 2 月 29 日
        center = date(year, 2, 28)
    return center - timedelta(days=WINDOW_DAYS), center + timedelta(days=WINDOW_DAYS)


def vci_anomaly(
    hist: np.ndarray, tgt: np.ndarray, min_years: int = MIN_HISTORY, min_ndvi: float = 0.1
):
    """hist: (n, H, W) 历史同期 NDVI；tgt: (H, W) 目标 NDVI。返回 (vci, anom)。

    VCI 只对植被有意义，NDVI 低于 min_ndvi 的像元（水面/裸地）不输出。
    """
    cnt = np.isfinite(hist).sum(axis=0)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        hmin = np.nanmin(hist, axis=0)
        hmax = np.nanmax(hist, axis=0)
        hmean = np.nanmean(hist, axis=0)
    rng = hmax - hmin
    enough = (cnt >= min_years) & np.isfinite(tgt) & (tgt > min_ndvi)
    ok = enough & (rng > 0.05)
    safe_rng = np.where(rng > 0.05, rng, 1.0)
    vci = np.where(ok, np.clip((tgt - hmin) / safe_rng, 0.0, 1.0) * 100.0, np.nan)
    anom = np.where(enough, tgt - hmean, np.nan)
    return vci.astype("float32"), anom.astype("float32")


def _cover(scene_bbox, aoi) -> float:
    """景 bbox 覆盖 AOI 的比例（0~1）。"""
    if not scene_bbox or not aoi:
        return 0.0
    ix0, iy0 = max(scene_bbox[0], aoi[0]), max(scene_bbox[1], aoi[1])
    ix1, iy1 = min(scene_bbox[2], aoi[2]), min(scene_bbox[3], aoi[3])
    if ix1 <= ix0 or iy1 <= iy0:
        return 0.0
    inter = (ix1 - ix0) * (iy1 - iy0)
    area = (aoi[2] - aoi[0]) * (aoi[3] - aoi[1])
    return inter / area if area > 0 else 0.0


def pick_scene(client, collection: str, aoi, target: date, year: int):
    """同季窗口内挑一景：覆盖优先（≥0.8 视为完整），其次云量低，再其次离目标日近。"""
    start, end = same_season(target, year)
    scenes = client.search(
        bbox=tuple(aoi),
        start=datetime(start.year, start.month, start.day),
        end=datetime(end.year, end.month, end.day, 23, 59, 59),
        max_cloud_cover=MAX_CLOUD,
        max_items=50,
        collections=[collection],
    )
    scenes = [s for s in scenes if s.collection == collection]
    if not scenes:
        return None

    center = start + timedelta(days=WINDOW_DAYS)

    def key(s):
        cov = _cover(s.bbox, aoi)
        cloud = s.cloud_cover if s.cloud_cover is not None else 100.0
        dist = abs((s.datetime.date() - center).days)
        return (cov < 0.8, -round(cov, 1), cloud, dist)

    scenes.sort(key=key)
    return scenes[0]


def _to_grid(src: Path, shape, transform, crs) -> np.ndarray:
    import rasterio
    from rasterio.enums import Resampling
    from rasterio.warp import reproject

    dst = np.full(shape, np.nan, dtype="float32")
    with rasterio.open(src) as s:
        reproject(
            source=rasterio.band(s, 1),
            destination=dst,
            src_transform=s.transform,
            src_crs=s.crs,
            dst_transform=transform,
            dst_crs=crs,
            resampling=Resampling.bilinear,
            src_nodata=float("nan"),
            dst_nodata=float("nan"),
        )
    return dst


def _write_raster(dest: Path, arr: np.ndarray, transform, crs, name: str, desc: str) -> None:
    import rasterio

    dest.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(
        dest,
        "w",
        driver="GTiff",
        height=arr.shape[0],
        width=arr.shape[1],
        count=1,
        dtype="float32",
        crs=crs,
        transform=transform,
        nodata=float("nan"),
        compress="deflate",
        tiled=True,
        blockxsize=512,
        blockysize=512,
    ) as d:
        d.write(arr, 1)
        d.set_band_description(1, name)
        d.update_tags(index=name, description=desc)


def _summary(arr: np.ndarray, transform, crs) -> dict:
    import math

    import rasterio

    valid = np.isfinite(arr)
    vals = arr[valid]
    if vals.size == 0:
        return {"valid_fraction": 0.0}
    h, w = arr.shape
    b = rasterio.transform.array_bounds(h, w, transform)  # (left, bottom, right, top)
    lat_mid = math.radians((b[1] + b[3]) / 2)
    px_km2 = ((b[2] - b[0]) * 111320 * math.cos(lat_mid) / w) * ((b[3] - b[1]) * 110540 / h) / 1e6
    return {
        "mean": round(float(np.mean(vals)), 4),
        "median": round(float(np.median(vals)), 4),
        "p10": round(float(np.percentile(vals, 10)), 4),
        "p90": round(float(np.percentile(vals, 90)), 4),
        "min": round(float(vals.min()), 4),
        "max": round(float(vals.max()), 4),
        "valid_fraction": round(float(valid.mean()), 4),
        "area_km2": round(float(valid.sum()) * px_km2, 2),
        "bounds": [round(v, 5) for v in b],
    }


def compute_series(
    data_root: Path,
    scene_id: str,
    provider: str,
    collection: str,
    aoi,
    *,
    index: str,
    fetch,
    search_client,
    years: list[int],
    target_date: date,
    target_ndvi: Path,
) -> dict:
    """计算目标景的 VCI 与 NDVI 距平（写 analysis/<scene>/vci.tif、ndvi_anom.tif）。

    fetch(scene_id, collection, bbox)：下载该景 AOI 内的 NIR/红光波段（由调用方提供，避免本模块依赖 pipeline）。
    """
    import rasterio

    root = Path(data_root)
    with rasterio.open(target_ndvi) as t:
        tgt = t.read(1).astype("float32")
        transform, crs = t.transform, t.crs
        shape = (t.height, t.width)

    stack, used, skipped = [], [], []
    for y in years:
        try:
            scene = pick_scene(search_client, collection, aoi, target_date, y)
        except Exception as exc:
            skipped.append({"year": y, "reason": f"检索失败：{str(exc)[:80]}"})
            continue
        if scene is None:
            skipped.append({"year": y, "reason": "同季无可用影像"})
            continue
        sid = scene.scene_id
        try:
            fetch(sid, collection, list(aoi))
            res = compute_scene_index(
                root,
                provider,
                sid,
                collection,
                "ndvi",
                dest_dir=root / "analysis" / "_series" / scene_id / sid,
                keep_source=False,
            )
        except Exception as exc:
            skipped.append({"year": y, "scene_id": sid, "reason": f"处理失败：{str(exc)[:80]}"})
            continue
        if not res.get("ok"):
            skipped.append({"year": y, "scene_id": sid, "reason": res.get("error", "计算失败")})
            continue
        f = root / "analysis" / "_series" / scene_id / sid / "ndvi.tif"
        stack.append(_to_grid(f, shape, transform, crs))
        used.append(
            {
                "year": y,
                "scene_id": sid,
                "date": scene.datetime.date().isoformat(),
                "cloud": scene.cloud_cover,
            }
        )

    if len(used) < MIN_HISTORY:
        return {
            "ok": False,
            "error": f"历史有效年份不足（{len(used)}/{MIN_HISTORY}），请换区域或减少云量限制",
            "skipped": skipped,
        }

    hist = np.stack(stack, axis=0)
    vci, anom = vci_anomaly(hist, tgt)
    out_dir = root / "analysis" / scene_id
    out_dir.mkdir(parents=True, exist_ok=True)

    results = {}
    for name, arr in (("vci", vci), ("ndvi_anom", anom)):
        desc = INDICES[name][1]
        _write_raster(out_dir / f"{name}.tif", arr, transform, crs, name, desc)
        stats = {
            "index": name,
            "description": desc,
            "years_used": [u["year"] for u in used],
            "years_skipped": skipped,
            "history": used,
            "min_history": MIN_HISTORY,
            **_summary(arr, transform, crs),
        }
        (out_dir / f"{name}.json").write_text(
            json.dumps(stats, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        results[name] = stats

    has_source = _copy_sources(root, provider, scene_id, out_dir, "source_4326.tif", "aoi.geojson")
    # 接口返回请求的那个指数；另一个同时写盘，历史列表里可见
    primary = results[index]
    return {
        "ok": True,
        "key": scene_id,
        "stats": {**primary, "has_source": has_source, "all": results},
    }


def _raw_aoi_4326(raw_dir: Path, scene_id: str):
    import rasterio
    from rasterio.warp import transform_bounds

    for k in ("red", "nir08", "nir"):
        f = raw_dir / f"{scene_id}.{k}.tif"
        if f.exists():
            with rasterio.open(f) as s:
                return tuple(transform_bounds(s.crs, "EPSG:4326", *s.bounds))
    return None


def run_series_for_scene(
    data_root: Path,
    pipe,
    layout,
    provider: str,
    scene_id: str,
    index: str,
    *,
    years: int | None = None,
) -> dict:
    """接口入口：检索历史同期 → 下载 → 计算 VCI / 距平。"""
    from rspipe.web.download import download_scene_raw

    if index not in SERIES_INDICES:
        return {"ok": False, "error": f"不支持的序列指数：{index}"}
    collection = collection_from_scene_id(scene_id)
    if collection not in NIR_KEY:
        return {"ok": False, "error": "序列指数仅支持 Sentinel-2 与 Landsat"}

    root = Path(data_root)
    raw_dir = root / "raw" / provider / scene_id
    if not raw_dir.is_dir():
        return {"ok": False, "error": "该景未下载，请先下载"}
    aoi = _raw_aoi_4326(raw_dir, scene_id)
    if aoi is None:
        return {"ok": False, "error": "无法读取该景的 AOI 范围"}

    client = pipe.clients[provider]
    found = client.search(ids=[scene_id], max_items=1, collections=[collection])
    if not found:
        return {"ok": False, "error": f"{scene_id} 在 {provider} 上检索不到"}
    target_date = found[0].datetime.date()

    target_ndvi = root / "analysis" / scene_id / "ndvi.tif"
    if not target_ndvi.exists():
        res = compute_scene_index(root, provider, scene_id, collection, "ndvi")
        if not res.get("ok"):
            return {"ok": False, "error": res.get("error", "目标景 NDVI 计算失败")}

    earliest = EARLIEST_YEAR.get(collection, 1985)
    n_years = years or DEFAULT_YEARS
    ys = history_years(target_date.year, n_years, earliest)
    if not ys:
        return {"ok": False, "error": "没有可用的历史年份"}

    nir = NIR_KEY[collection]

    def fetch(sid: str, coll: str, bbox) -> None:
        download_scene_raw(pipe, layout, sid, provider, coll, tuple(bbox), keys=["red", nir])

    return compute_series(
        root,
        scene_id,
        provider,
        collection,
        aoi,
        index=index,
        fetch=fetch,
        search_client=client,
        years=ys,
        target_date=target_date,
        target_ndvi=target_ndvi,
    )
