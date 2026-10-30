"""景质量度量：Tier 解析、AOI 窗口实测云量、选景排序。

背景：STAC 的 `eo:cloud_cover` 是整景（path/row 级，约 185×180 km）云量，
而 AOI 只占景的一角，二者可能差异极大（实测 0.97% vs 34.78%）。
因此选景必须用 **AOI 窗口内** 的 QA 实测云量。

只读 QA/SCL 瓦片（约 3–5 s/景），不下载光谱波段。
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np
import shapely
from shapely.geometry import box as shapely_box

from rspipe.bands import BandResolver
from rspipe.ingest.cogreader import CogReader
from rspipe.ingest.signing import needs_signing, sign_href
from rspipe.logging import get_logger
from rspipe.models import SceneMeta, SensorSpec

log = get_logger("rspipe.quality")


# ── Tier 解析 ────────────────────────────────────────────────────────────────
def parse_tier(scene_id: str, properties: dict[str, Any] | None = None) -> str | None:
    """解析 Landsat 数据级别（T1/T2）；非 Landsat 返回 None。

    优先级：`landsat:collection_category` 属性 > scene_id 的 `_T1/_T2` 后缀。
    """
    cat = (properties or {}).get("landsat:collection_category")
    if cat:
        m = re.match(r"^(T\d)", str(cat).upper())
        if m:
            return m.group(1)
    m = re.search(r"_(T[12])$", scene_id.strip().upper())
    if m:
        return m.group(1)
    return None


# ── AOI 云量实测 ─────────────────────────────────────────────────────────────

_CLOUD_CACHE: dict[tuple, float | None] = {}
"""AOI 实测云量进程内缓存：key=(scene_id, bbox 四舍五入)。

