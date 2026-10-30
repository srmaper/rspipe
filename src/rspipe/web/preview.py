"""整景预览：本地 raw 波段 / 云端 COG 金字塔层 直接出瓦片（不下载、不产 COG）。

关键点：瓦片常常只有一部分落在影像范围内。必须按“瓦片 bbox ∩ 影像范围”的比例，
把交集读出来贴到 256×256 画布的正确子区域；否则窗口被压成 1~3 像素再拉伸，
会出现横条纹/渐变伪影。
"""

from __future__ import annotations

import contextlib
import io
import time
from pathlib import Path

import numpy as np
from PIL import Image

from rspipe.ingest.cogreader import CogReader
from rspipe.ingest.signing import needs_signing, sign_href
from rspipe.web.tiles import _ndvi_lut, _stretch_rgb, plan_tile_window, tile_bounds

_SCENE_TTL = 600.0
_READER_TTL = 900.0
_scenes: dict[str, tuple[float, object]] = {}
_readers: dict[tuple[str, str], tuple[float, CogReader]] = {}

_KEYS = {
    "rgb": ("red", "green", "blue"),
    "false": ("nir", "red", "green"),
    "ndvi": ("red", "nir"),
}

TILE = 256


# ── 场景/读取器缓存 ──────────────────────────────────────────────────────────
def _get_scene(pipe, provider: str, scene_id: str):
    hit = _scenes.get(scene_id)
    if hit and time.time() - hit[0] < _SCENE_TTL:
        return hit[1]
    scenes = pipe.clients[provider].search(ids=[scene_id], max_items=1)
    if not scenes:
        return None
    s = scenes[0]
    s.provider = provider
    _scenes[scene_id] = (time.time(), s)
    return s


def _get_reader(pipe, provider: str, scene_id: str, key: str) -> CogReader | None:
    ck = (scene_id, key)
    hit = _readers.get(ck)
    if hit and time.time() - hit[0] < _READER_TTL:
        return hit[1]
    scene = _get_scene(pipe, provider, scene_id)
    if scene is None:
        return None
    from rspipe.bands import BandResolver

    asset = BandResolver(scene, pipe._spec_for(scene)).try_asset_for(key)
    if asset is None:
        return None
    href = asset.href
    try:
        if needs_signing(href):
            href = sign_href(href)
    except Exception:
        pass
    try:
        r = CogReader(href, block_size=1 << 18)
    except Exception:
        return None
    _readers[ck] = (time.time(), r)
    if len(_readers) > 8:
        oldest = min(_readers, key=lambda k: _readers[k][0])
        with contextlib.suppress(Exception):
            _readers.pop(oldest)[1].close()
    return r


# ── 几何：瓦片 ∩ 影像 → (dst 子矩形, src 像素窗口) ───────────────────────────
def _dn_to_refl(a: np.ndarray, collection: str) -> np.ndarray:
    f = a.astype("float32")
    f = f * 2.75e-05 - 0.2 if "landsat" in collection else f / 10000.0
    f[f <= 0] = np.nan
    return f


# ── 按景缓存的拉伸区间（逐瓦片各自拉伸会造成拼接花斑） ──────────────────────
_RANGE_CACHE: dict[tuple, dict[str, tuple[float, float]]] = {}


def _ranges_for_raw(raw_dir: Path, keys, scale: str) -> dict[str, tuple[float, float]]:
    """整景低分辨率采样 → 每波段稳定的 2%/98% 拉伸区间（按景缓存）。"""
    import rasterio

    ck = (str(raw_dir), tuple(keys), scale)
    hit = _RANGE_CACHE.get(ck)
    if hit is not None:
        return hit
    out: dict[str, tuple[float, float]] = {}
    for k in keys:
        f = raw_dir / f"{raw_dir.name}.{k}.tif"
        if not f.exists():
            continue
        try:
            with rasterio.open(f) as src:
                arr = src.read(1, out_shape=(128, 128)).astype("float32")
            arr = _dn_to_refl(arr, scale)
            v = arr[np.isfinite(arr)]
            if v.size:
                lo, hi = np.percentile(v, [2, 98])
                out[k] = (float(lo), float(hi))
        except Exception:
            continue
    if len(_RANGE_CACHE) > 64:
        _RANGE_CACHE.clear()
    _RANGE_CACHE[ck] = out
    return out


def _stretch_fixed(ranges, keys, stacks) -> tuple[np.ndarray, np.ndarray]:
    """用固定的按景区间拉伸，保证相邻瓦片颜色一致。"""
    stack = np.stack(stacks)
    valid = np.isfinite(stack).all(axis=0) & (stack.sum(axis=0) > 0)
    out = np.zeros((3, *valid.shape), dtype="float32")
    for i, k in enumerate(keys):
        v = stacks[i]
        lo, hi = ranges.get(k, (0.0, 0.30))
        if hi <= lo:
            hi = lo + 1e-6
        out[i] = np.where(valid, np.clip((v - lo) / (hi - lo) * 255.0, 0, 255), 0)
    return out, valid


