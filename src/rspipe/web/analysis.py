"""影像分析：指数计算（NDVI/NDWI/NDBI/NBR/BSI/NDRE）、两期变化、统计与瓦片。

输入：data/raw/<provider>/<scene>/<scene>.<band>.tif（下载产物）
输出：data/analysis/<key>/<name>.tif（EPSG:4326，float32）+ meta.json
"""

from __future__ import annotations

import contextlib
import json
import math
from pathlib import Path
from typing import Any

import numpy as np

# name -> (所需波段, 说明)
INDICES = {
    "ndvi": (("red", "nir"), "归一化植被指数 (NIR-R)/(NIR+R)"),
    "ndwi": (("green", "nir"), "归一化水体指数 (G-NIR)/(G+NIR)"),
    "ndbi": (("swir16", "nir"), "归一化建筑指数 (SWIR1-NIR)/(SWIR1+NIR)"),
    "nbr": (("nir", "swir22"), "归一化燃烧指数 (NIR-SWIR2)/(NIR+SWIR2)"),
    "bsi": (("swir16", "red", "nir", "blue"), "裸土指数 ((SWIR1+R)-(NIR+B))/((SWIR1+R)+(NIR+B))"),
    "ndre": (("nir", "rededge1"), "红边叶绿素指数 (NIR-RE1)/(NIR+RE1)"),
}

# 伪彩：低 → 中 → 高
COLORMAPS = {
    "ndvi": [(139, 110, 80), (230, 215, 120), (0, 120, 55)],
    "ndwi": [(150, 120, 80), (245, 245, 245), (20, 70, 160)],
    "ndbi": [(0, 120, 55), (220, 220, 220), (200, 40, 40)],
    "nbr": [(40, 40, 40), (250, 200, 40), (0, 110, 60)],
    "bsi": [(240, 240, 240), (190, 170, 140), (110, 80, 50)],
    "ndre": [(139, 110, 80), (235, 220, 120), (0, 130, 60)],
}

# 每个指数的推荐阈值与显示区间（不同指数的取值范围差别很大，不能共用一个）
#   threshold        : 单期分类阈值（大于它算"命中"，如植被/水体/建成区）
#   slider           : (min, max, step)
#   vmin/vmax        : 伪彩映射的数值区间（固定，不按瓦片拉伸，避免拼接色差）
#   change_*         : 两期变化的阈值与色带区间
INDEX_META = {
    "ndvi": {
        "label": "植被指数",
        "threshold": 0.30,
        "slider": (-0.2, 0.9, 0.05),
        "vmin": -0.2,
        "vmax": 0.9,
        "low": "裸土 / 水体",
        "high": "浓密植被",
        "change_threshold": 0.10,
        "change_vmin": -0.3,
        "change_vmax": 0.3,
    },
    "ndwi": {
        "label": "水体指数",
        "threshold": 0.10,
        "slider": (-0.5, 0.6, 0.05),
        "vmin": -0.4,
        "vmax": 0.6,
        "low": "陆地",
        "high": "水体",
        "change_threshold": 0.10,
        "change_vmin": -0.3,
        "change_vmax": 0.3,
    },
    "ndbi": {
        "label": "建筑指数",
        "threshold": 0.05,
        "slider": (-0.5, 0.5, 0.05),
        "vmin": -0.4,
        "vmax": 0.4,
        "low": "植被 / 水体",
        "high": "建成区",
        "change_threshold": 0.05,
        "change_vmin": -0.2,
        "change_vmax": 0.2,
    },
    "nbr": {
        "label": "燃烧指数",
        "threshold": 0.30,
        "slider": (-0.5, 0.9, 0.05),
        "vmin": -0.2,
        "vmax": 0.9,
        "low": "火烧 / 裸地",
        "high": "健康植被",
        "change_threshold": 0.10,
        "change_vmin": -0.3,
        "change_vmax": 0.3,
    },
    "bsi": {
        "label": "裸土指数",
        "threshold": 0.10,
        "slider": (-0.5, 0.5, 0.05),
        "vmin": -0.4,
        "vmax": 0.5,
        "low": "植被 / 水体",
        "high": "裸土",
        "change_threshold": 0.05,
        "change_vmin": -0.2,
        "change_vmax": 0.2,
    },
    "ndre": {
        "label": "红边叶绿素",
        "threshold": 0.20,
        "slider": (-0.2, 0.7, 0.05),
        "vmin": -0.1,
        "vmax": 0.6,
        "low": "弱长势",
        "high": "强长势",
        "change_threshold": 0.05,
        "change_vmin": -0.2,
        "change_vmax": 0.2,
    },
}

