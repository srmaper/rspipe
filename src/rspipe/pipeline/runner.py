"""流水线编排：增量发现 -> 单景处理 -> 匀色 -> 镶嵌 -> 状态推进。"""

from __future__ import annotations

import math
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from rspipe.clients.base import ProviderClient
from rspipe.clients.factory import build_enabled_clients
from rspipe.config import PipelineConfig, Settings
from rspipe.errors import ConfigError, PipelineError, RspipeError
from rspipe.logging import get_logger
from rspipe.models import SceneMeta, SceneResult
from rspipe.pipeline.state import PipelineState, ProcessedRecord
from rspipe.pipeline.steps import SceneContext, SceneProcessor
from rspipe.storage.layout import DataLayout

log = get_logger("rspipe.pipeline.runner")


@dataclass(slots=True)
class PipelineReport:
    """一次 run 的汇总报告。"""

    run_id: str
    started_at: datetime
    finished_at: datetime | None = None
    status: str = "ok"
    discovered: int = 0
    processed: int = 0
    skipped: int = 0
    failed: int = 0
    scenes: list[SceneResult] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def summary(self) -> str:
        return (
            f"run={self.run_id} discovered={self.discovered} "
            f"processed={self.processed} skipped={self.skipped} failed={self.failed}"
        )


