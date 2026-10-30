"""导出包附加内容：快视预览图（PNG）与 README 说明。"""

from __future__ import annotations

import io
from pathlib import Path

import numpy as np
from PIL import Image

BAND_CN = {
    "coastal": "海岸气溶胶",
    "blue": "蓝",
    "green": "绿",
    "red": "红",
    "rededge1": "红边1",
    "rededge2": "红边2",
    "rededge3": "红边3",
    "nir": "近红外",
    "nir08": "窄近红外",
    "nir09": "水汽",
    "swir16": "短波红外1",
    "swir22": "短波红外2",
    "scl": "场景分类",
    "qa_pixel": "像元质量标志",
}


def make_quicklook(raw_dir: Path, collection: str, max_px: int = 1024) -> bytes | None:
    """用 raw 的红/绿/蓝波段拼一张快视图（2% 拉伸）。"""
    import rasterio

    paths = {}
    for k in ("red", "green", "blue"):
        f = raw_dir / f"{raw_dir.name}.{k}.tif"
        if f.exists():
            paths[k] = f
    if len(paths) < 3:
        return None
    chans = []
    for k in ("red", "green", "blue"):
        with rasterio.open(paths[k]) as src:
            scale = max(1, max(src.width, src.height) // max_px)
            h = max(1, src.height // scale)
            w = max(1, src.width // scale)
            a = src.read(1, out_shape=(h, w)).astype("float32")
        a = a * 2.75e-05 - 0.2 if "landsat" in collection else a / 10000.0
        a[a <= 0] = np.nan
        chans.append(a)
    stack = np.stack(chans)
    valid = np.isfinite(stack).all(axis=0) & (stack.sum(axis=0) > 0)
    out = np.zeros((3, *valid.shape), dtype="float32")
    for i in range(3):
        v = stack[i]
        if valid.any():
            lo, hi = np.percentile(v[valid], [2, 98])
            if hi <= lo:
                hi = lo + 1e-6
            out[i] = np.clip((v - lo) / (hi - lo) * 255, 0, 255)
        out[i] = np.where(valid, out[i], 0)
    img = Image.fromarray(out.transpose(1, 2, 0).astype("uint8"), mode="RGB")
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def make_readme(
    scene_id: str, provider: str, collection: str, raw_dir: Path | None, has_cog: bool
) -> bytes:
    lines = [
        f"场景: {scene_id}",
        f"数据源: {provider} / {collection}",
        "",
        "== 文件说明 ==",
    ]
    if has_cog:
        lines.append(
            f"{scene_id}.cog.tif\n"
            "    成品影像：红/绿/蓝/近红外 4 波段地表反射率（float32，0~1）\n"
            "    已按 QA/SCL 去云去云影（NaN = 云或无数据），COG 格式带金字塔，\n"
            "    可直接在 QGIS/ArcGIS 打开，也可用于指数计算（NDVI 等）"
        )
    if raw_dir is not None:
        lines.append(
            "raw/\n"
            "    原始波段裁剪：AOI 范围内的 uint16 DN（未做辐射定标、未去云），\n"
            "    用于自行复算或做其他指数；各波段分辨率不同（10m/20m/60m），\n"
            "    因此同一景内不同波段文件的像素尺寸可能不一致"
        )
        lines.append(
            f"{scene_id}.composite.tif\n"
            "    合成影像：由 raw 的红/绿/蓝/近红外按定标公式合成的 4 波段反射率\n"
            "    （float32，0~1，NaN=无数据），已统一到红波段网格，可直接叠加/算指数"
        )
    lines.append(
        f"{scene_id}.preview_4326.tif\n"
        "    预览栅格：合并 R/G/B/NIR 并重投影到 EPSG:4326（uint16，值=反射率×10000，0=无数据），\n"
        "    平台预览即用此文件，体积小、无投影误差，可直接叠加到 Web 地图"
    )
    lines.append("aoi.geojson\n    本次下载使用的 AOI 范围（WGS84 面），便于复现裁剪范围")
    lines.append(
        "quicklook.png\n    快视预览图：红/绿/蓝 2% 线性拉伸合成，仅供目视参考，非定量产品"
    )
    lines.append("README.txt\n    本说明文件：目录结构、波段清单、定标公式")
    items = (
        sorted(raw_dir.parent.parent.parent.glob(f"catalog/*/{scene_id}.json")) if raw_dir else []
    )
    if items:
        lines.append(
            f"{items[0].parent.name}/{items[0].name}\n"
            "    STAC 元数据：采集时间、云量、波段资产、footprint 等；\n"
            "    rspi:* 字段为本流水线回填的质量指标（AOI 云量、有效像元比例、Tier 等）"
        )
    if raw_dir is not None:
        lines += ["", "== raw 波段清单 =="]
        for f in sorted(raw_dir.glob("*.tif")):
            key = f.name.rsplit(".", 2)[-2]
            lines.append(f"  {f.name:52s} {BAND_CN.get(key, key)}")
    lines += [
        "",
        "== 定标（DN → 地表反射率） ==",
        "  Sentinel-2 L2A : ρ = DN / 10000      （earth-search 数据已剔除 -1000 偏移）",
        "  Landsat 8/9 L2 : ρ = DN * 2.75e-05 - 0.2",
        "",
    ]
    return "\n".join(lines).encode("utf-8")


def make_composite(raw_dir: Path, collection: str, dest: Path) -> Path | None:
    """raw 波段 -> 4 波段反射率 GeoTIFF（red/green/blue/nir，float32）。"""
    import rasterio
    from rasterio.enums import Resampling
    from rasterio.warp import reproject

    sid = raw_dir.name
    picks = {}
    for name, cands in (
        ("red", ("red",)),
        ("green", ("green",)),
        ("blue", ("blue",)),
        ("nir", ("nir", "nir08")),
    ):
        for c in cands:
            f = raw_dir / f"{sid}.{c}.tif"
            if f.exists():
                picks[name] = f
                break
    if len(picks) < 3:
        return None
    bands = [k for k in ("red", "green", "blue", "nir") if k in picks]
    with rasterio.open(picks["red"]) as ref:
        profile = {
            "driver": "GTiff",
            "height": ref.height,
            "width": ref.width,
            "count": len(bands),
            "dtype": "uint16",
            "crs": ref.crs,
            "transform": ref.transform,
            "nodata": 0,
            "compress": "deflate",
            "tiled": True,
            "blockxsize": 512,
            "blockysize": 512,
        }
        dst = rasterio.open(dest, "w", **profile)
    try:
        for i, k in enumerate(bands, start=1):
            with rasterio.open(picks[k]) as src:
                same = (src.width, src.height) == (
                    profile["width"],
                    profile["height"],
                ) and src.transform == profile["transform"]
                if same:
                    arr = src.read(1).astype("float32")
                else:
                    arr = np.zeros((profile["height"], profile["width"]), dtype="float32")
                    reproject(
                        source=rasterio.band(src, 1),
                        destination=arr,
                        src_transform=src.transform,
                        src_crs=src.crs,
                        dst_transform=profile["transform"],
                        dst_crs=profile["crs"],
                        resampling=Resampling.bilinear,
                    )
            arr = arr * 2.75e-05 - 0.2 if "landsat" in collection else arr / 10000.0
            arr = np.where(np.isfinite(arr) & (arr > 0), np.clip(arr, 0, 1) * 10000.0, 0)
            dst.write(arr.astype("uint16"), i)
            dst.set_band_description(i, k)
    finally:
        dst.update_tags(REFLECTANCE_SCALE="0.0001", NOTE="DN*0.0001=reflectance; 0=nodata")
        dst.close()
    return dest