BAND_ALIASES = {
    "nir": ("nir", "nir08"),
    "swir16": ("swir16", "swir1"),
    "swir22": ("swir22", "swir2"),
    "rededge1": ("rededge1", "red_edge_1"),
}


def _band_path(raw_dir: Path, sid: str, key: str) -> Path | None:
    for cand in BAND_ALIASES.get(key, (key,)):
        f = raw_dir / f"{sid}.{cand}.tif"
        if f.exists():
            return f
    return None


def _copy_sources(
    data_root: Path,
    provider: str,
    scene_id: str,
    dest_dir: Path,
    source_name: str,
    aoi_name: str,
) -> bool:
    """把该景的 4326 预览栅格（含全部已下载波段）与 AOI 复制进分析目录。

    分析结果因此自带原始影像，不依赖 raw 目录是否被清理，导出 zip 时一并打包。
    """
    import shutil

    raw_dir = Path(data_root) / "raw" / provider / scene_id
    pv = raw_dir / f"{scene_id}.preview_4326.tif"
    if not pv.exists():
        return False
    try:
        dst = dest_dir / source_name
        if not dst.exists() or dst.stat().st_mtime < pv.stat().st_mtime:
            shutil.copyfile(pv, dst)
    except OSError:
        return False
    aoi = raw_dir / "aoi.geojson"
    if aoi.exists():
        with contextlib.suppress(OSError):
            shutil.copyfile(aoi, dest_dir / aoi_name)
    return True


def _read_grid(raw_dir: Path, sid: str, keys, collection: str):
    """读所需波段到统一网格（以 red/第一个可用波段为基准），返回 (bands, transform, crs)。"""
    import rasterio
    from rasterio.enums import Resampling
    from rasterio.warp import reproject

    paths = {}
    for k in keys:
        p = _band_path(raw_dir, sid, k)
        if p is None:
            return None, None, None, [k]
        paths[k] = p

    ref_key = "red" if "red" in paths else next(iter(paths))
    with rasterio.open(paths[ref_key]) as ref:
        height, width = ref.height, ref.width
        transform, crs = ref.transform, ref.crs
        out = {}
        for k, p in paths.items():
            with rasterio.open(p) as src:
                same = (
                    (src.width, src.height) == (width, height)
                    and src.transform == transform
                    and src.crs == crs
                )
                if same:
                    arr = src.read(1).astype("float32")
                else:
                    arr = np.zeros((height, width), dtype="float32")
                    reproject(
                        source=rasterio.band(src, 1),
                        destination=arr,
                        src_transform=src.transform,
                        src_crs=src.crs,
                        dst_transform=transform,
                        dst_crs=crs,
                        resampling=Resampling.bilinear,
                    )
            arr = arr * 2.75e-05 - 0.2 if "landsat" in collection else arr / 10000.0
            arr[arr <= 0] = np.nan
            out[k] = arr
    return out, transform, crs, []


