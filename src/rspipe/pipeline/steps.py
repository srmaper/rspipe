"""单景端到端处理步骤。

流程（由 `ProductProfile` 决定哪些步骤生效）：
    fetch -> [unpack] -> stack -> [radiometric_normalize|harmonize] -> [reproject]
          -> [cog_convert] -> cloud_mask -> stac_publish

每步产物存在即跳过（幂等）。
"""

from __future__ import annotations

import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import rasterio
from affine import Affine

from rspipe.auth import AuthLike, NoAuth
from rspipe.bands import BandResolver
from rspipe.config import PipelineConfig
from rspipe.errors import (
    CogError,
    MetadataError,
    NormalizeError,
    RspipeError,
    StepSkippedError,
)
from rspipe.ingest.fetcher import fetch_asset
from rspipe.ingest.unpack import find_metadata, unpack_archive
from rspipe.logging import get_logger
from rspipe.models import (
    AssetRef,
    FetchMode,
    ProductProfile,
    SceneMeta,
    SceneResult,
    SensorSpec,
    StepResult,
)
from rspipe.normalize.cog import to_cog
from rspipe.normalize.harmonize import harmonize, normalize_dn
from rspipe.normalize.io import write_array
from rspipe.normalize.profile import plan_steps, profile_from_scene
from rspipe.normalize.reflectance import resolve_scaling
from rspipe.process.clouds import apply_mask, build_mask, write_mask
from rspipe.storage.layout import DataLayout

log = get_logger("rspipe.pipeline.steps")


@dataclass(slots=True)
class SceneContext:
    """单景处理的共享上下文。"""

    scene: SceneMeta
    config: PipelineConfig
    layout: DataLayout
    auth: AuthLike = field(default_factory=NoAuth)
    spec: SensorSpec | None = None
    band_names: Sequence[str] = field(default_factory=tuple)
    collection_id: str = "reflectance"
    workdir: Path | None = None
    aoi_bbox: tuple[float, float, float, float] | None = None
    metrics: dict[str, Any] = field(default_factory=dict)

    @property
    def scene_id(self) -> str:
        return self.scene.scene_id

    @property
    def staging_dir(self) -> Path:
        if self.workdir is not None:
            return self.workdir
        return self.layout.staging_scene_dir(self.scene_id)


