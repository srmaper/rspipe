"""下载深圳范围内各一景 Sentinel-2 与 Landsat 影像。"""

from __future__ import annotations

import time
from pathlib import Path

from shapely.geometry import box, shape

from rspipe.config import Settings, load_pipeline_config
from rspipe.models import SceneMeta
from rspipe.pipeline import Pipeline
from rspipe.pipeline.steps import SceneContext, SceneProcessor
from rspipe.storage.layout import DataLayout

AOI = box(113.760, 22.410, 114.620, 22.870)
AOI_BBOX = (113.760, 22.410, 114.620, 22.870)
AOI_FILE = "configs/aoi_shenzhen.geojson"

WANT = {
    "sentinel-2-l2a": "S2A_49QHF_20261007_0_L2A",
    "landsat-c2-l2": "LC09_L2SP_122044_20260906_02_T2",
}


def overlap(item_geom) -> float:
    g = shape(item_geom) if item_geom else None
    return g.intersection(AOI).area / AOI.area if g else 0.0


def main() -> None:
    cfg = load_pipeline_config("configs")
    cfg.pipeline.aoi = AOI_FILE
    settings = Settings(_env_file=".env" if Path(".env").exists() else None)
    pipe = Pipeline.from_config(cfg, settings)
    layout = DataLayout(cfg.data_root)
    layout.ensure()
    print(f"data root: {cfg.data_root}", flush=True)
    print(f"clients  : {list(pipe.clients)}", flush=True)

    # ── 逐传感器检索并选定最优景 ──────────────────────────────────────────
    chosen: dict[str, SceneMeta] = {}
    for provider, scenes_by_coll in (
        ("earth-search", ("sentinel-2-l2a",)),
        ("planetary-computer", ("landsat-c2-l2",)),
    ):
        client = pipe.clients.get(provider)
        if client is None:
            print(f"[skip] {provider}: client unavailable", flush=True)
            continue
        for coll in scenes_by_coll:
            found = client.search(
                bbox=AOI_BBOX,
                start=None,
                end=None,
                max_cloud_cover=15.0,
                max_items=20,
            )
            found = [s for s in found if s.collection == coll]
            # 按 AOI 实测云量排序（Tier1 优先），不再是 STAC 整景云量
            from rspipe.quality import format_ranked, rank_scenes

            ranked = rank_scenes(found, AOI_BBOX, prefer_tier1=True, min_overlap=0.3)
            print(format_ranked(ranked[:4]), flush=True)
            scored = [r.scene for r in ranked]
            print(f"\n[{provider}/{coll}] {len(scored)} candidates", flush=True)
            for s in scored[:3]:
                print(
                    f"   overlap={overlap(s.geometry) * 100:5.1f}% cloud={s.cloud_cover:5.2f}%  {s.scene_id}",
                    flush=True,
                )
            target = WANT.get(coll)
            pick = next((s for s in scored if s.scene_id == target), scored[0] if scored else None)
            if pick is not None:
                pick.provider = provider
                chosen[coll] = pick
                print(f"   -> SELECTED {pick.scene_id}  cloud={pick.cloud_cover:.2f}%", flush=True)

    if len(chosen) < 2:
        raise SystemExit(f"need 2 scenes, got {len(chosen)}")

    # ── 处理（AOI 窗口化下载 -> 归一化 -> COG -> 去云 -> STAC）──────────────
    results = {}
    for coll, scene in chosen.items():
        spec = pipe._spec_for(scene)
        ctx = SceneContext(
            scene=scene,
            config=cfg,
            layout=layout,
            auth=pipe._auth_for(scene.provider),
            spec=spec,
            band_names=tuple(cfg.pipeline.bands),
            collection_id="shenzhen",
        )
        print(f"\n=== processing {scene.scene_id} ===", flush=True)
        t = time.time()
        result = SceneProcessor(ctx).run(force=True)
        results[coll] = result
        print(f"TOTAL {time.time() - t:.1f}s status={result.status}", flush=True)
        for st in result.steps:
            print(f"   {st.name:22s} {st.status:8s} {st.elapsed_s:6.1f}s", flush=True)
        if result.error:
            print(f"   ERROR: {result.error}", flush=True)

    # ── 汇总 ──────────────────────────────────────────────────────────────
    print("\n" + "=" * 72, flush=True)
    print("SUMMARY", flush=True)
    print("=" * 72, flush=True)
    for coll, r in results.items():
        print(f"\n[{coll}] {r.scene_id}  status={r.status}", flush=True)
        for label, p in (("COG", r.cog_path), ("mask", r.mask_path), ("STAC", r.stac_item_path)):
            if p and Path(p).exists():
                mb = Path(p).stat().st_size / 1e6
                print(f"   {label:5s} {mb:7.2f} MB  {p}", flush=True)
            else:
                print(f"   {label:5s}   --", flush=True)


if __name__ == "__main__":
    main()