class Pipeline:
    """rspipe 主编排器。

    用法：
        pipe = Pipeline.from_config(config, settings)
        report = pipe.run_once()
        # 或常驻调度
        pipe.schedule()
    """

    def __init__(
        self,
        config: PipelineConfig,
        settings: Settings,
        *,
        clients: dict[str, ProviderClient] | None = None,
    ) -> None:
        self.config = config
        self.settings = settings
        self.layout = DataLayout(config.data_root)
        self.layout.ensure()
        self.state = PipelineState(self.layout.runs_db)
        self.clients = clients if clients is not None else build_enabled_clients(config, settings)
        # CLI 覆盖：--bbox 传入时的检索范围
        self._cli_bbox: tuple[float, float, float, float] | None = None

    @classmethod
    def from_config(
        cls,
        config: PipelineConfig | None = None,
        settings: Settings | None = None,
        **kwargs: Any,
    ) -> Pipeline:
        from rspipe.config import load_pipeline_config

        cfg = config or load_pipeline_config()
        st = settings or Settings()
        return cls(cfg, st, **kwargs)

    # ── 检索 ──────────────────────────────────────────────────────────────
    def discover(self, *, force_full: bool = False) -> list[SceneMeta]:
        """按水位线增量检索新景。"""
        if self._cli_bbox is not None:
            aoi_geom, aoi_bbox = None, self._cli_bbox
        else:
            aoi_geom, aoi_bbox = self._load_aoi()
        aoi_id = self._aoi_id()
        now = datetime.now(UTC)
        end = self._parse_dt(self.config.pipeline.end) or now

        found: list[SceneMeta] = []
        for name, client in self.clients.items():
            collections = self.config.provider(name).collections or [""]
            for coll in collections:
                start = None
                if not force_full:
                    start = self.state.search_start(name, coll, aoi_id)
                if start is None:
                    start = end - timedelta(days=self.config.schedule.lookback_days * 30)
                    start = max(start, self._parse_dt(self.config.pipeline.start) or start)

                log.info(
                    "discover.search",
                    provider=name,
                    collection=coll,
                    start=start.isoformat(),
                    end=end.isoformat(),
                )
                try:
                    scenes = client.search(
                        bbox=aoi_bbox,
                        geometry=aoi_geom if aoi_bbox is None else None,
                        start=start,
                        end=end,
                        max_cloud_cover=self.config.pipeline.max_cloud_cover,
                        max_items=self.config.schedule.max_scenes_per_run,
                    )
                except RspipeError as exc:
                    log.warning("discover.failed", provider=name, error=str(exc))
                    continue

                for s in scenes:
                    s.provider = name
                    if not s.collection:
                        s.collection = coll
                found.extend(scenes)
                self.state.advance_watermark(
                    name,
                    coll,
                    aoi_id,
                    end,
                    lookback_days=self.config.schedule.lookback_days,
                )

        # 幂等去重：已处理过的跳过
        seen: set[str] = set()
        unique: list[SceneMeta] = []
        for s in found:
            if s.scene_id in seen:
                continue
            seen.add(s.scene_id)
            if self.state.is_processed(s.provider, s.scene_id):
                continue
            unique.append(s)

        log.info("discover.done", matched=len(found), new=len(unique))
        return unique

    def _load_aoi(self) -> tuple[dict[str, Any] | None, tuple[float, float, float, float] | None]:
        path = self.config.pipeline.aoi
        if not path:
            return None, None
        p = Path(path)
        if not p.exists():
            raise ConfigError(f"AOI file not found: {p}")
        import json

        data = json.loads(p.read_text(encoding="utf-8"))
        if data.get("type") == "FeatureCollection":
            feats = data.get("features") or []
            if not feats:
                return None, None
            geom = feats[0].get("geometry")
            bbox = _geom_bbox(geom)
            return geom, bbox
        if data.get("type") in ("Polygon", "MultiPolygon"):
            return data, _geom_bbox(data)
        return None, None

    def _aoi_id(self) -> str:
        path = self.config.pipeline.aoi
        return Path(path).stem if path else "default"

    @staticmethod
    def _parse_dt(value: str | None) -> datetime | None:
        if not value:
            return None
        text = str(value).replace("Z", "+00:00")
        dt = datetime.fromisoformat(text)
        return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt

    # ── 单景处理 ──────────────────────────────────────────────────────────
    def process_scene(self, scene: SceneMeta, *, force: bool = False) -> SceneResult:
        spec = self._spec_for(scene)
        aoi_bbox = self._cli_bbox if self._cli_bbox is not None else self._load_aoi()[1]
        ctx = SceneContext(
            scene=scene,
            config=self.config,
            layout=self.layout,
            auth=self._auth_for(scene.provider),
            spec=spec,
            band_names=tuple(self.config.pipeline.bands),
            collection_id=self.config.pipeline.name or "reflectance",
            aoi_bbox=aoi_bbox,
        )
        processor = SceneProcessor(ctx)
        result = processor.run(force=force)

        self.state.mark_processed(
            ProcessedRecord(
                scene_id=scene.scene_id,
                provider=scene.provider,
                collection=scene.collection,
                processed_at=datetime.now(UTC),
                cog_path=str(result.cog_path) if result.cog_path else None,
                item_path=str(result.stac_item_path) if result.stac_item_path else None,
                status=result.status,
                error=result.error,
            )
        )
        return result

    def _spec_for_any(self, scenes: Sequence[SceneMeta]) -> Any:
        for s in scenes:
            spec = self._spec_for(s)
            if spec is not None:
                return spec
        return None

    def _spec_for_client(self, provider: str) -> Any:
        """为 CLI 报表取该数据源的代表性 SensorSpec。"""
        for key in self.config.provider(provider).collections or [""]:
            text = key.lower()
            for k, spec in self.config.sensors.items():
                if any(a.lower() in text for a in spec.alias) or k in text:
                    return spec
        return next(iter(self.config.sensors.values()), None)

    def _spec_for(self, scene: SceneMeta) -> Any:
        key = scene.sensor_key
        if key and key in self.config.sensors:
            return self.config.sensors[key]
        # 按 collection 关键字回退
        text = f"{scene.platform or ''} {scene.collection} {scene.scene_id}".lower()
        for k, spec in self.config.sensors.items():
            if any(a.lower() in text for a in spec.alias):
                return spec
            if k.replace("-", "").replace("_", "") in text.replace("-", "").replace("_", ""):
                return spec
        return None

    def _auth_for(self, provider: str) -> Any:
        client = self.clients.get(provider)
        return getattr(client, "_auth", None) or None

    # ── 一次性运行 ────────────────────────────────────────────────────────
    def run_once(
        self,
        *,
        force_full: bool = False,
        force: bool = False,
        aoi_cloud_max: float | None = None,
    ) -> PipelineReport:
        run = self.state.start_run()
        report = PipelineReport(run_id=run.run_id, started_at=run.started_at)

        try:
            scenes = self.discover(force_full=force_full)
        except RspipeError as exc:
            report.status = "failed"
            report.errors.append(str(exc))
            self.state.finish_run(run, status="failed", message=str(exc))
            return report

        if aoi_cloud_max is not None and self._cli_bbox is not None:
            from rspipe.quality import rank_scenes

            ranked = rank_scenes(
                scenes,
                self._cli_bbox,
                auth=self._auth_for(next(iter(self.clients), "")),
                prefer_tier1=True,
                min_overlap=0.01,
            )
            thresh = aoi_cloud_max
            scenes = [
                r.scene for r in ranked
                if r.aoi_cloud is not None and r.aoi_cloud <= thresh
            ]
            log.info(
                "run.filter-aoi-cloud",
                max=aoi_cloud_max,
                kept=len(scenes),
            )
        elif aoi_cloud_max is not None:
            from rspipe.quality import rank_scenes

            aoi_bbox = self._load_aoi()[1]
            ranked = rank_scenes(
                scenes,
                aoi_bbox if aoi_bbox is not None else self._sz_default,
            )
            scenes = [r.scene for r in ranked if (r.aoi_cloud or 0) <= aoi_cloud_max]

        report.discovered = len(scenes)
        for scene in scenes[: self.config.schedule.max_scenes_per_run]:
            try:
                result = self.process_scene(scene, force=force)
            except RspipeError as exc:
                report.failed += 1
                report.errors.append(f"{scene.scene_id}: {exc}")
                log.error("scene.failed", scene=scene.scene_id, error=str(exc))
                continue

            report.scenes.append(result)
            if result.status == "ok":
                report.processed += 1
            elif result.status == "failed":
                report.failed += 1
                if result.error:
                    report.errors.append(f"{scene.scene_id}: {result.error}")
            else:
                report.skipped += 1

        report.finished_at = datetime.now(UTC)
        run.scenes_found = report.discovered
        run.scenes_ok = report.processed
        run.scenes_failed = report.failed
        status = "ok" if report.failed == 0 else "partial"
        self.state.finish_run(run, status=status, message=report.summary)

        log.info("run.summary", **{"run": report.summary})
        return report

    def run_scene_ids(self, ids: Sequence[str]) -> PipelineReport:
        """直接处理指定 scene_id（跳过水位线/发现）。

        逐数据源用 STAC `ids=` 检索；找不到的记入 skipped。
        """
        run = self.state.start_run()
        report = PipelineReport(run_id=run.run_id, started_at=run.started_at)
        wanted = list(dict.fromkeys(ids))
        found: dict[str, SceneMeta] = {}
        for name, client in self.clients.items():
            try:
                scenes = client.search(ids=wanted, max_items=len(wanted))
            except RspipeError as exc:
                log.warning("run_scene_ids.search-failed", provider=name, error=str(exc))
                continue
            for s in scenes:
                s.provider = name
                # first-found-wins：配置顺序靠前的数据源（公开端点）优先，
                # 避免被需凭证的数据源（如 usgs-landsat 登录页）覆盖
                found.setdefault(s.scene_id, s)

        report.discovered = len(wanted)
        for sid in wanted:
            scene = found.get(sid)
            if scene is None:
                report.skipped += 1
                report.errors.append(f"{sid}: not found on any enabled provider")
                continue
            try:
                result = self.process_scene(scene)
            except RspipeError as exc:
                report.failed += 1
                report.errors.append(f"{sid}: {exc}")
                log.error("scene.failed", scene=sid, error=str(exc))
                continue
            report.scenes.append(result)
            if result.status == "ok":
                report.processed += 1
            elif result.status == "failed":
                report.failed += 1
                if result.error:
                    report.errors.append(f"{sid}: {result.error}")
            else:
                report.skipped += 1

        report.finished_at = datetime.now(UTC)
        run.scenes_found = report.discovered
        run.scenes_ok = report.processed
        run.scenes_failed = report.failed
        status = "ok" if report.failed == 0 else "partial"
        self.state.finish_run(run, status=status, message=report.summary)
        log.info("run.summary", **{"run": report.summary})
        return report

    _sz_default: tuple[float, float, float, float] = (113.76, 22.41, 114.62, 22.87)

    # ── 镶嵌 ──────────────────────────────────────────────────────────────
    def build_mosaic(
        self,
        *,
        scene_ids: Sequence[str] | None = None,
        date: str | None = None,
        feather: str | None = None,
    ) -> Path:
        """对已处理的景做匀色 + 羽化镶嵌。

        `scene_ids` 省略时用全部已处理景（受 `max_scenes_per_run` 限制）。
        """
        from rspipe.process.color import balance_collection
        from rspipe.process.mosaic import MosaicSource, mosaic

        records = self.state.recent_processed(limit=self.config.schedule.max_scenes_per_run)
        if scene_ids:
            wanted = set(scene_ids)
            records = [r for r in records if r.scene_id in wanted]
        records = [r for r in records if r.status == "ok" and r.cog_path and Path(r.cog_path).exists()]

        if not records:
            raise PipelineError("no processed scenes available for mosaicking")

        arrays: dict[str, Any] = {}
        masks: dict[str, Any] = {}
        metas: dict[str, dict[str, Any]] = {}

        for rec in records:
            import rasterio

            with rasterio.open(rec.cog_path) as src:
                arr = src.read()
                valid = np_isfinite_all(arr[0])
                arrays[rec.scene_id] = arr.astype("float32")
                masks[rec.scene_id] = valid
                metas[rec.scene_id] = {
                    "cloud_cover": self._cloud_of(rec.scene_id),
                    "valid_fraction": float(valid.mean()),
                }

        band_names = list(self.config.pipeline.bands)
        ref_id = None
        if self.config.color_balance.reference not in ("best", "first"):
            ref_id = self.config.color_balance.reference

        balanced, report = balance_collection(
            arrays,
            band_names=band_names,
            reference_id=ref_id,
            method=self.config.color_balance.method,  # type: ignore[arg-type]
            quantiles=self.config.color_balance.quantiles,
            valid_masks=masks,
        )
        log.info(
            "mosaic.balance",
            reference=report.reference_id,
            method=report.method,
            skipped=report.skipped,
        )

        sources = [
            MosaicSource(
                scene_id=sid,
                data=balanced[sid],
                mask=masks[sid],
                weight_boost=1.0 - (metas[sid]["cloud_cover"] or 0.0) / 100.0,
            )
            for sid in balanced
        ]
        result = mosaic(
            sources,
            feather=feather or self.config.mosaic.feather,  # type: ignore[arg-type]
            blend_distance=self.config.mosaic.blend_distance,
            dtype=self.config.mosaic.dtype,
        )

        aoi_id = self._aoi_id()
        date_key = date or datetime.now(UTC).strftime("%Y%m%d")
        out_dir = self.layout.mosaic_dir(aoi_id, date_key)
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = self.layout.mosaic_path(aoi_id, date_key)

        from rspipe.normalize.io import write_array

        transform, _width, _height = _grid_from_records(records)
        write_array(
            result.data,
            out_path,
            transform=transform,
            crs=_crs_from_records(records),
            nodata=float("nan"),
            dtype=self.config.mosaic.dtype,
            band_names=band_names,
        )
        log.info("mosaic.saved", out=str(out_path), sources=len(sources))
        return out_path

    def _cloud_of(self, scene_id: str) -> float | None:
        from rspipe.stac.store import StacIndex

        index = StacIndex(self.layout.catalog_index)
        for coll in index.collections():
            rec = index.get(coll, scene_id)
            if rec is not None:
                return rec.cloud_cover
        return None

    # ── 调度 ──────────────────────────────────────────────────────────────
    def schedule(self) -> None:
        """按 `schedule.cron` 常驻运行。"""
        from rspipe.pipeline.scheduler import run_scheduler

        run_scheduler(self)


