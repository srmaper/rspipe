"""瓦片渲染：本地成果 COG → 256×256 PNG（金字塔级选取 + 2% 拉伸 + 无值区留白）。

设计：
- COG 的 CRS 是 UTM（EPSG:326xx），Web Mercator XYZ 瓦片的 bbox 先转 UTM
  再 from_bounds 取窗口（近似等角，瓦片畸变 < 0.3%，城市级可忽略）。
- 大瓦片 z<15 → 用 overview 级（rasterio 窗口自动选）、取样像素 ≤256×256；
  z≥15 → bilinear 重采样到 256 方格。
- NaN / nodata → 255×255×255 白色（MapLibre 用 opacity0 遮罩无效区更简单：
  直接输出白底，产品 COG 里的 NaN 区域在影像上呈空白）。
"""

from __future__ import annotations

import io
import math
from pathlib import Path

import numpy as np
import rasterio
from PIL import Image
from rasterio.warp import Resampling
from rasterio.warp import transform as warp_transform
from rasterio.windows import Window, from_bounds

TILE = 256


def tile_bounds(z: int, x: int, y: int) -> tuple[float, float, float, float]:
    """Web-Mercator XYZ → (minx, miny, maxx, maxy) in EPSG:3857."""
    n = 1 << z
    d = 2.0 * math.pi * 6378137.0 / n
    minx = -math.pi * 6378137.0 + x * d
    maxx = minx + d
    maxy = math.pi * 6378137.0 - y * d
    miny = maxy - d
    return (minx, miny, maxx, maxy)