class SceneProcessor:
    """执行一景影像的完整处理链。"""

    def __init__(self, ctx: SceneContext) -> None:
        self.ctx = ctx
        self.scene = ctx.scene
        self.cfg = ctx.config
        self.layout = ctx.layout
        self.profile: ProductProfile = profile_from_scene(ctx.scene)
        self.plans = plan_steps(self.profile)
        self._scaling: Any = None

    # ── 主入口 ────────────────────────────────────────────────────────────
    def run(self, *, force: bool = False) -> SceneResult:
        result = SceneResult(scene_id=self.scene.scene_id, profile=self.profile)
        started = time.perf_counter()

        for plan in self.plans:
            step_start = time.perf_counter()
            try:
                if not force and self._skip_if_done(plan.name):
                    raise StepSkippedError(f"{plan.name}: output exists")
                outputs = self._dispatch(plan.name)
                result.steps.append(
                    StepResult(
                        name=plan.name,
                        status="ok",
                        outputs=outputs,
                        elapsed_s=time.perf_counter() - step_start,
                    )
                )
            except StepSkippedError as exc:
                result.steps.append(
                    StepResult(
                        name=plan.name,
                        status="skipped",
                        elapsed_s=time.perf_counter() - step_start,
                        message=str(exc),
                    )
                )
            except RspipeError as exc:
                log.error("step.failed", scene=self.scene.scene_id, step=plan.name, error=str(exc))
                result.steps.append(
                    StepResult(
                        name=plan.name,
                        status="failed",
                        elapsed_s=time.perf_counter() - step_start,
                        message=str(exc),
                    )
                )
                result.status = "failed"
                result.error = f"{plan.name}: {exc}"
                return result

        result.status = "ok"
        result.cog_path = self._cog_path()
        result.mask_path = self._mask_path() if self._mask_path().exists() else None
        item_path = self.layout.stac_item_path(self.ctx.collection_id, self.scene.scene_id)
        result.stac_item_path = item_path if item_path.exists() else None
        m = self.ctx.metrics
        result.aoi_cloud_cover = m.get("rspi:aoi_cloud_cover")
        result.valid_fraction = m.get("rspi:valid_fraction")

        log.info(
            "scene.done",
            scene=self.scene.scene_id,
            status=result.status,
            elapsed_s=round(time.perf_counter() - started, 2),
        )
        return result

    # ── 步骤分发 ──────────────────────────────────────────────────────────
    def _dispatch(self, name: str) -> list[Path]:
        handler = getattr(self, f"_step_{name}", None)
        if handler is None:
            raise NormalizeError(f"no handler for step {name!r}")
        return handler()

    def _skip_if_done(self, name: str) -> bool:
        """幂等：关键步骤的产物已存在则跳过。"""
        if name == "cog_convert":
            return self._cog_path().exists()
        if name == "cloud_mask":
            return self._mask_path().exists()
        if name == "stac_publish":
            return self.layout.stac_item_path(
                self.ctx.collection_id, self.scene.scene_id
            ).exists()
        return False

    # ── 产物路径 ──────────────────────────────────────────────────────────
    def _cog_path(self) -> Path:
        return self.layout.cog_path(self.scene.scene_id)

    def _mask_path(self) -> Path:
        return self.layout.mask_path(self.scene.scene_id)

    def _stack_path(self) -> Path:
        return self.ctx.staging_dir / f"{self.scene.scene_id}.stack.tif"

    def _norm_path(self) -> Path:
        return self.ctx.staging_dir / f"{self.scene.scene_id}.norm.tif"

    # ── 步骤实现 ──────────────────────────────────────────────────────────
    def _step_unpack(self) -> list[Path]:
        raw_dir = self.layout.ensure_scene(self.scene.provider, self.scene.scene_id)
        if not self.scene.raw_href:
            raise NormalizeError(f"scene {self.scene.scene_id}: no raw archive href")
        src = Path(fetch_asset(
            AssetRef(key="product", href=self.scene.raw_href, fetch_mode=FetchMode.DOWNLOAD),
            raw_dir / Path(self.scene.raw_href).name,
            auth=self.ctx.auth,
        ))
        root = unpack_archive(src, self.ctx.staging_dir)
        meta = find_metadata(root)
        if meta is not None:
            self._metadata_path = meta
        return [root]

    def _step_stack(self) -> list[Path]:
        resolver = BandResolver(self.scene, self.ctx.spec)
        assets = resolver.data_assets(self.ctx.band_names)
        if not assets:
            raise MetadataError(
                f"scene {self.scene.scene_id}: no resolvable band assets for "
                f"{list(self.ctx.band_names)}"
            )
        paths: list[str] = []
        for common in self.ctx.band_names:
            asset = assets.get(common)
            if asset is None:
                raise MetadataError(f"scene {self.scene.scene_id}: missing band {common!r}")
            fetched = fetch_asset(
                asset,
                self.ctx.staging_dir / f"{self.scene.scene_id}.{common}.tif",
                auth=self.ctx.auth,
                mode=asset.fetch_mode,
                bbox=self._bbox_or_none(),
            )
            paths.append(fetched)

        out = self._stack_path()
        _stack_paths(paths, out, bbox=self._read_bbox())
        return [out]

    def _step_radiometric_normalize(self) -> list[Path]:
        scaling = self._get_scaling()
        out = self._norm_path()
        normalize_dn(
            self._stack_path(),
            out,
            band_names=list(self.ctx.band_names),
            scaling=scaling,
        )
        return [out]

    def _step_harmonize(self) -> list[Path]:
        scaling = self._get_scaling()
        out = self._norm_path()
        harmonize(
            self._stack_path(),
            out,
            band_names=list(self.ctx.band_names),
            scaling=scaling,
            spec=self.ctx.spec,
            radiometry=self.profile.radiometry,
        )
        return [out]

    def _step_reproject(self) -> list[Path]:
        target_crs = self.cfg.normalize.target_crs
        if not target_crs:
            raise StepSkippedError("reproject: no target CRS configured")
        from rspipe.normalize.io import reproject_to_grid

        src = self._norm_path() if self._norm_path().exists() else self._stack_path()
        out = self.ctx.staging_dir / f"{self.scene.scene_id}.reproj.tif"
        reproject_to_grid(
            src,
            out,
            crs=target_crs,
            resolution=self.cfg.normalize.target_resolution,
            nodata=float("nan"),
        )
        return [out]

    def _step_cog_convert(self) -> list[Path]:
        src = self._norm_path() if self._norm_path().exists() else self._stack_path()
        if not src.exists():
            raise CogError(f"scene {self.scene.scene_id}: no input raster for COG conversion")
        out = self._cog_path()
        to_cog(src, out, band_names=list(self.ctx.band_names))
        return [out]

    def _step_cloud_mask(self) -> list[Path]:
        resolver = BandResolver(self.scene, self.ctx.spec)
        qa_asset = resolver.qa_asset()
        if qa_asset is None:
            log.warning("cloud_mask.no-qa", scene=self.scene.scene_id)
            return []

        qa_path = fetch_asset(
            qa_asset,
            self.ctx.staging_dir / f"{self.scene.scene_id}.qa.tif",
            auth=self.ctx.auth,
            mode=qa_asset.fetch_mode,
            bbox=self._bbox_or_none(),
        )
        qa, transform, crs, _nodata = _read_band_windowed(qa_path, self._read_bbox())
        # QA/SCL 是位掩膜或类别码，必须保持整型才能做位运算
        qa = qa.astype("uint16" if qa.max() > 255 else "uint8")

        kind = self._qa_kind()
        cm = self.cfg.cloud_mask
        result = build_mask(
            qa,
            kind=kind,
            spec=self.ctx.spec,
            buffer_pixels=cm.buffer_pixels,
            keep_snow=cm.keep_snow,
            keep_water=cm.keep_water,
        )

        out = self._mask_path()
        write_mask(result.mask, out, transform=transform, crs=crs)

        # 把掩膜对齐到 COG 网格后应用（QA/SCL 常为 20m，COG 波段为 10m）
        cog = self._cog_path()
        if cog.exists():
            with rasterio.open(str(cog)) as src:
                arr = src.read()
                profile = src.profile.copy()
            mask_aligned = _resample_mask_to(result.mask, transform, arr.shape[1:])
            masked = apply_mask(arr, mask_aligned)
            tmp = cog.with_suffix(".masked.tif")
            write_array(
                masked,
                tmp,
                transform=profile["transform"],
                crs=profile["crs"],
                nodata=float("nan"),
                dtype="float32",
                band_names=list(self.ctx.band_names),
            )
            # 重新编码为 COG：write_array 产出的是普通 GTiff，直接覆盖会破坏 COG 布局
            from rspipe.normalize.cog import to_cog

            to_cog(
                tmp,
                cog,
                band_names=list(self.ctx.band_names),
                nodata=float("nan"),
            )
            tmp.unlink(missing_ok=True)
            log.info(
                "cloud_mask.applied",
                scene=self.scene.scene_id,
                clear=float(result.clear_fraction),
                mask_shape=list(result.mask.shape),
                cog_shape=list(arr.shape[1:]),
            )
            # 采集 AOI 质量指标，供 STAC 回填与运行报告
            valid_frac = float(mask_aligned.mean())
            self.ctx.metrics.update(
                {
                    "rspi:scene_cloud_cover": self.scene.cloud_cover,
                    "rspi:aoi_cloud_cover": float(1.0 - result.clear_fraction),
                    "rspi:valid_fraction": valid_frac,
                    "rspi:tier": self.scene.tier,
                    "rspi:aoi_bbox": list(self.ctx.aoi_bbox) if self.ctx.aoi_bbox else None,
                }
            )
        return [out]

    def _step_color_balance(self) -> list[Path]:
        # 单景阶段不调整；匀色在镶嵌阶段对景集合统一做
        raise StepSkippedError("color_balance: deferred to mosaic stage")

    def _step_mosaic(self) -> list[Path]:
        raise StepSkippedError("mosaic: runs in mosaic stage")

    def _step_stac_publish(self) -> list[Path]:
        from rspipe.stac.catalog import CatalogPublisher
        from rspipe.stac.items import build_item, validate_item
        from rspipe.stac.store import StacIndex, record_from_item_dict

        cog = self._cog_path()
        if not cog.exists():
            raise CogError(f"scene {self.scene.scene_id}: COG missing, cannot publish STAC")

        item = build_item(
            scene=self.scene,
            cog_path=cog,
            mask_path=self._mask_path() if self._mask_path().exists() else None,
            collection_id=self.ctx.collection_id,
            properties_extra=dict(self.ctx.metrics) if self.ctx.metrics else None,
        )
        ok = validate_item(item)
        if not ok:
            log.warning("stac.item.invalid", scene=self.scene.scene_id)

        publisher = CatalogPublisher(self.layout.catalog)
        item_path = publisher.add_item(item, collection_id=self.ctx.collection_id)

        index = StacIndex(self.layout.catalog_index)
        index.upsert(record_from_item_dict(item.to_dict(), href=str(item_path)))
        return [item_path]

    # ── 辅助 ──────────────────────────────────────────────────────────────
    def _get_scaling(self) -> Any:
        if self._scaling is not None:
            return self._scaling
        spec = self.ctx.spec
        if spec is None:
            from rspipe.normalize.reflectance import ScalingSet

            self._scaling = ScalingSet(by_band={}, source="identity")
            return self._scaling
        self._scaling = resolve_scaling(
            spec=spec,
            bands=list(self.ctx.band_names),
            scene=self.scene,
            mtl_path=getattr(self, "_metadata_path", None),
        )
        return self._scaling

    def _bbox_or_none(self) -> tuple[float, float, float, float] | None:
        bbox = self.scene.bbox
        if bbox and any(bbox):
            return bbox
        return None

    def _read_bbox(self) -> tuple[float, float, float, float] | None:
        """读取窗口：优先 ctx.aoi_bbox（CLI --bbox），其次 cfg AOI，再次景 bbox。

        只取窗口可大幅减少网络传输（By CogReader 按 COG 瓦片 Range 拉取）。
        """
        if self.ctx.aoi_bbox is not None:
            return _intersect(self.scene.bbox, self.ctx.aoi_bbox) or self.ctx.aoi_bbox
        aoi = self.cfg.pipeline.aoi
        if aoi:
            geom_bbox = _aoi_bbox(Path(aoi))
            if geom_bbox is not None:
                return _intersect(self.scene.bbox, geom_bbox) or geom_bbox
        return self._bbox_or_none()

    def _qa_kind(self) -> str:
        resolver = BandResolver(self.scene, self.ctx.spec)
        qa = resolver.qa_asset()
        if qa is None:
            return "none"
        key = qa.key.lower()
        if "scl" in key or "scene_classification" in key:
            return "scl"
        return "qa_pixel"


