"""下载执行：raw 全波段（并行+续传）；本地 raw 快速通道定位。"""

from __future__ import annotations

import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

log = logging.getLogger(__name__)


def local_raw(layout, provider: str, scene_id: str, key: str) -> Path | None:
    p = layout.raw_scene_dir(provider, scene_id) / f"{scene_id}.{key.lower()}.tif"
    return p if p.exists() else None


def download_scene_raw(pipe, layout, scene_id: str, provider: str, collection: str, bbox, *, keys=None, progress=None, block_size=1 << 23, workers=6) -> Path:
    """raw AOI 裁剪下载（8MB 块 × 6 线程，已存在文件跳过）。

    `keys`：波段白名单（默认全谱 + SCL/qa_pixel）。
    """

    from rspipe.ingest.cogreader import CogReader
    from rspipe.ingest.signing import needs_signing, sign_href

    scenes = pipe.clients[provider].search(ids=[scene_id], max_items=1)
    if not scenes:
        raise RuntimeError(f"{scene_id} not found on {provider}")
    scene = scenes[0]
    scene.provider = provider
    keys = keys or (
        ["coastal", "blue", "green", "red", "rededge1", "rededge2", "rededge3", "nir", "nir08", "nir09", "swir16", "swir22", "scl"]
        if "sentinel" in collection
        else ["coastal", "blue", "green", "red", "nir08", "swir16", "swir22", "qa_pixel"]
    )
    keys = [k for k in keys if scene.asset_by_key(k) is not None]
    out_dir = layout.raw_scene_dir(provider, scene_id)
    out_dir.mkdir(parents=True, exist_ok=True)

    def one(key: str) -> Path:
        import rasterio
        from affine import Affine

        dest = out_dir / f"{scene_id}.{key}.tif"
        if dest.exists():
            return dest
        asset = scene.asset_by_key(key)
        href = sign_href(asset.href) if needs_signing(asset.href) else asset.href
        with CogReader(href, block_size=block_size) as r:
            col, row, w, h = r.window_for_bbox(tuple(bbox))
            if w <= 0 or h <= 0:
                raise RuntimeError('AOI 与该景范围无交集，请重新圈选')
            data = r.read_window(col, row, w, h)
            a, b, c, d, e, f = r.info.transform or (1, 0, 0, 0, -1, 0)
            transform = Affine(a, b, c, d, e, f) * Affine.translation(col, row)
            epsg = r.info.epsg
        profile = {
            "driver": "GTiff",
            "height": int(data.shape[0]),
            "width": int(data.shape[1]),
            "count": 1,
            "dtype": str(data.dtype),
            "crs": f"EPSG:{epsg}" if epsg else None,
            "transform": transform,
            "compress": "deflate",
            "tiled": True,
            "blockxsize": 512,
            "blockysize": 512,
        }
        with rasterio.open(dest, "w", **profile) as dst:
            dst.write(data, 1)
            dst.set_band_description(1, key)
        return dest

    done = 0
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(one, k): k for k in keys}
        for fu in as_completed(futs):
            fu.result()
            done += 1
            if progress:
                progress(done / len(keys), f"raw {done}/{len(keys)} 波段")
    if progress:
        progress(0.95, "生成 4326 预览栅格…")
    from rspipe.web.postprocess import build_preview_4326, write_aoi_geojson

    try:
        write_aoi_geojson(out_dir, bbox)
        preview = build_preview_4326(out_dir, collection)
    except Exception as exc:
        log.warning("postprocess failed %s: %s", scene_id, exc)
        preview = None
    if progress:
        progress(1.0, f"raw 完成 {time.time()-t0:.0f}s" + ("（含 4326 预览）" if preview else ""))
    return out_dir