def plan_tile_window(bounds, crs, transform, width, height, b3857, level_scale=1.0):
    """瓦片 bbox(3857) 与栅格范围求交 → (dst 子矩形, src 像素窗口) 或 None。

    关键：比例必须在 **墨卡托(3857)空间** 计算——瓦片像素与 3857 米线性对应；
    若先转到 UTM 再按比例，纬度越高误差越大（z6 可达 20%，表现为低层级错位）。
    """
    from pyproj import Transformer

    fwd = Transformer.from_crs(3857, crs, always_xy=True)
    inv = Transformer.from_crs(crs, 3857, always_xy=True)

    # 栅格范围 → 3857（取四角）
    rx, ry = inv.transform([bounds[0], bounds[2]], [bounds[1], bounds[3]])
    rmx0, rmx1 = min(rx), max(rx)
    rmy0, rmy1 = min(ry), max(ry)

    # 3857 空间求交
    ix0, ix1 = max(b3857[0], rmx0), min(b3857[2], rmx1)
    iy0, iy1 = max(b3857[1], rmy0), min(b3857[3], rmy1)
    if ix1 <= ix0 or iy1 <= iy0:
        return None

    tw = b3857[2] - b3857[0]
    th = b3857[3] - b3857[1]
    if tw <= 0 or th <= 0:
        return None

    fx0, fx1 = (ix0 - b3857[0]) / tw, (ix1 - b3857[0]) / tw
    fy0, fy1 = (b3857[3] - iy1) / th, (b3857[3] - iy0) / th
    dx0, dx1 = round(fx0 * TILE), round(fx1 * TILE)
    dy0, dy1 = round(fy0 * TILE), round(fy1 * TILE)
    dx1 = max(dx0 + 1, min(TILE, dx1))
    dy1 = max(dy0 + 1, min(TILE, dy1))

    # 交集 → 栅格 CRS → 像素窗口
    cx, cy = fwd.transform([ix0, ix1], [iy1, iy0])
    left, right = min(cx), max(cx)
    top, bottom = min(cy), max(cy)
    tr_a, _tr_b, tr_c, _tr_d, tr_e, tr_f = tuple(transform)[:6]
    col0, col1 = sorted(((left - tr_c) / tr_a, (right - tr_c) / tr_a))
    row0, row1 = sorted(((top - tr_f) / tr_e, (bottom - tr_f) / tr_e))
    if level_scale != 1.0:
        col0, col1 = col0 * level_scale, col1 * level_scale
        row0, row1 = row0 * level_scale, row1 * level_scale
    ic0 = max(0, int(col0 // 1))
    ic1 = min(width, int(-(-col1 // 1)))
    ir0 = max(0, int(row0 // 1))
    ir1 = min(height, int(-(-row1 // 1)))
    if ic1 <= ic0 or ir1 <= ir0:
        return None
    return (dx0, dy0, dx1, dy1), (ic0, ir0, ic1 - ic0, ir1 - ir0)


def _window_for_tile(src: rasterio.io.DatasetReader, z: int, x: int, y: int):
    b3857 = tile_bounds(z, x, y)
    tx, ty = warp_transform("EPSG:3857", src.crs, [b3857[0], b3857[2]], [b3857[3], b3857[1]])
    left, right = (tx[0], tx[1]) if tx[0] < tx[1] else (tx[1], tx[0])
    bottom, top = (ty[0], ty[1]) if ty[0] < ty[1] else (ty[1], ty[0])
    desired = from_bounds(left, bottom, right, top, src.transform)
    # 手工 clamp 到栅格范围（rasterio 的 Window.intersection 对浮点窗易炸）
    col = max(0, min(src.width - 1, math.floor(desired.col_off)))
    row = max(0, min(src.height - 1, math.floor(desired.row_off)))
    w = max(0, min(src.width - col, math.ceil(desired.width + (desired.col_off - col))))
    h = max(0, min(src.height - row, math.ceil(desired.height + (desired.row_off - row))))
    return Window(col, row, w, h)


def _stretch_rgb(stack: np.ndarray, valid: np.ndarray) -> np.ndarray:
    """逐通道 2%~98% 线性拉伸到 0-255；无效区输出 255(白)。"""
    out = np.full(stack.shape, 255, dtype="float32")
    for i in range(stack.shape[0]):
        v = stack[i]
        vals = v[valid]
        if vals.size < 16:
            continue
        lo, hi = np.percentile(vals, [2, 98])
        if hi <= lo:
            hi = lo + 1e-6
        s = np.clip((v - lo) / (hi - lo) * 255.0, 0, 255)
        s[~valid] = 255
        out[i] = s
    return out


def render_tile(
    cog_path: Path,
    z: int,
    x: int,
    y: int,
    channels: str = "rgb",  # rgb | false | ndvi
) -> bytes | None:
    """渲染一张矢量-free 光栅 PNG；窗口完全在外则返回 None。"""
    with rasterio.open(cog_path) as src:
        win = _window_for_tile(src, z, x, y)
        if win.width < 1 or win.height < 1:
            return None

        # 限制读取像素总量（缩放前后都在 256 量级）
        cap = TILE * TILE
        scale = min(1.0, math.sqrt(cap / max(1.0, win.width * win.height)))
        out_h = max(1, int(win.height * scale))
        out_w = max(1, int(win.width * scale))

        if channels == "ndvi":
            r = src.read(1, window=win, out_shape=(out_h, out_w), resampling=Resampling.bilinear).astype("float32")
            n = src.read(4, window=win, out_shape=(out_h, out_w), resampling=Resampling.bilinear).astype("float32")
            with np.errstate(invalid="ignore"):
                nd = (n - r) / (n + r)
            valid = np.isfinite(nd) & (r + n > 0)
            nd = np.where(valid, np.clip(nd, -1, 1), np.nan)
            rgb = _ndvi_lut(nd, valid)
        else:
            # rgb: 1,2,3；false(假彩): nir,red,green → 4,1,2
            idx = [1, 2, 3] if channels == "rgb" else [4, 1, 2]
            stack = src.read(idx, window=win, out_shape=(3, out_h, out_w), resampling=Resampling.bilinear)
            stack = stack.astype("float32")
            if stack_views_are_dn(stack):  # 兜底：万一传入的是 DN 而非反射率
                stack = stack / 10000.0
            valid = np.isfinite(stack[0]) & np.isfinite(stack[1]) & np.isfinite(stack[2])
            rgb = _stretch_rgb(stack, valid)

        img = Image.fromarray(rgb.transpose(1, 2, 0).astype("uint8"), mode="RGB")
        buf = io.BytesIO()
        img.save(buf, format="PNG", optimize=True)
        return buf.getvalue()


def stack_views_are_dn(stack: np.ndarray) -> bool:
    """成果 COG 均是 float 反射率（0-1 量级）；粗值 DN 理论不出现，防御兜底。"""
    s = float(np.nanmax(stack[np.isfinite(stack)])) if np.isfinite(stack).any() else 0.0
    return s > 100.0


def _ndvi_lut(ndvi: np.ndarray, valid: np.ndarray) -> np.ndarray:
    """NDVI -1..1 → 调色板（低=棕黄, 高=绿）。"""
    out = np.full((3, *ndvi.shape), 255, dtype="float32")
    t = np.where(valid, np.clip((ndvi + 1) / 2, 0, 1), 1.0)
    # 颜色停靠点: t=0 → 棕(139,110,80), t=0.5 → 草黄(230,215,120), t=1 → 深绿(0,120,55)
    stops = [
        (139.0, 110.0, 80.0),
        (230.0, 215.0, 120.0),
        (0.0, 120.0, 55.0),
    ]
    lo_t = np.clip(t * 2, 0, 1)
    hi_t = np.clip(t * 2 - 1, 0, 1)
    for c in range(3):
        c01 = stops[0][c] + (stops[1][c] - stops[0][c]) * lo_t
        c12 = stops[1][c] + (stops[2][c] - stops[1][c]) * hi_t
        col = np.where(t <= 0.5, c01, c12)
        out[c] = np.where(valid, col, 255.0)
    return out


# ── COG 按景拉伸缓存（逐瓦片拉伸会造成拼接花斑） ────────────────────────────
_COG_RANGE_CACHE: dict[str, dict[int, tuple[float, float]]] = {}


def _cog_ranges(cog_path: Path) -> dict[int, tuple[float, float]]:
    """对 COG 低分辨率采样，得到每波段稳定的 2%/98% 区间（按景缓存）。"""
    import rasterio

    key = str(cog_path)
    hit = _COG_RANGE_CACHE.get(key)
    if hit is not None:
        return hit
    out: dict[int, tuple[float, float]] = {}
    try:
        with rasterio.open(cog_path) as src:
            integer_store = src.dtypes[0] in ("uint16", "int16")
            for bi in range(1, min(4, src.count) + 1):
                arr = src.read(bi, out_shape=(128, 128)).astype("float32")
                if integer_store:                 # 预览栅格按 rho*10000 存储
                    arr = arr / 10000.0
                    arr[arr <= 0] = np.nan
                v = arr[np.isfinite(arr)]
                if v.size:
                    lo, hi = np.percentile(v, [2, 98])
                    out[bi] = (float(lo), float(hi))
    except Exception:
        return out
    if len(_COG_RANGE_CACHE) > 64:
        _COG_RANGE_CACHE.clear()
    _COG_RANGE_CACHE[key] = out
    return out


def render_cog_tile_subrect(cog_path: Path, z: int, x: int, y: int, channels: str = "rgb") -> bytes | None:
    """COG 瓦片：按交集子矩形拼到 256 画布，无值区透明；有效像元<1% 返回 None。"""
    import io

    import numpy as np
    import rasterio
    from PIL import Image
    from rasterio.enums import Resampling
    from rasterio.windows import Window

    idx = {"rgb": (1, 2, 3), "false": (4, 1, 2), "ndvi": (1, 4)}.get(channels, (1, 2, 3))
    b = tile_bounds(z, x, y)
    with rasterio.open(cog_path) as src:
        plan = plan_tile_window(src.bounds, src.crs, src.transform, src.width, src.height, b)
        if plan is None:
            return None
        (dx0, dy0, dx1, dy1), (col, row, w, h) = plan
        chans = []
        for bi in idx:
            arr = src.read(
                bi,
                window=Window(col, row, w, h),
                out_shape=(dy1 - dy0, dx1 - dx0),
                resampling=Resampling.bilinear,
            ).astype("float32")
            chans.append(arr)

    if channels == "ndvi":
        red, nir = chans[0], chans[1]
        with np.errstate(invalid="ignore"):
            nd = (nir - red) / (nir + red)
        valid = np.isfinite(nd) & ((nir + red) > 0)
        rgb = _ndvi_lut(np.clip(nd, -1, 1), valid)
    else:
        stack = np.stack(chans)
        valid = np.isfinite(stack).all(axis=0) & (stack.sum(axis=0) > 0)
        ranges = _cog_ranges(cog_path)
        rgb = np.zeros(stack.shape, dtype="float32")
        for i, bi in enumerate(idx):
            lo, hi = ranges.get(bi, (0.0, 0.30))
            if hi <= lo:
                hi = lo + 1e-6
            rgb[i] = np.where(valid, np.clip((stack[i] - lo) / (hi - lo) * 255.0, 0, 255), 0)
    if valid.mean() < 0.002:
        return None
    rgba = np.zeros((TILE, TILE, 4), dtype="uint8")
    rgba[..., :3] = rgb.transpose(1, 2, 0).astype("uint8")
    rgba[..., 3] = np.where(valid, 255, 0).astype("uint8")
    img = Image.fromarray(rgba, mode="RGBA")
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def render_raster_tile(raster_path: Path, z: int, x: int, y: int, channels: str = "rgb") -> bytes | None:
    """多波段栅格（1=red,2=green,3=blue,4=nir，反射率）→ 瓦片 PNG。

    与 COG/raw 路径共用几何与按景拉伸，只是数据源换成单文件（通常是 4326 预览栅格）。
    """
    import io

    import numpy as np
    import rasterio
    from PIL import Image
    from rasterio.enums import Resampling
    from rasterio.windows import Window

    idx = {"rgb": (1, 2, 3), "false": (4, 1, 2), "ndvi": (1, 4)}.get(channels, (1, 2, 3))
    with rasterio.open(raster_path) as src:
        if src.count < 3:
            return None
        plan = plan_tile_window(src.bounds, src.crs, src.transform, src.width, src.height, tile_bounds(z, x, y))
        if plan is None:
            return None
        (dx0, dy0, dx1, dy1), (col, row, w, h) = plan
        integer_store = src.dtypes[0] in ("uint16", "int16")
        chans = []
        for bi in idx:
            if bi > src.count:
                return None
            a = src.read(bi, window=Window(col, row, w, h), out_shape=(dy1 - dy0, dx1 - dx0),
                         resampling=Resampling.bilinear).astype("float32")
            if integer_store:
                a = a / 10000.0          # 预览栅格按 ρ×10000 存储
                a[a <= 0] = np.nan
            chans.append(a)

    canvas = np.full((3, TILE, TILE), np.nan, dtype="float32")
    for i, a in enumerate(chans):
        canvas[i, dy0:dy1, dx0:dx1] = a

    if channels == "ndvi":
        red, nir = canvas[0], canvas[1]
        with np.errstate(invalid="ignore"):
            nd = (nir - red) / (nir + red)
        valid = np.isfinite(nd) & ((nir + red) > 0)
        rgb = _ndvi_lut(np.clip(nd, -1, 1), valid)
    else:
        valid = np.isfinite(canvas).all(axis=0) & (canvas.sum(axis=0) > 0)
        ranges = _cog_ranges(raster_path)
        rgb = np.zeros(canvas.shape, dtype="float32")
        for i, bi in enumerate(idx):
            lo, hi = ranges.get(bi, (0.0, 0.30))
            if hi <= lo:
                hi = lo + 1e-6
            rgb[i] = np.where(valid, np.clip((canvas[i] - lo) / (hi - lo) * 255.0, 0, 255), 0)

    if valid.mean() < 0.002:
        return None
    rgba = np.zeros((TILE, TILE, 4), dtype="uint8")
    rgba[..., :3] = rgb.transpose(1, 2, 0).astype("uint8")
    rgba[..., 3] = np.where(valid, 255, 0).astype("uint8")
    img = Image.fromarray(rgba, mode="RGBA")
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()
