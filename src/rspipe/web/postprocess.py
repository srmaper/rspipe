"""下载后处理：合并波段 → 重投影到 EPSG:4326 的预览栅格；写入 AOI。

产物：
  data/raw/<provider>/<scene>/<scene>.preview_4326.tif   4 波段 float32 反射率（R/G/B/NIR）
  data/raw/<provider>/<scene>/aoi.geojson                本次下载使用的 AOI 范围
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np

BAND_CANDIDATES = {
    "red": ("red",),
    "green": ("green",),
    "blue": ("blue",),
    "nir": ("nir", "nir08"),
}


def write_aoi_geojson(raw_dir: Path, bbox) -> Path | None:
    """把下载用的 bbox 写成 GeoJSON 面（打包随附）。"""
    try:
        w, s, e, n = (float(v) for v in bbox)
    except Exception:
        return None
    gj = {
        "type": "FeatureCollection",
        "name": raw_dir.name,
        "features": [
            {
                "type": "Feature",
                "properties": {"scene_id": raw_dir.name, "kind": "download_aoi_bbox"},
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [[[w, s], [e, s], [e, n], [w, n], [w, s]]],
                },
            }
        ],
    }
    dest = raw_dir / "aoi.geojson"
    dest.write_text(json.dumps(gj, ensure_ascii=False, indent=2), encoding="utf-8")
    return dest


def build_preview_4326(raw_dir: Path, collection: str) -> Path | None:
    """合并可用波段 → EPSG:4326 的 4 波段反射率栅格（供预览与打包）。"""
    import rasterio
    from rasterio.enums import Resampling
    from rasterio.warp import calculate_default_transform, reproject

    sid = raw_dir.name
    picks: dict[str, Path] = {}
    for name, cands in BAND_CANDIDATES.items():
        for c in cands:
            f = raw_dir / f"{sid}.{c}.tif"
            if f.exists():
                picks[name] = f
                break
    if len(picks) < 3:
        return None

    bands = [k for k in ("red", "green", "blue", "nir") if k in picks]
    ref_path = picks["red"]
    with rasterio.open(ref_path) as ref:
        ref_profile = {
            "height": ref.height,
            "width": ref.width,
            "crs": ref.crs,
            "transform": ref.transform,
        }
        merged = np.full((len(bands), ref.height, ref.width), np.nan, dtype="float32")
        for i, k in enumerate(bands):
            with rasterio.open(picks[k]) as src:
                same = (
                    (src.width, src.height) == (ref.width, ref.height)
                    and src.transform == ref.transform
                    and src.crs == ref.crs
                )
                if same:
                    arr = src.read(1).astype("float32")
                else:
                    arr = np.zeros((ref.height, ref.width), dtype="float32")
                    reproject(
                        source=rasterio.band(src, 1),
                        destination=arr,
                        src_transform=src.transform,
                        src_crs=src.crs,
                        dst_transform=ref.transform,
                        dst_crs=ref.crs,
                        resampling=Resampling.bilinear,
                    )
            arr = arr * 2.75e-05 - 0.2 if "landsat" in collection else arr / 10000.0
            arr[arr <= 0] = np.nan
            merged[i] = arr

    # 合并结果 → 4326（预览用途：最长边限 4096，uint16 存储 ρ×10000）
    dst_crs = "EPSG:4326"
    with rasterio.open(ref_path) as _r:
        src_bounds = _r.bounds
    transform, width, height = calculate_default_transform(
        ref_profile["crs"], dst_crs, ref_profile["width"], ref_profile["height"], *src_bounds
    )
    max_side = 4096
    if max(width, height) > max_side:
        s = max(width, height) / max_side  # 单一比例，保持长宽比
        width = max(1, round(width / s))
        height = max(1, round(height / s))
        transform = transform * transform.scale(s, s)

    out = np.zeros((len(bands), height, width), dtype="uint16")
    for i in range(len(bands)):
        tmp = np.full((height, width), np.nan, dtype="float32")
        reproject(
            source=merged[i],
            destination=tmp,
            src_transform=ref_profile["transform"],
            src_crs=ref_profile["crs"],
            dst_transform=transform,
            dst_crs=dst_crs,
            resampling=Resampling.bilinear,
            dst_nodata=float("nan"),
        )
        scaled = np.where(np.isfinite(tmp), np.clip(tmp, 0, 1) * 10000.0, 0)
        out[i] = scaled.astype("uint16")

    dest = raw_dir / f"{sid}.preview_4326.tif"
    profile = {
        "driver": "GTiff",
        "height": height,
        "width": width,
        "count": len(bands),
        "dtype": "uint16",
        "crs": dst_crs,
        "transform": transform,
        "nodata": 0,
        "compress": "deflate",
        "tiled": True,
        "blockxsize": 512,
        "blockysize": 512,
    }
    with rasterio.open(dest, "w", **profile) as d:
        d.write(out)
        for i, k in enumerate(bands, start=1):
            d.set_band_description(i, k)
        d.update_tags(REFLECTANCE_SCALE="0.0001", NOTE="DN*0.0001=reflectance; 0=nodata")
    return dest