def _index_array(name: str, b: dict) -> np.ndarray:
    with np.errstate(invalid="ignore", divide="ignore"):
        if name == "ndvi":
            return (b["nir"] - b["red"]) / (b["nir"] + b["red"])
        if name == "ndwi":
            return (b["green"] - b["nir"]) / (b["green"] + b["nir"])
        if name == "ndbi":
            return (b["swir16"] - b["nir"]) / (b["swir16"] + b["nir"])
        if name == "nbr":
            return (b["nir"] - b["swir22"]) / (b["nir"] + b["swir22"])
        if name == "bsi":
            a = (b["swir16"] + b["red"]) - (b["nir"] + b["blue"])
            c = (b["swir16"] + b["red"]) + (b["nir"] + b["blue"])
            return a / c
        if name == "ndre":
            return (b["nir"] - b["rededge1"]) / (b["nir"] + b["rededge1"])
    raise ValueError(f"unknown index {name}")


def compute_scene_index(
    data_root: Path, provider: str, scene_id: str, collection: str, index: str
) -> dict:
    """计算单景指数：写 4326 栅格 + 统计。"""
    import rasterio
    from rasterio.enums import Resampling
    from rasterio.warp import calculate_default_transform, reproject

    raw_dir = Path(data_root) / "raw" / provider / scene_id
    if not raw_dir.is_dir():
        return {"ok": False, "error": "该景未下载"}
    keys, desc = INDICES[index]
    bands, transform, crs, missing = _read_grid(raw_dir, scene_id, keys, collection)
    if bands is None:
        return {"ok": False, "error": "缺少波段：" + ", ".join(missing)}

    arr = _index_array(index, bands)
    valid = np.isfinite(arr)
    if not valid.any():
        return {"ok": False, "error": "无有效像元"}

    # 面积统计（原生网格，米制）
    px_area = abs(transform[0] * transform[4])  # 像元面积 m²
    total_km2 = float(valid.sum()) * px_area / 1e6
    vals = arr[valid]

    # 输出到 4326
    dst_crs = "EPSG:4326"
    with rasterio.open(
        _band_path(raw_dir, scene_id, "red") or _band_path(raw_dir, scene_id, keys[0])
    ) as src0:
        src_bounds = src0.bounds
    dt, dw, dh = calculate_default_transform(crs, dst_crs, arr.shape[1], arr.shape[0], *src_bounds)
    out = np.full((dh, dw), np.nan, dtype="float32")
    reproject(
        source=arr,
        destination=out,
        src_transform=transform,
        src_crs=crs,
        dst_transform=dt,
        dst_crs=dst_crs,
        resampling=Resampling.bilinear,
        dst_nodata=float("nan"),
    )

    dest_dir = Path(data_root) / "analysis" / scene_id
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"{index}.tif"
    with rasterio.open(
        dest,
        "w",
        driver="GTiff",
        height=dh,
        width=dw,
        count=1,
        dtype="float32",
        crs=dst_crs,
        transform=dt,
        nodata=float("nan"),
        compress="deflate",
        tiled=True,
        blockxsize=512,
        blockysize=512,
    ) as d:
        d.write(out, 1)
        d.set_band_description(1, index)
        d.update_tags(index=index, description=desc, scene_id=scene_id)

    stats = {
        "index": index,
        "description": desc,
        "mean": round(float(np.nanmean(vals)), 4),
        "median": round(float(np.nanmedian(vals)), 4),
        "p10": round(float(np.nanpercentile(vals, 10)), 4),
        "p90": round(float(np.nanpercentile(vals, 90)), 4),
        "min": round(float(np.nanmin(vals)), 4),
        "max": round(float(np.nanmax(vals)), 4),
        "valid_fraction": round(float(valid.mean()), 4),
        "area_km2": round(total_km2, 2),
        "bounds": [round(v, 5) for v in rasterio.open(dest).bounds],
    }
    stats["has_source"] = _copy_sources(
        data_root, provider, scene_id, dest_dir, "source_4326.tif", "aoi.geojson"
    )
    (dest_dir / f"{index}.json").write_text(
        json.dumps(stats, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return {"ok": True, "stats": stats}


def classify_stats(
    data_root: Path, provider: str, scene_id: str, index: str, threshold: float
) -> dict:
    """按阈值统计"大于阈值"的面积（绿地率/水体占比等）。"""
    import rasterio

    f = Path(data_root) / "analysis" / scene_id / f"{index}.tif"
    if not f.exists():
        return {}
    with rasterio.open(f) as src:
        a = src.read(1)
        # 近似面积：用 4326 网格估算（纬度加权）
        h, w = a.shape
        left, bottom, right, top = src.bounds
        lat_mid = math.radians((bottom + top) / 2)
        dx = (right - left) * 111320 * math.cos(lat_mid) / w
        dy = (top - bottom) * 110540 / h
        px_km2 = dx * dy / 1e6
    valid = np.isfinite(a)
    above = valid & (a > threshold)
    total = float(valid.sum()) * px_km2
    hit = float(above.sum()) * px_km2
    return {
        "threshold": threshold,
        "above_area_km2": round(hit, 2),
        "valid_area_km2": round(total, 2),
        "above_ratio": round(hit / total, 4) if total else 0.0,
    }


def _bounds_of(path: Path):
    import rasterio

    with rasterio.open(path) as s:
        return tuple(s.bounds)


def compute_change(
    data_root: Path, provider: str, a: str, b: str, collection: str, index: str, threshold: float
) -> dict:
    """两期指数差值（b - a）与变化面积统计。"""
    import rasterio
    from rasterio.enums import Resampling
    from rasterio.warp import reproject

    # 缺结果时自动补算两期（同一 provider）
    for sid in (a, b):
        f = Path(data_root) / "analysis" / sid / f"{index}.tif"
        if not f.exists():
            coll = collection or ("landsat-c2-l2" if sid.startswith("L") else "sentinel-2-l2a")
            res = compute_scene_index(data_root, provider, sid, coll, index)
            if not res.get("ok"):
                return {"ok": False, "error": f"{sid} 计算失败：{res.get('error')}"}

    fa = Path(data_root) / "analysis" / a / f"{index}.tif"
    fb = Path(data_root) / "analysis" / b / f"{index}.tif"

    with rasterio.open(fa) as sa, rasterio.open(fb) as sb:
        dst = np.full((sa.height, sa.width), np.nan, dtype="float32")
        src_b = sb.read(1).astype("float32")
        reproject(
            source=src_b,
            destination=dst,
            src_transform=sb.transform,
            src_crs=sb.crs,
            dst_transform=sa.transform,
            dst_crs=sa.crs,
            resampling=Resampling.bilinear,
            dst_nodata=float("nan"),
        )
        arr_a = sa.read(1)
        transform, crs = sa.transform, sa.crs

    diff = dst - arr_a
    valid = np.isfinite(diff)
    if not valid.any():
        return {"ok": False, "error": "两期无重叠有效像元"}

    # 4326 网格 → 米制像元面积（纬度加权）
    left, bottom, right, top = _bounds_of(fa)
    h, w = diff.shape
    lat_mid = math.radians((bottom + top) / 2)
    px_area = ((right - left) * 111320 * math.cos(lat_mid) / w) * ((top - bottom) * 110540 / h)
    vals = diff[valid]
    dec = valid & (diff < -threshold)
    inc = valid & (diff > threshold)

    key = f"{a}__{b}"
    dest_dir = Path(data_root) / "analysis" / key
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"{index}_change.tif"
    with rasterio.open(
        dest,
        "w",
        driver="GTiff",
        height=diff.shape[0],
        width=diff.shape[1],
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
        d.write(diff, 1)
        d.set_band_description(1, f"{index}_change")
        d.update_tags(index=index, kind="change", a=a, b=b)

    stats = {
        "index": index,
        "kind": "change",
        "a": a,
        "b": b,
        "threshold": threshold,
        "mean_diff": round(float(np.nanmean(vals)), 4),
        "decrease_km2": round(float(dec.sum()) * px_area / 1e6, 2),
        "increase_km2": round(float(inc.sum()) * px_area / 1e6, 2),
        "valid_km2": round(float(valid.sum()) * px_area / 1e6, 2),
    }
    (dest_dir / f"{index}_change.json").write_text(
        json.dumps(stats, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    stats["has_source"] = _copy_sources(
        data_root, provider, a, dest_dir, "source_4326.tif", "aoi.geojson"
    )
    _copy_sources(data_root, provider, b, dest_dir, "source_b_4326.tif", "aoi_b.geojson")
    return {"ok": True, "key": key, "stats": stats}


def vectorize_index(
    data_root: Path,
    key: str,
    name: str,
    threshold: float,
    *,
    op: str = "above",
    min_pixels: int = 4,
    max_features: int = 3000,
    simplify_px: float = 1.0,
) -> dict:
    """按阈值二值化后转矢量（GeoJSON，EPSG:4326）。

    op="above" : index > threshold（植被/水体/建成区等）
    op="abs"   : |value| > threshold（两期变化的增减区域）

    返回的是"面"图层：丢掉小于 min_pixels 个像元的碎斑，按面积从大到小最多保留
    max_features 个，几何按 simplify_px 个像元抽稀，保证前端能流畅渲染。
    """
    import math

    import rasterio
    from rasterio import features
    from shapely.geometry import mapping, shape

    f = Path(data_root) / "analysis" / key / f"{name}.tif"
    if not f.exists():
        return {"type": "FeatureCollection", "features": [], "count": 0}

    with rasterio.open(f) as src:
        a = src.read(1)
        transform = src.transform

    valid = np.isfinite(a)
    mask = (np.abs(a) > threshold) if op == "abs" else (a > threshold)
    mask &= valid
    if not mask.any():
        return {"type": "FeatureCollection", "features": [], "count": 0}

    px_deg = abs(transform.a * transform.e)
    min_area = px_deg * max(1, min_pixels)
    tol = abs(transform.a) * max(0.0, simplify_px)
    lat_mid = math.radians((transform.f + transform.e * a.shape[0] / 2) if transform.e else 0.0)
    km2_per_deg2 = 111.32 * 110.54 * math.cos(lat_mid)

    polys = []
    for geom, val in features.shapes(
        mask.astype("uint8"), mask=mask, transform=transform, connectivity=4
    ):
        if not val:
            continue
        sh = shape(geom)
        if sh.is_empty or sh.area < min_area:
            continue
        if tol > 0:
            sh = sh.simplify(tol, preserve_topology=True)
            if sh.is_empty:
                continue
        polys.append((sh.area, sh))

    polys.sort(key=lambda x: -x[0])
    feats = [
        {
            "type": "Feature",
            "geometry": mapping(sh),
            "properties": {"area_km2": round(area * km2_per_deg2, 3)},
        }
        for area, sh in polys[:max_features]
    ]
    out = {
        "type": "FeatureCollection",
        "features": feats,
        "count": len(polys),
        "truncated": len(polys) > max_features,
        "threshold": threshold,
        "op": op,
        "name": name,
    }
    # 落到分析目录，导出 zip 时一并打包
    with contextlib.suppress(OSError):
        (f.parent / f"{name}_vector.geojson").write_text(
            json.dumps(out, ensure_ascii=False), encoding="utf-8"
        )
    return out


def record_run(data_root: Path, key: str, payload: dict) -> None:
    """把本次分析的参数（模式/指数/阈值/两期景）记到 run.json，供历史列表使用。"""
    d = Path(data_root) / "analysis" / key
    if not d.is_dir():
        return
    payload = dict(payload)
    payload.setdefault("created", __import__("time").time())
    (d / "run.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def list_history(data_root: Path, limit: int = 50) -> list[dict]:
    """扫描 data/analysis/*，返回按时间倒序的分析历史。"""
    base = Path(data_root) / "analysis"
    if not base.is_dir():
        return []
    dirs = [d for d in base.iterdir() if d.is_dir()]
    dirs.sort(key=lambda d: -d.stat().st_mtime)

    out: list[dict] = []
    for d in dirs[: max(1, limit)]:
        run: dict = {}
        rp = d / "run.json"
        if rp.exists():
            try:
                run = json.loads(rp.read_text(encoding="utf-8"))
            except Exception:
                run = {}
        idx = str(run.get("index") or "")
        if not idx:
            tifs = sorted(d.glob("*.tif"))
            idx = tifs[0].stem if tifs else ""
        if not idx:
            continue
        stats: dict = {}
        sp = d / f"{idx}.json"
        if sp.exists():
            try:
                stats = json.loads(sp.read_text(encoding="utf-8"))
            except Exception:
                stats = {}
        parts = d.name.split("__")
        out.append(
            {
                "key": d.name,
                "index": idx,
                "mode": run.get("mode", "change" if "__" in d.name else "single"),
                "threshold": run.get("threshold"),
                "provider": run.get("provider"),
                "scene_a": run.get("scene_a") or parts[0],
                "scene_b": run.get("scene_b") or (parts[1] if len(parts) > 1 else None),
                "created": run.get("created") or d.stat().st_mtime,
                "stats": stats,
                "has_source": (d / "source_4326.tif").exists(),
                "has_aoi": (d / "aoi.geojson").exists(),
            }
        )
    return out


def _quicklook_png(tif: Path, *, max_size: int = 1024) -> bytes | None:
    """把指数栅格渲染成小尺寸 PNG（同一套色带与固定区间）。"""
    import io

    import rasterio
    from PIL import Image

    try:
        with rasterio.open(tif) as src:
            scale = max(1, int(max(src.width, src.height) / max_size))
            w = max(1, src.width // scale)
            h = max(1, src.height // scale)
            arr = src.read(1, out_shape=(h, w)).astype("float32")
    except Exception:
        return None

    base = tif.stem.replace("_change", "")
    meta: dict[str, Any] = INDEX_META.get(base, {})
    stops = COLORMAPS.get(base, COLORMAPS["ndvi"])
    if tif.stem.endswith("_change"):
        stops = [(200, 40, 40), (245, 245, 245), (0, 130, 60)]
        lo, hi = float(meta.get("change_vmin", -0.5)), float(meta.get("change_vmax", 0.5))
    else:
        lo, hi = float(meta.get("vmin", -1.0)), float(meta.get("vmax", 1.0))

    valid = np.isfinite(arr)
    tt = np.clip((np.nan_to_num(arr, nan=lo) - lo) / (hi - lo), 0, 1)
    lo_t, hi_t = np.clip(tt * 2, 0, 1), np.clip(tt * 2 - 1, 0, 1)
    rgba = np.zeros((h, w, 4), dtype="uint8")
    for c in range(3):
        c01 = stops[0][c] + (stops[1][c] - stops[0][c]) * lo_t
        c12 = stops[1][c] + (stops[2][c] - stops[1][c]) * hi_t
        rgba[..., c] = np.where(valid, np.where(tt <= 0.5, c01, c12), 0).astype("uint8")
    rgba[..., 3] = np.where(valid, 255, 0).astype("uint8")
    buf = io.BytesIO()
    Image.fromarray(rgba, mode="RGBA").save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def export_bundle(data_root: Path, key: str, out_zip: Path) -> Path:
    """把一整套分析结果（栅格 + 统计 json + 快视图 + 说明）打成 zip。"""
    import zipfile

    src_dir = Path(data_root) / "analysis" / key
    if not src_dir.is_dir():
        raise FileNotFoundError(f"analysis dir not found: {src_dir}")

    out_zip.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out_zip, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for f in sorted(src_dir.iterdir()):
            if not f.is_file():
                continue
            zf.write(f, f.name)
            if f.suffix.lower() in (".tif", ".tiff") and not f.name.startswith("source"):
                png = _quicklook_png(f)
                if png:
                    zf.writestr(f"{f.stem}_quicklook.png", png)
        readme = (
            "分析结果包\n"
            "==========\n\n"
            f"来源：{key}\n\n"
            "内容：\n"
            "  *.tif               指数/变化栅格（EPSG:4326，float32，nodata=NaN）\n"
            "  source_4326.tif     原始影像（分析用的 4326 栅格，1=R 2=G 3=B 4=NIR，ρ×10000）\n"
            "  source_b_4326.tif   两期变化时第二期原始影像\n"
            "  aoi.geojson         下载 AOI 边界（两期时为 aoi.geojson / aoi_b.geojson）\n"
            "  *.json              统计结果（均值/中位数/P10/P90/面积等）\n"
            "  *_quicklook.png     伪彩快视图（与网页同一套色带与区间）\n"
            "  *_vector.geojson    按阈值二值化后的矢量面（含 area_km2 属性）\n\n"
            "读取示例：\n"
            "  python -c \"import rasterio; s=rasterio.open('ndvi.tif'); print(s.bounds, s.crs)\"\n"
            "  python -c \"import rasterio; s=rasterio.open('source_4326.tif'); print(s.count, s.dtypes)\"\n"
        )
        zf.writestr("README.txt", readme)
    return out_zip


def render_index_tile(data_root: Path, key: str, name: str, z: int, x: int, y: int) -> bytes | None:
    """指数/变化栅格 → 伪彩瓦片。"""
    import io

    import rasterio
    from PIL import Image
    from rasterio.enums import Resampling
    from rasterio.windows import Window

    from rspipe.web.tiles import TILE, plan_tile_window, tile_bounds

    f = Path(data_root) / "analysis" / key / f"{name}.tif"
    if not f.exists():
        return None
    with rasterio.open(f) as src:
        plan = plan_tile_window(
            src.bounds, src.crs, src.transform, src.width, src.height, tile_bounds(z, x, y)
        )
        if plan is None:
            return None
        (dx0, dy0, dx1, dy1), (col, row, w, h) = plan
        arr = src.read(
            1,
            window=Window(col, row, w, h),
            out_shape=(dy1 - dy0, dx1 - dx0),
            resampling=Resampling.bilinear,
        ).astype("float32")

    is_change = name.endswith("_change")
    base = name.replace("_change", "")
    meta: dict[str, Any] = INDEX_META.get(base, {})
    valid = np.isfinite(arr)
    if is_change:
        stops = [(200, 40, 40), (245, 245, 245), (0, 130, 60)]  # 减少→红, 无变化→白, 增加→绿
        lo, hi = meta.get("change_vmin", -0.5), meta.get("change_vmax", 0.5)
    else:
        stops = COLORMAPS.get(base, COLORMAPS["ndvi"])
        lo, hi = meta.get("vmin", -1.0), meta.get("vmax", 1.0)
    t = np.clip((arr - lo) / (hi - lo), 0, 1)
    lo_t, hi_t = np.clip(t * 2, 0, 1), np.clip(t * 2 - 1, 0, 1)
    rgb = np.zeros((3, *arr.shape), dtype="float32")
    for c in range(3):
        c01 = stops[0][c] + (stops[1][c] - stops[0][c]) * lo_t
        c12 = stops[1][c] + (stops[2][c] - stops[1][c]) * hi_t
        rgb[c] = np.where(valid, np.where(t <= 0.5, c01, c12), 0)

    rgba = np.zeros((TILE, TILE, 4), dtype="uint8")
    rgba[dy0:dy1, dx0:dx1, :3] = rgb.transpose(1, 2, 0).astype("uint8")
    rgba[dy0:dy1, dx0:dx1, 3] = np.where(valid, 255, 0).astype("uint8")
    buf = io.BytesIO()
    Image.fromarray(rgba, mode="RGBA").save(buf, format="PNG", optimize=True)
    return buf.getvalue()