# ── 模块级工具 ───────────────────────────────────────────────────────────────
def np_isfinite_all(arr: Any) -> Any:
    import numpy as np

    return np.isfinite(arr)


def _geom_bbox(geom: dict[str, Any] | None) -> tuple[float, float, float, float] | None:
    if not geom:
        return None
    coords = geom.get("coordinates") or []
    xs: list[float] = []
    ys: list[float] = []

    def walk(node: Any) -> None:
        if isinstance(node, (list, tuple)):
            if node and isinstance(node[0], (int, float)):
                xs.append(float(node[0]))
                ys.append(float(node[1]))
            else:
                for child in node:
                    walk(child)

    walk(coords)
    if not xs:
        return None
    return (min(xs), min(ys), max(xs), max(ys))


def _grid_from_records(records: Sequence[Any]) -> tuple[Any, int, int]:
    import rasterio
    from rasterio.transform import from_origin

    minx = miny = float("inf")
    maxx = maxy = float("-inf")
    res = 0.0
    for rec in records:
        with rasterio.open(rec.cog_path) as src:
            b = src.bounds
            minx = min(minx, b.left)
            miny = min(miny, b.bottom)
            maxx = max(maxx, b.right)
            maxy = max(maxy, b.top)
            res = max(res, abs(src.transform.a), abs(src.transform.e))
    if not math.isfinite(res) or res == 0:
        res = 0.001
    width = max(1, round((maxx - minx) / res))
    height = max(1, round((maxy - miny) / res))
    return from_origin(minx, maxy, res, res), width, height


def _crs_from_records(records: Sequence[Any]) -> Any:
    import rasterio

    with rasterio.open(records[0].cog_path) as src:
        return src.crs


def _uuid() -> str:
    return uuid.uuid4().hex[:12]


__all__ = ["Pipeline", "PipelineReport"]