def _compose(channels: str, stacks: list[np.ndarray]) -> tuple[np.ndarray, np.ndarray]:
    if channels == "ndvi":
        red, nir = stacks[0], stacks[1]
        with np.errstate(invalid="ignore"):
            nd = (nir - red) / (nir + red)
        valid = np.isfinite(nd) & ((nir + red) > 0)
        rgb = _ndvi_lut(np.clip(nd, -1, 1), valid)
    else:
        stack = np.stack(stacks)
        valid = np.isfinite(stack).all(axis=0) & (stack.sum(axis=0) > 0)
        rgb = _stretch_rgb(stack, valid)
    return rgb, valid


def _png(rgb: np.ndarray, valid: np.ndarray) -> bytes | None:
    """RGBA 输出：无值区全透明；有效像元 <1% 视为空瓦片（返回 None → 204）。"""
    if valid.mean() < 0.002:
        return None
    rgba = np.zeros((TILE, TILE, 4), dtype="uint8")
    rgba[..., :3] = rgb.transpose(1, 2, 0).astype("uint8")
    rgba[..., 3] = np.where(valid, 255, 0).astype("uint8")
    img = Image.fromarray(rgba, mode="RGBA")
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


# ── 本地 raw 波段直出 ────────────────────────────────────────────────────────
def render_local_tile(
    data_root: Path,
    provider: str,
    collection: str,
    scene_id: str,
    z: int,
    x: int,
    y: int,
    channels: str = "rgb",
) -> bytes | None:
    import rasterio
    from rasterio.enums import Resampling
    from rasterio.windows import Window

    keys = _KEYS.get(channels, _KEYS["rgb"])
    raw_dir = Path(data_root) / "raw" / provider / scene_id
    if not raw_dir.is_dir():
        return None
    paths = {}
    for k in keys:
        f = raw_dir / f"{scene_id}.{k}.tif"
        if not f.exists():
            return None
        paths[k] = f

    b = tile_bounds(z, x, y)
    canvas = np.full((3, TILE, TILE), np.nan, dtype="float32")
    got_any = False
    for i, k in enumerate(keys):
        with rasterio.open(paths[k]) as src:
            plan = plan_tile_window(src.bounds, src.crs, src.transform, src.width, src.height, b)
            if plan is None:
                continue
            (dx0, dy0, dx1, dy1), (col, row, w, h) = plan
            arr = src.read(
                1,
                window=Window(col, row, w, h),
                out_shape=(dy1 - dy0, dx1 - dx0),
                resampling=Resampling.bilinear,
            )
            canvas[i, dy0:dy1, dx0:dx1] = _dn_to_refl(arr, collection)
            got_any = True
    if not got_any:
        return None
    stacks = [canvas[0], canvas[1], canvas[2]] if len(keys) == 3 else [canvas[0], canvas[1]]
    if channels == "ndvi":
        rgb, valid = _compose(channels, stacks)
    else:
        scale = "landsat" if "landsat" in collection else "s2"
        rgb, valid = _stretch_fixed(_ranges_for_raw(raw_dir, keys, scale), keys, stacks)
    return _png(rgb, valid)


# ── 云端 COG 金字塔层直出 ────────────────────────────────────────────────────
def render_remote_tile(
    pipe,
    provider: str,
    collection: str,
    scene_id: str,
    z: int,
    x: int,
    y: int,
    channels: str = "rgb",
) -> bytes | None:
    keys = _KEYS.get(channels, _KEYS["rgb"])
    b = tile_bounds(z, x, y)
    canvas = np.full((3, TILE, TILE), np.nan, dtype="float32")
    got_any = False
    for i, k in enumerate(keys):
        reader = _get_reader(pipe, provider, scene_id, k)
        if reader is None:
            return None
        info = reader.info
        bounds = info.bounds
        if bounds is None or info.epsg is None or info.transform is None:
            return None
        # 先看瓦片大致需要多少像素，决定金字塔层
        from pyproj import Transformer

        tr = Transformer.from_crs(3857, f"EPSG:{info.epsg}", always_xy=True)
        xs, _ys = tr.transform([b[0], b[2]], [b[3], b[1]])
        need_px = abs(max(xs) - min(xs)) / abs(info.transform[0])
        level = reader.pick_level(int(need_px)) if reader.levels else 0
        lv = reader.levels[level] if reader.levels else None
        scale = (lv.width / info.width) if lv else 1.0
        w_lv = lv.width if lv else info.width
        h_lv = lv.height if lv else info.height
        plan = plan_tile_window(bounds, f"EPSG:{info.epsg}", info.transform, w_lv, h_lv, b, level_scale=scale)
        if plan is None:
            continue
        (dx0, dy0, dx1, dy1), (col, row, w, h) = plan
        arr = reader.read_window_level(level, col, row, w, h)
        if arr.size == 0:
            continue
        img = Image.fromarray(np.nan_to_num(arr.astype("float32"), nan=0.0), mode="F")
        img = img.resize((dx1 - dx0, dy1 - dy0), Image.BILINEAR)
        canvas[i, dy0:dy1, dx0:dx1] = _dn_to_refl(np.asarray(img, dtype="float32"), collection)
        got_any = True
    if not got_any:
        return None
    rgb, valid = _compose(channels, [canvas[0], canvas[1], canvas[2]] if len(keys) == 3 else [canvas[0], canvas[1]])
    return _png(rgb, valid)