def _stack_paths(
    paths: Sequence[str | Path],
    out: Path,
    *,
    bbox: tuple[float, float, float, float] | None = None,
) -> Path:
    """把多个单波段栅格堆叠为多波段 GeoTIFF（要求同网格）。

    `paths` 元素可以是本地路径、`/vsicurl/...` VSI 字符串或 http(s) URL。
    远程 URL 走 `CogReader`（只拉取命中瓦片），本地走 rasterio。
    `bbox`（EPSG:4326）非空时只读取该窗口。
    """
    if not paths:
        raise NormalizeError("stack: empty input")

    arrays = []
    ref_profile: dict[str, Any] | None = None
    for p in paths:
        arr, transform, crs, nodata = _read_band_windowed(p, bbox)
        arrays.append(arr)
        if ref_profile is None:
            ref_profile = {"transform": transform, "crs": crs, "nodata": nodata}

    assert ref_profile is not None
    stack = np.stack(arrays, axis=0)
    write_array(
        stack,
        out,
        transform=ref_profile["transform"],
        crs=ref_profile["crs"],
        nodata=ref_profile.get("nodata"),
        dtype=str(stack.dtype),
    )
    return out


def _read_band_windowed(
    src: str | Path,
    bbox: tuple[float, float, float, float] | None,
) -> tuple[np.ndarray, Any, Any, float | None]:
    """读取单波段窗口，返回 (array, transform, crs, nodata)。"""
    from rspipe.ingest.cogreader import CogReader

    text = str(src)
    if text.startswith(("http://", "https://")):
        # 远程 COG：只取命中瓦片（必要时先做 SAS 签名）
        from rspipe.ingest.signing import needs_signing, sign_href

        url = sign_href(text) if needs_signing(text) else text
        with CogReader(url) as reader:
            if bbox is not None:
                col0, row0, w, h = reader.window_for_bbox(bbox)
            else:
                col0, row0, w, h = 0, 0, reader.info.width, reader.info.height
            if w <= 0 or h <= 0:
                raise NormalizeError(f"empty window for bbox {bbox}")
            data = reader.read_window(col0, row0, w, h).astype("float32")
            a, b, c, d, e, f = reader.info.transform or (1, 0, 0, 0, -1, 0)
            transform = Affine(a, b, c, d, e, f) * Affine.translation(col0, row0)
            crs = f"EPSG:{reader.info.epsg}" if reader.info.epsg else None
            log.debug(
                "band.window",
                window=(col0, row0, w, h),
                epsg=reader.info.epsg,
                bytes=reader.stats["range_file"]["bytes_read"],
            )
        return data, transform, crs, None

    # 本地 / VSI：走 rasterio
    from rspipe.ingest.fetcher import open_raster

    with open_raster(src), rasterio.open(str(src)) as ds:
        window = _window_for_bbox(ds, bbox) if bbox else None
        if window is not None:
            data = ds.read(1, window=window).astype("float32")
            transform = ds.window_transform(window)
        else:
            data = ds.read(1).astype("float32")
            transform = ds.transform
        return data, transform, ds.crs, ds.nodata