同一景 + 同一 AOI 的 QA 读取与位解码在本地/网络皆不便宜（5–20 s），
检索/预览/任务三类调用会重复触发，必须查表。
"""
_CLOUD_CACHE_MAX = 512


def _cloud_cache_key(scene_id: str, bbox: tuple[float, float, float, float]) -> tuple:
    return (scene_id, tuple(round(v, 3) for v in bbox))


def aoi_cloud_fraction(
    scene: SceneMeta,
    bbox: tuple[float, float, float, float],
    *,
    spec: SensorSpec | None = None,
    auth: Any = None,
) -> float | None:
    """计算 AOI（bbox, EPSG:4326）窗口内的云+云影占比（0..1）。

    返回 None 表示无法度量（无 QA 资产 / 无法读取）。
    只读 QA 瓦片，不下载光谱波段；结果按 (scene_id, bbox) 进程内缓存。
    """
    key = _cloud_cache_key(scene.scene_id, tuple(bbox))
    if key in _CLOUD_CACHE:
        return _CLOUD_CACHE[key]
    val = _aoi_cloud_uncached(scene, bbox, spec=spec, auth=auth)
    _CLOUD_CACHE[key] = val
    if len(_CLOUD_CACHE) > _CLOUD_CACHE_MAX:  # 粗略 LRU：无处可查时丢最早 64 条
        for old in list(_CLOUD_CACHE)[:64]:
            _CLOUD_CACHE.pop(old, None)
    return val


def _aoi_cloud_uncached(
    scene: SceneMeta,
    bbox: tuple[float, float, float, float],
    *,
    spec: SensorSpec | None = None,
    auth: Any = None,
) -> float | None:
    resolver = BandResolver(scene, spec)
    qa_asset = resolver.qa_asset()
    if qa_asset is None:
        return None

    href = qa_asset.href
    try:
        if needs_signing(href):
            href = sign_href(href)
    except Exception:
        log.debug("quality.sign-failed", scene=scene.scene_id)

    import tempfile
    from pathlib import Path

    from rspipe.ingest.fetcher import fetch_asset
    from rspipe.process.clouds import build_mask

    try:
        with tempfile.TemporaryDirectory() as tmp:
            local = fetch_asset(
                qa_asset,
                Path(tmp) / f"{scene.scene_id}.qa.tif",
                auth=auth,
                mode=qa_asset.fetch_mode,
                bbox=bbox,
            )
            text = str(local)
            if text.startswith(("http://", "https://")):
                with CogReader(text) as reader:
                    col, row, w, h = reader.window_for_bbox(bbox)
                    if w <= 0 or h <= 0:
                        return None
                    qa = reader.read_window(col, row, w, h)
            else:
                import rasterio

                with rasterio.open(text) as src:
                    qa = src.read(1)

        qa_int = np.asarray(qa)
        if qa_int.dtype.kind == "f":
            qa_int = np.where(np.isfinite(qa_int), qa_int, 0)
        qa_int = qa_int.astype("uint16" if qa_int.max(initial=0) > 255 else "uint8")

        kind = _mask_kind(qa_asset.key, qa_asset.roles)
        result = build_mask(qa_int, kind=kind, spec=spec, buffer_pixels=0)
        return float(1.0 - result.clear_fraction)
    except Exception as exc:
        log.debug("quality.aoi-cloud-failed", scene=scene.scene_id, error=str(exc)[:80])
        return None


def _mask_kind(asset_key: str, roles: Sequence[str]) -> str:
    joined = f"{asset_key} {' '.join(roles)}".lower()
    if "scl" in joined or "scene_classification" in joined:
        return "scl"
    return "qa_pixel"


# ── 重叠度 ───────────────────────────────────────────────────────────────────
def aoi_overlap(scene: SceneMeta, bbox: tuple[float, float, float, float]) -> float:
    """景 footprint 与 AOI bbox 的重叠占比（0..1）。"""
    aoi = shapely_box(*bbox)
    if aoi.area <= 0:
        return 0.0
    if scene.geometry is not None:
        try:
            geom = shapely.from_geojson(json.dumps(scene.geometry))
        except Exception:
            geom = None
        if geom is not None and geom.is_valid:
            return float(geom.intersection(aoi).area / aoi.area)
    # 无 geometry：退化用 scene.bbox 的矩形
    if scene.bbox and any(scene.bbox):
        sb = shapely_box(*scene.bbox)
        if sb.is_valid:
            return float(sb.intersection(aoi).area / aoi.area)
    return 0.0


# ── 选景排序 ─────────────────────────────────────────────────────────────────
@dataclass(slots=True)
class RankedScene:
    """带质量评分的场景。"""

    scene: SceneMeta
    tier: str | None
    aoi_cloud: float | None
    overlap: float
    score: float  # 越小越好

    @property
    def tier_penalty(self) -> float:
        return 0.0 if (self.tier in (None, "T1")) else 1.0


def rank_scenes(
    scenes: Sequence[SceneMeta],
    bbox: tuple[float, float, float, float],
    *,
    spec: SensorSpec | None = None,
    auth: Any = None,
    prefer_tier1: bool = True,
    min_overlap: float = 0.0,
    max_workers: int = 6,
) -> list[RankedScene]:
    """按选景质量排序：Tier1 优先 > AOI 实测云量升序 > 重叠度降序。

    `min_overlap` 过滤掉与 AOI 重叠过小的景（如 0.3 = 至少 30%）。
    AOI 云量实测是网络拉 QA 瓦片的主代价，`max_workers` 并行共享之。
    """
    from concurrent.futures import ThreadPoolExecutor

    cand: list[tuple[SceneMeta, float, str | None]] = []
    for s in scenes:
        ov = aoi_overlap(s, bbox)
        if ov <= min_overlap:
            continue
        cand.append((s, ov, parse_tier(s.scene_id, s.properties)))

    if not cand:
        log.info("quality.ranked", total=len(scenes), kept=0, best=None, best_aoi_cloud=None)
        return []

    def measure(s: SceneMeta) -> float | None:
        return aoi_cloud_fraction(s, bbox, spec=spec, auth=auth)

    clouds: list[float | None]
    if len(cand) == 1:
        clouds = [measure(cand[0][0])]
    else:
        with ThreadPoolExecutor(max_workers=max(1, max_workers), thread_name_prefix="rsq") as ex:
            clouds = list(ex.map(measure, [c[0] for c in cand]))

    ranked: list[RankedScene] = []
    for (s, ov, tier), ac in zip(cand, clouds, strict=False):
        tier_pen = (0.0 if tier in (None, "T1") else 1.0) if prefer_tier1 else 0.0
        cloud_v = ac if ac is not None else (s.cloud_cover if s.cloud_cover is not None else 100.0)
        ranked.append(
            RankedScene(
                scene=s,
                tier=tier,
                aoi_cloud=ac,
                overlap=ov,
                score=tier_pen * 1000.0 + cloud_v * 10.0 - ov,
            )
        )

    ranked.sort(key=lambda r: r.score)
    log.info(
        "quality.ranked",
        total=len(scenes),
        kept=len(ranked),
        best=ranked[0].scene.scene_id if ranked else None,
        best_aoi_cloud=round(ranked[0].aoi_cloud, 2) if ranked and ranked[0].aoi_cloud is not None else None,
    )
    return ranked


def format_ranked(ranked: Sequence[RankedScene]) -> str:
    """可读的排行表文本。"""
    lines = [
        f"{'scene':38s} {'tier':4s} {'AOI云%':>7s} {'景云%':>6s} {'重叠%':>6s}",
        "-" * 68,
    ]
    for r in ranked:
        tier = r.tier or "-"
        ac = f"{r.aoi_cloud*100:.2f}" if r.aoi_cloud is not None else "n/a"
        sc = f"{r.scene.cloud_cover:.2f}" if r.scene.cloud_cover is not None else "-"
        lines.append(
            f"{r.scene.scene_id:38s} {tier:4s} {ac:>7s} {sc:>6s} {r.overlap*100:5.1f}%"
        )
    return "\n".join(lines)


__all__ = [
    "RankedScene",
    "aoi_cloud_fraction",
    "aoi_overlap",
    "format_ranked",
    "parse_tier",
    "rank_scenes",
]
