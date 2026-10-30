"""FastAPI 组装：检索 / 场景详情 / 瓦片 / 任务 + WS 进度 + 静态前端。"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import date, datetime
from pathlib import Path

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles

from rspipe.config import Settings, load_pipeline_config
from rspipe.quality import format_ranked, rank_scenes

from .jobs import JobCenter
from .schemas import JobReq, SceneOut, SearchReq
from .tiles import render_cog_tile_subrect, render_raster_tile

log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[3]  # 仓库根（web/app.py 在 src/rspipe/web/ 下）
DIST = ROOT / "web" / "dist"

PROVIDERS_MAP = {
    "earth-search": ("https://earth-search.aws.element84.com/v1", ["sentinel-2-l2a"]),
    "planetary-computer": ("https://planetarycomputer.microsoft.com/api/stac/v1", ["landsat-c2-l2"]),
}


def collection_of(scene_id: str, items) -> str:
    """从 STAC item json 推断 collection；失败按卫星名兜底。"""
    try:
        for p in items:
            data = json.loads(Path(p).read_text(encoding="utf-8"))
            c = data.get("collection") or ""
            if c:
                return c
    except Exception:
        pass
    return "sentinel-2-l2a" if scene_id.startswith("S2") else "landsat-c2-l2"


def _collection_of_scene(provider: str, scene_id: str) -> str:
    return "landsat-c2-l2" if scene_id.startswith("L") else "sentinel-2-l2a"


def _band_info(scene) -> list[dict]:
    """每个栅格资产的 key / 分辨率(m) / 数据类型。"""
    out = []
    for asset in scene.assets:
        if not asset.is_raster:
            continue
        rb = (asset.extra or {}).get("raster:bands") or []
        res = dtype = nodata = None
        if isinstance(rb, list) and rb and isinstance(rb[0], dict):
            res = rb[0].get("spatial_resolution")
            dtype = rb[0].get("data_type")
            nodata = rb[0].get("nodata")
        out.append({"key": asset.key, "resolution": res, "dtype": dtype, "nodata": nodata})
    return out


def _cog_bounds(data_root, scene_id: str) -> list[float] | None:
    """本地 processed COG 在 4326 下的范围。"""
    f = Path(data_root) / "derived" / "cog" / f"{scene_id}.tif"
    if not f.exists():
        return None
    try:
        import rasterio
        from pyproj import Transformer

        with rasterio.open(f) as src:
            b = src.bounds
            tr = Transformer.from_crs(src.crs, 4326, always_xy=True)
            xs, ys = tr.transform([b.left, b.right], [b.bottom, b.top])
        return [round(min(xs), 6), round(min(ys), 6), round(max(xs), 6), round(max(ys), 6)]
    except Exception:
        return None


def _raw_bounds(data_root, provider: str, scene_id: str) -> list[float] | None:
    """本地 raw 裁剪文件在 4326 下的实际范围（AOI 与景求交后的真实边界）。"""
    d = Path(data_root) / "raw" / provider / scene_id
    if not d.is_dir():
        return None
    for k in ("red", "green", "blue", "nir08", "nir"):
        f = d / f"{scene_id}.{k}.tif"
        if not f.exists():
            continue
        try:
            import rasterio
            from pyproj import Transformer

            with rasterio.open(f) as src:
                b = src.bounds
                tr = Transformer.from_crs(src.crs, 4326, always_xy=True)
                xs, ys = tr.transform([b.left, b.right], [b.bottom, b.top])
            return [round(min(xs), 6), round(min(ys), 6), round(max(xs), 6), round(max(ys), 6)]
        except Exception:
            return None
    return None


def _local_bands(data_root, provider: str, scene_id: str) -> list[str]:
    d = Path(data_root) / "raw" / provider / scene_id
    if not d.is_dir():
        return []
    return sorted(
        f.name.rsplit(".", 2)[-2]
        for f in d.glob("*.tif")
        if not f.name.endswith((".preview_4326.tif", ".composite.tif"))
    )


def _has_raw(data_root, provider: str, scene_id: str) -> bool:
    d = Path(data_root) / "raw" / provider / scene_id
    try:
        return d.is_dir() and any(f.suffix == ".tif" for f in d.iterdir())
    except Exception:
        return False


def _has_cog(data_root, scene_id: str) -> bool:
    return (Path(data_root) / "derived" / "cog" / f"{scene_id}.tif").exists()


def _parse_date(s: str | None) -> datetime | None:
    if not s:
        return None
    return datetime.combine(date.fromisoformat(s[:10]), datetime.min.time(), tzinfo=None)


def _buffer_non_polygon(geom: dict, meters: float) -> dict:
    """面要素原样返回；点/线要素转 EPSG:3857 打 meters 缓冲再回 4326。"""
    try:
        if geom.get("type") in ("Polygon", "MultiPolygon"):
            return geom
        import shapely.geometry as sg
        import shapely.ops
        from pyproj import Transformer

        sh = sg.shape(geom)
        fwd = Transformer.from_crs(4326, 3857, always_xy=True)
        rev = Transformer.from_crs(3857, 4326, always_xy=True)
        in_m = shapely.ops.transform(fwd.transform, sh)
        poly = in_m.buffer(meters, quad_segs=16)
        return sg.mapping(shapely.ops.transform(rev.transform, poly))
    except Exception:
        return geom


def _geoscope_boundary(q: str) -> dict | None:
    """参考项目同款 geoscope 边界接口（aida.citylaydar.com），形如 tiles.html。

    返回 {display, geometry, area_km2} 或 None（失败/未命中时不抛错，走后续兜底）。
    """
    import httpx

    try:
        r = httpx.post(
            "https://aida.citylaydar.com/geoscope/api/boundary",
            json={"location": q},
            timeout=30.0,
        )
        j = r.json()
    except Exception:
        return None
    gj = j.get("geojson") if isinstance(j, dict) else None
    if j.get("status") != "success" or not gj or not gj.get("features"):
        return None
    feats = [f for f in gj["features"] if f.get("geometry")]
    if not feats:
        return None
    best = max(feats, key=lambda f: bbox_area_km2(f["geometry"]))
    geom = best["geometry"]
    name = best.get("properties", {}).get("name") or q
    return {"display": name, "geometry": geom, "area_km2": bbox_area_km2(geom)}


def bbox_area_km2(geom: dict) -> float:
    """按 bbox 估算面积（km²）——遍历多 Polygon 全部外环。"""
    import math

    try:
        xs: list[float] = []
        ys: list[float] = []
        c = geom.get("coordinates")
        if geom.get("type") == "Polygon":
            polys = [c]
        elif geom.get("type") == "MultiPolygon":
            polys = c
        else:
            return 0.0
        for rings in polys:
            for pt in rings[0]:  # 外环
                xs.append(pt[0])
                ys.append(pt[1])
        if not xs:
            return 0.0
        mid = sum(ys) / len(ys)
        dx = (max(xs) - min(xs)) * 111.32 * math.cos(math.radians(mid))
        dy = (max(ys) - min(ys)) * 110.57
        return abs(dx * dy)
    except Exception:
        return 0.0


def _fmt(s: str | None) -> str | None:
    if not s:
        return None
    try:
        return date.fromisoformat(s[:10]).isoformat()
    except ValueError:
        return None


def create_app() -> FastAPI:
    app = FastAPI(title="rspipe web", version="0.1")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )
    cfg = load_pipeline_config("configs")
    settings = Settings(_env_file=str(ROOT / ".env") if (ROOT / ".env").exists() else None)
    center = JobCenter(cfg, settings)
    state = {"pipe": center._pipe, "cfg": cfg}

    # ---------- 检索 ----------
    @app.post("/api/search")
    def api_search(req: SearchReq) -> dict:
        pipe = state["pipe"]
        bbox = tuple(req.bbox)
        if not (bbox[0] < bbox[2] and bbox[1] < bbox[3]):
            raise HTTPException(422, f"bbox 需为 (minx,miny,maxx,maxy) 且有序：{bbox}")
        try:
            sd = _parse_date(req.start)
            ed = _parse_date(req.end)
        except ValueError as exc:
            raise HTTPException(422, f"日期格式应为 YYYY-MM-DD：{exc}") from exc
        if sd and ed and sd >= ed:
            raise HTTPException(422, f"start 须早于 end：{req.start} >= {req.end}")
        out = {"bbox": list(bbox), "groups": []}
        for prov, coll in req.providers:
            if prov not in PROVIDERS_MAP:
                continue
            client = pipe.clients[prov]
            try:
                scenes = client.search(
                    bbox=bbox,
                    start=sd,
                    end=ed,
                    max_cloud_cover=req.cloud,
                    max_items=50,
                )
            except Exception as exc:
                log.warning("search %s/%s failed: %s", prov, coll, exc)
                continue
            scenes = [s for s in scenes if s.collection == coll]
            ranked = rank_scenes(
                scenes,
                bbox,
                auth=pipe._auth_for(prov),
                prefer_tier1="landsat" in coll,
                min_overlap=req.min_overlap,
            )
            if not ranked and scenes:
                # 覆盖度全不达标时放宽门槛重新排名（榜单会如实显示覆盖度）
                ranked = rank_scenes(
                    scenes,
                    bbox,
                    auth=pipe._auth_for(prov),
                    prefer_tier1="landsat" in coll,
                    min_overlap=0.05,
                )
            group = {
                "provider": prov,
                "collection": coll,
                "total": len(scenes),
                "kept": len(ranked),
                "table": format_ranked(ranked[:20]),
                "scenes": [
                    SceneOut(
                        scene_id=r.scene.scene_id,
                        provider=prov,
                        collection=coll,
                        datetime=r.scene.datetime.isoformat(),
                        cloud_cover=r.scene.cloud_cover,
                        aoi_cloud=r.aoi_cloud,
                        overlap=r.overlap,
                        tier=r.tier,
                        footprint=r.scene.geometry,
                        bbox=list(r.scene.bbox) if r.scene.bbox else None,
                        has_raw=_has_raw(cfg.data_root, prov, r.scene.scene_id),
                        raw_bounds=_raw_bounds(cfg.data_root, prov, r.scene.scene_id),
                        cog_bounds=_cog_bounds(cfg.data_root, r.scene.scene_id),
                        has_cog=_has_cog(cfg.data_root, r.scene.scene_id),
                    ).model_dump()
                    for r in ranked[:20]
                ],
            }
            out["groups"].append(group)
        return out

    # ---------- 场景详情 ----------
    @app.get("/api/scenes/{provider}/{scene_id}")
    def api_scene(provider: str, scene_id: str) -> dict:
        pipe = state["pipe"]
        if provider not in PROVIDERS_MAP:
            raise HTTPException(404, f"unknown provider {provider}")
        for _coll in PROVIDERS_MAP[provider][1]:
            hits = pipe.clients[provider].search(ids=[scene_id], max_items=1)
            if hits:
                s = hits[0]
                return {
                    "scene_id": s.scene_id,
                    "provider": provider,
                    "collection": s.collection,
                    "datetime": s.datetime.isoformat(),
                    "cloud_cover": s.cloud_cover,
                    "tier": s.tier,
                    "footprint": s.geometry,
                    "bbox": list(s.bbox) if s.bbox else None,
                    "properties": {k: v for k, v in s.properties.items() if isinstance(v, (int, float, str, bool, list))},
                    "bands": [a.key for a in s.assets if a.is_raster],
                    "band_info": _band_info(s),
                    "local_bands": _local_bands(cfg.data_root, provider, scene_id),
                    "raw_bounds": _raw_bounds(cfg.data_root, provider, scene_id),
                    "cog_bounds": _cog_bounds(cfg.data_root, scene_id),
                }
        raise HTTPException(404, f"{scene_id} not found on {provider}")

    # ---------- AOI ----------
    @app.get("/api/aoi")
    def api_aoi() -> list[dict]:
        layout_configs = ROOT / "configs"
        out = []
        for f in sorted(layout_configs.glob("aoi_*.geojson")):
            try:
                g = json.loads(f.read_text(encoding="utf-8"))
                feat = g["features"][0]
                out.append({"id": feat.get("properties", {}).get("id", f.stem), "name": feat.get("properties", {}).get("name", f.stem), "geometry": feat["geometry"]})
            except Exception:
                continue
        return out

    # ---------- 任务 ----------
    @app.post("/api/jobs")
    def api_jobs(req: JobReq) -> dict:
        job = center.submit(req)
        return job.to_dict()

    @app.get("/api/jobs")
    def api_jobs_list() -> list[dict]:
        return center.snapshot()

    @app.delete("/api/jobs/{job_id}")
    def api_job_delete(job_id: str) -> dict:
        """终态任务的删除：清该景全部本地产物 + 从任务列表移除。"""
        job = center.get(job_id)
        if job is None:
            raise HTTPException(404, f"job {job_id} not found")
        if job.status in ("queued", "running"):
            raise HTTPException(409, "任务仍在排队/运行，不能删除")
        from rspipe.web.cleanup import delete_scene_data

        removed = delete_scene_data(Path(cfg.data_root), job.provider, job.scene_id)
        center.jobs.pop(job_id, None)
        return removed

    @app.websocket("/ws/jobs")
    async def ws_jobs(ws: WebSocket):
        await ws.accept()
        last = None
        try:
            while True:
                snap = center.snapshot()
                if snap != last:
                    await ws.send_text(json.dumps(snap))
                    last = snap
                await asyncio.sleep(1.0)
        except WebSocketDisconnect:
            pass
        except Exception:
            await ws.close()

    # ---------- 地名 → 边界（对齐 tiles.html 的地址搜索） ----------
    @app.post("/api/geocode")
    def api_geocode(body: dict) -> dict:
        import httpx

        q = (body.get("q") or "").strip()
        if not q:
            raise HTTPException(400, "empty query")
        # 主源：geoscope（参考项目同款，国内可达）；兜底：Nominatim
        geoscope = _geoscope_boundary(q)
        # 点/线要素 → 100 米缓冲区（bbox 落图需要面型；100m 城市级足够）
        if geoscope is not None:
            geoscope["geometry"] = _buffer_non_polygon(geoscope["geometry"], meters=100)
            return geoscope
        try:
            r = httpx.get(
                "https://nominatim.openstreetmap.org/search",
                params={"q": q, "format": "jsonv2", "polygon_geojson": 1, "limit": 1, "accept-language": "zh-CN"},
                headers={"User-Agent": "rspipe-web/0.1"},
                timeout=25.0,
            )
            hits = r.json()
        except Exception as exc:
            raise HTTPException(502, f"geocoder unreachable: {exc}") from exc
        if not hits:
            return {}
        h = hits[0]
        geom = h.get("geojson")
        if not geom:
            return {}
        area = bbox_area_km2(geom)
        return {"display": h.get("display_name") or q, "geometry": geom, "area_km2": round(area)}



    # ---------- 环境部署 / 服务控制 ----------
    @app.get("/api/system/status")
    def api_system_status() -> dict:
        from rspipe.web.system import env_status

        return env_status()

    @app.post("/api/system/setup")
    def api_system_setup() -> dict:
        from rspipe.web.system import start_setup

        return start_setup()

    @app.post("/api/system/restart")
    def api_system_restart() -> dict:
        from rspipe.web.system import restart_service

        return restart_service()

    # ---------- 影像分析（指数 / 变化） ----------
    @app.get("/api/analysis/scenes")
    def api_analysis_scenes() -> list[dict]:
        root = Path(cfg.data_root)
        out = []
        for prov_dir in sorted((root / "raw").iterdir()) if (root / "raw").is_dir() else []:
            if not prov_dir.is_dir():
                continue
            for scene_dir in sorted(prov_dir.iterdir()):
                if not scene_dir.is_dir():
                    continue
                bands = sorted(
                    f.name.rsplit(".", 2)[-2]
                    for f in scene_dir.glob("*.tif")
                    if not f.name.endswith((".preview_4326.tif", ".composite.tif"))
                )
                if not bands:
                    continue
                out.append({
                    "scene_id": scene_dir.name,
                    "provider": prov_dir.name,
                    "bands": bands,
                    "bounds": _raw_bounds(cfg.data_root, prov_dir.name, scene_dir.name),
                })
        return out

    @app.post("/api/analysis/run")
    def api_analysis_run(body: dict) -> dict:
        from rspipe.web.analysis import INDICES, classify_stats, compute_scene_index

        scene_id = (body.get("scene_id") or "").strip()
        provider = (body.get("provider") or "").strip()
        index = (body.get("index") or "ndvi").strip().lower()
        threshold = float(body.get("threshold", 0.0) or 0.0)
        if not scene_id or not provider:
            raise HTTPException(422, "scene_id / provider 必填")
        if index not in INDICES:
            raise HTTPException(422, f"不支持的指数：{index}")

        coll = _collection_of_scene(provider, scene_id)
        res = compute_scene_index(Path(cfg.data_root), provider, scene_id, coll, index)
        if not res.get("ok"):
            return res
        res["classify"] = classify_stats(Path(cfg.data_root), provider, scene_id, index, threshold)
        res["tiles"] = f"/api/analysis/tiles/{scene_id}/{index}/{{z}}/{{x}}/{{y}}.png"
        return res

    @app.post("/api/analysis/change")
    def api_analysis_change(body: dict) -> dict:
        from rspipe.web.analysis import INDICES, compute_change

        a = (body.get("scene_a") or "").strip()
        b = (body.get("scene_b") or "").strip()
        index = (body.get("index") or "ndvi").strip().lower()
        threshold = float(body.get("threshold", 0.1) or 0.1)
        provider = (body.get("provider") or "").strip()
        if not a or not b:
            raise HTTPException(422, "scene_a / scene_b 必填")
        if index not in INDICES:
            raise HTTPException(422, f"不支持的指数：{index}")
        coll = _collection_of_scene(provider, a) if provider else "sentinel-2-l2a"
        res = compute_change(Path(cfg.data_root), provider, a, b, coll, index, threshold)
        if res.get("ok"):
            res["tiles"] = f"/api/analysis/tiles/{res['key']}/{index}_change/{{z}}/{{x}}/{{y}}.png"
        return res

    @app.get("/api/analysis/tiles/{key}/{name}/{z}/{x}/{y}.png")
    def api_analysis_tile(key: str, name: str, z: int, x: int, y: int):
        from rspipe.web.analysis import render_index_tile

        data = render_index_tile(Path(cfg.data_root), key, name, z, x, y)
        if data is None:
            return Response(status_code=204)
        return Response(content=data, media_type="image/png", headers={"Cache-Control": "max-age=3600"})

    # ---------- 瓦片（本地 COG 优先；缺失时直接读云端金字塔层做整景预览） ----------
    @app.get("/api/tiles/{scene_id}/{z}/{x}/{y}.png")
    def api_tile(
        scene_id: str,
        z: int,
        x: int,
        y: int,
        channels: str = "rgb",
        provider: str = "",
        collection: str = "",
    ):
        cog = Path(cfg.data_root) / "derived" / "cog" / f"{scene_id}.tif"
        if cog.exists():
            data = render_cog_tile_subrect(cog, z, x, y, channels)
            if data is not None:
                return Response(content=data, media_type="image/png", headers={"Cache-Control": "max-age=604800"})
        # 下载后处理产物：合并 + 重投影到 4326 的预览栅格（快且无投影误差）
        if provider:
            pv = Path(cfg.data_root) / "raw" / provider / scene_id / f"{scene_id}.preview_4326.tif"
            if pv.exists():
                data = render_raster_tile(pv, z, x, y, channels)
                if data is not None:
                    return Response(content=data, media_type="image/png",
                                    headers={"Cache-Control": "max-age=604800"})
        if provider:
            # 只用已下载的本地 raw 波段；没有就 204（不回源云端）
            from rspipe.web.preview import render_local_tile

            try:
                data = render_local_tile(
                    Path(cfg.data_root), provider, collection or "", scene_id, z, x, y, channels
                )
            except Exception as exc:
                log.warning("local raw tile failed %s: %s", scene_id, exc)
                data = None
            if data is not None:
                return Response(content=data, media_type="image/png", headers={"Cache-Control": "max-age=604800"})
        return Response(status_code=204)

    # ---------- 场景打包导出（COG + STAC json + raw 波段全部） ----------
    @app.get("/api/export/{scene_id}")
    def api_export(scene_id: str):
        import zipfile

        root = Path(cfg.data_root)
        cog = root / "derived" / "cog" / f"{scene_id}.tif"
        raw_dirs = [d for d in (root / "raw").glob(f"*/{scene_id}") if d.is_dir()]
        items = sorted((root / "catalog").rglob(f"{scene_id}.json"))
        if not cog.exists() and not raw_dirs and not items:
            raise HTTPException(404, f"scene {scene_id}: 本地没有任何产物（raw/COG/STAC）")
        out_dir = root / "web_export"
        out_dir.mkdir(parents=True, exist_ok=True)
        dest = out_dir / f"{scene_id}.zip"
        # 缓存失效：源文件比 zip 新（例如后补了 COG）就重新打包
        src_mtimes = [p.stat().st_mtime for p in ([cog] if cog.exists() else []) + items]
        for raw_dir in raw_dirs[:1]:
            src_mtimes += [f.stat().st_mtime for f in raw_dir.glob("*.tif")]
        stale = dest.exists() and src_mtimes and dest.stat().st_mtime < max(src_mtimes)
        if not dest.exists() or stale:
            with zipfile.ZipFile(dest, "w", compression=zipfile.ZIP_STORED) as zf:
                if cog.exists():
                    zf.write(cog, arcname=f"{scene_id}/{scene_id}.cog.tif")
                for item in items:
                    zf.write(item, arcname=f"{scene_id}/{item.parent.name}/{item.name}")
                for raw_dir in raw_dirs[:1]:
                    for f in sorted(raw_dir.glob("*.tif")):
                        if f.name.endswith(".aux.xml") or f.name.endswith(".preview_4326.tif"):
                            continue  # 预览栅格在根目录单独放，避免重复
                        zf.write(f, arcname=f"{scene_id}/raw/{f.name}")
                    pv = raw_dir / f"{scene_id}.preview_4326.tif"
                    if pv.exists():
                        zf.write(pv, arcname=f"{scene_id}/{scene_id}.preview_4326.tif")
                    aoi = raw_dir / "aoi.geojson"
                    if aoi.exists():
                        zf.write(aoi, arcname=f"{scene_id}/aoi.geojson")
                # 快视图 + 合成反射率 tif + 说明文件
                from rspipe.web.export import make_composite, make_quicklook, make_readme

                coll = collection_of(scene_id, items)
                try:
                    if raw_dirs:
                        png = make_quicklook(raw_dirs[0], coll)
                        if png:
                            zf.writestr(f"{scene_id}/quicklook.png", png)
                        comp_dir = out_dir / "_composite"
                        comp_dir.mkdir(parents=True, exist_ok=True)
                        comp = comp_dir / f"{scene_id}.composite.tif"
                        if comp.exists():
                            comp.unlink()
                        made = make_composite(raw_dirs[0], coll, comp)
                        if made is not None:
                            zf.write(made, arcname=f"{scene_id}/{scene_id}.composite.tif")
                    zf.writestr(
                        f"{scene_id}/README.txt",
                        make_readme(
                            scene_id,
                            raw_dirs[0].parent.name if raw_dirs else "",
                            coll,
                            raw_dirs[0] if raw_dirs else None,
                            cog.exists(),
                        ),
                    )
                except Exception as exc:
                    log.warning("export extras failed %s: %s", scene_id, exc)
        return FileResponse(str(dest), media_type="application/zip", filename=f"{scene_id}.zip")

    # ---------- 静态前端 ----------
    if DIST.exists():
        app.mount("/", StaticFiles(directory=str(DIST), html=True), name="web")
    else:
        @app.get("/")
        def root() -> dict:
            return {"web": "dist 未构建 —— cd web && npm run build"}

    return app


app = create_app()


def main() -> None:  # python -m rspipe.web
    import uvicorn

    uvicorn.run("rspipe.web.app:app", host="0.0.0.0", port=8000, log_level="info")


if __name__ == "__main__":
    main()