def _resample_mask_to(
    mask: np.ndarray,
    src_transform: Any,
    out_shape: tuple[int, int],
) -> np.ndarray:
    """把掩膜最近邻缩放到目标网格尺寸（QA/SCL 常与波段分辨率不同）。

    同一 CRS、仅分辨率不同时等价于按比例取最近邻，无需重投影。
    """
    out_h, out_w = int(out_shape[0]), int(out_shape[1])
    if mask.shape == (out_h, out_w):
        return mask
    if mask.size == 0 or out_h <= 0 or out_w <= 0:
        return np.zeros((out_h, out_w), dtype=bool)

    src_h, src_w = mask.shape
    rows = (np.arange(out_h) * src_h / out_h).astype(int)
    cols = (np.arange(out_w) * src_w / out_w).astype(int)
    rows = np.clip(rows, 0, src_h - 1)
    cols = np.clip(cols, 0, src_w - 1)
    return mask[rows][:, cols].astype(bool)


def _window_for_bbox(src: Any, bbox: tuple[float, float, float, float]) -> Any | None:
    """计算 bbox 对应的读取窗口；无交集返回 None。"""
    from rasterio.windows import from_bounds

    minx, miny, maxx, maxy = bbox
    b = src.bounds
    if not (minx < b.right and maxx > b.left and miny < b.top and maxy > b.bottom):
        return None
    try:
        w = from_bounds(minx, miny, maxx, maxy, transform=src.transform)
    except Exception:
        return None
    w = w.round_offsets().round_lengths()
    if w.width <= 0 or w.height <= 0:
        return None
    return w


def _aoi_bbox(path: Path) -> tuple[float, float, float, float] | None:
    """从 GeoJSON 文件提取 bbox。"""
    import json

    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None

    coords: list[list[float]] = []

    def walk(node: Any) -> None:
        if isinstance(node, (list, tuple)):
            if node and isinstance(node[0], (int, float)):
                coords.append([float(node[0]), float(node[1])])
            else:
                for child in node:
                    walk(child)

    if data.get("type") == "FeatureCollection":
        for f in data.get("features") or []:
            walk((f.get("geometry") or {}).get("coordinates"))
    elif data.get("type") in ("Polygon", "MultiPolygon"):
        walk(data.get("coordinates"))
    if not coords:
        return None
    xs = [c[0] for c in coords]
    ys = [c[1] for c in coords]
    return (min(xs), min(ys), max(xs), max(ys))


def _intersect(
    a: tuple[float, float, float, float] | None,
    b: tuple[float, float, float, float] | None,
) -> tuple[float, float, float, float] | None:
    if a is None:
        return b
    if b is None:
        return a
    inter = (max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3]))
    if inter[0] >= inter[2] or inter[1] >= inter[3]:
        return None
    return inter


__all__ = ["SceneContext", "SceneProcessor"]
