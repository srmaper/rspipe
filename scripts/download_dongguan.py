"""下载东莞两景：Sentinel-2 + Landsat，原始 DN 裁剪 + processed 成果双产物。

用法:
    python scripts/download_dongguan.py raw|processed|all
"""

from __future__ import annotations

import logging
import sys
import time
from pathlib import Path

logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

import rasterio  # noqa: E402
from affine import Affine  # noqa: E402

from rspipe.config import Settings, load_pipeline_config  # noqa: E402
from rspipe.ingest.cogreader import CogReader  # noqa: E402
from rspipe.ingest.signing import needs_signing, sign_href  # noqa: E402
from rspipe.pipeline import Pipeline  # noqa: E402
from rspipe.pipeline.steps import SceneContext, SceneProcessor  # noqa: E402
from rspipe.quality import aoi_cloud_fraction  # noqa: E402
from rspipe.storage.layout import DataLayout  # noqa: E402

DG = (113.620, 22.750, 114.300, 23.100)
AOI_NAME = "dongguan"

TARGETS = [
    ("earth-search", "sentinel-2-l2a", "S2B_49QGF_20261010_0_L2A"),
    ("planetary-computer", "landsat-c2-l2", "LC09_L2SP_122044_20260314_02_T1"),
]

# 原始 DN 下载的资产白名单（光谱 + 场景分类/QA）
RAW_KEYS_S2 = [
    "coastal", "blue", "green", "red", "rededge1", "rededge2", "rededge3",
    "nir", "nir08", "nir09", "swir16", "swir22", "scl",
]
RAW_KEYS_LS = ["coastal", "blue", "green", "red", "nir08", "swir16", "swir22", "qa_pixel"]


def _scene_by_id(pipe: Pipeline, prov: str, coll: str, sid: str):
    scenes = pipe.clients[prov].search(ids=[sid], max_items=1)
    if not scenes:
        raise SystemExit(f"{sid} not found on {prov}")
    s = scenes[0]
    s.provider = prov
    return s


def _raw_one_band(pipe, sid, prov, key, asset, out_dir, block_size):
    dest = out_dir / f"{sid}.{key}.tif"
    if dest.exists():
        return f"    {key:10s} exists, skipped"
    href = sign_href(asset.href) if needs_signing(asset.href) else asset.href
    t = time.time()
    with CogReader(href, block_size=block_size) as r:
        col, row, w, h = r.window_for_bbox(DG)
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
    mb = dest.stat().st_size / 1e6
    return f"    {key:10s} {w:5d}x{h:<5d} {mb:7.2f} MB  {time.time()-t:5.1f}s"


def download_raw(pipe: Pipeline, sid: str, prov: str, coll: str, layout: DataLayout, block_size=(1 << 23), workers=6) -> list[Path]:
    from concurrent.futures import ThreadPoolExecutor, as_completed

    scene = _scene_by_id(pipe, prov, coll, sid)
    keys = RAW_KEYS_S2 if "sentinel" in coll else RAW_KEYS_LS
    out_dir = layout.raw_scene_dir(prov, sid)
    out_dir.mkdir(parents=True, exist_ok=True)
    print(f"\n--- RAW {scene.scene_id} -> {out_dir}  (block={block_size>>20}MB x {workers} threads)", flush=True)
    t0 = time.time()
    futs = {}
    with ThreadPoolExecutor(max_workers=workers) as ex:
        for key in keys:
            asset = scene.asset_by_key(key)
            if asset is None:
                print(f"    [skip] no asset {key}", flush=True)
                continue
            futs[ex.submit(_raw_one_band, pipe, sid, prov, key, asset, out_dir, block_size)] = key
        for fu in as_completed(futs):
            try:
                print(fu.result(), flush=True)
            except Exception as e:
                print(f"    {futs[fu]:10s} FAILED: {e}", flush=True)
    written = list(out_dir.glob(f"{sid}.*.tif"))
    print(f"    [{'%.0f' % (time.time()-t0)}s total] {len(written)}/{len(keys)} files", flush=True)
    return written


def process(pipe: Pipeline, prov: str, coll: str, sid: str, cfg, layout: DataLayout):
    scene = _scene_by_id(pipe, prov, coll, sid)
    spec = pipe._spec_for(scene)
    ctx = SceneContext(
        scene=scene,
        config=cfg,
        layout=layout,
        auth=pipe._auth_for(prov),
        spec=spec,
        band_names=tuple(cfg.pipeline.bands),
        collection_id=AOI_NAME,
        aoi_bbox=DG,
    )
    print(f"\n--- PROCESSED {scene.scene_id} (AOI云={(aoi_cloud_fraction(scene, DG) or 0)*100:.2f}%)", flush=True)
    t = time.time()
    result = SceneProcessor(ctx).run(force=False)
    print(f"TOTAL {time.time()-t:.1f}s status={result.status}", flush=True)
    for st in result.steps:
        print(f"    {st.name:20s} {st.status:8s} {st.elapsed_s:6.1f}s", flush=True)
    if result.error:
        print("    ERROR:", result.error, flush=True)
    for label, p in (("COG", result.cog_path), ("STAC", result.stac_item_path)):
        if p and Path(p).exists():
            print(f"    {label} {Path(p).stat().st_size/1e6:8.2f} MB  {p}", flush=True)
    return result


def main() -> None:
    mode = sys.argv[1] if len(sys.argv) > 1 else "all"
    cfg = load_pipeline_config("configs")
    settings = Settings(_env_file=".env" if Path(".env").exists() else None)
    pipe = Pipeline.from_config(cfg, settings)
    layout = DataLayout(cfg.data_root)
    layout.ensure()

    for prov, coll, sid in TARGETS:
        if mode in ("raw", "all"):
            download_raw(pipe, sid, prov, coll, layout)
        if mode in ("processed", "all"):
            process(pipe, prov, coll, sid, cfg, layout)


if __name__ == "__main__":
    main()
