"""轻量 COG 窗口读取器：只按需拉取命中的瓦片。

GDAL 的 GTiff 驱动在 `open()` 阶段会把远程 COG 整个读完（实测 232 MB / 888 块），
在慢网络下不可接受。这里直接解析 TIFF 结构，只取窗口命中的瓦片：

    reader = CogReader("https://.../B04.tif")
    arr = reader.read_window(col_off, row_off, width, height)

支持：BitsPerSample 8/16/32/64、SampleFormat uint/int/float、
Compression 1(none)/8(deflate)、Predictor 1(none)/2(horizontal)。
其它编码（LZW/LERC/JPEG）会显式报错，提示改用 `download` 模式。
"""

from __future__ import annotations

import struct
import zlib
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from rspipe.errors import DownloadError
from rspipe.ingest.rangefile import RangeFile
from rspipe.logging import get_logger

log = get_logger("rspipe.ingest.cogreader")

# TIFF tag
TAG_IMAGE_WIDTH = 256
TAG_IMAGE_LENGTH = 257
TAG_BITS_PER_SAMPLE = 258
TAG_COMPRESSION = 259
TAG_PHOTOMETRIC = 262
TAG_SAMPLES_PER_PIXEL = 277
TAG_PLANAR_CONFIG = 284
TAG_PREDICTOR = 317
TAG_TILE_WIDTH = 322
TAG_TILE_LENGTH = 323
TAG_TILE_OFFSETS = 324
TAG_TILE_BYTE_COUNTS = 325
TAG_SAMPLE_FORMAT = 339

# TIFF type -> (struct fmt char, size)
_TYPES: dict[int, tuple[str, int]] = {
    1: ("B", 1),   # BYTE
    2: ("s", 1),   # ASCII
    3: ("H", 2),   # SHORT
    4: ("I", 4),   # LONG
    5: ("II", 8),  # RATIONAL
    6: ("b", 1),   # SBYTE
    7: ("B", 1),   # UNDEFINED
    8: ("h", 2),   # SSHORT
    9: ("i", 4),   # SLONG
    11: ("f", 4),  # FLOAT
    12: ("d", 8),  # DOUBLE
}

_SAMPLE_DTYPES = {
    (1, 8): "uint8", (1, 16): "uint16", (1, 32): "uint32", (1, 64): "uint64",
    (2, 8): "int8", (2, 16): "int16", (2, 32): "int32", (2, 64): "int64",
    (3, 16): "float16", (3, 32): "float32", (3, 64): "float64",
}


class CogReadError(DownloadError):
    """COG 结构不支持时抛出。"""


@dataclass(slots=True)
class TiffInfo:
    """TIFF 关键结构信息。"""

    byte_order: str
    width: int
    height: int
    tile_width: int
    tile_height: int
    bits_per_sample: int
    sample_format: int
    samples_per_pixel: int
    compression: int
    predictor: int
    planar_config: int
    tile_offsets: list[int] = field(default_factory=list)
    tile_byte_counts: list[int] = field(default_factory=list)
    entries: dict[int, Any] = field(default_factory=dict)
    # 地理参考：(a, b, c, d, e, f) 即 x = a*col + b*row + c ; y = d*col + e*row + f
    transform: tuple[float, float, float, float, float, float] | None = None
    epsg: int | None = None

    @property
    def dtype(self) -> np.dtype:
        key = (self.sample_format, self.bits_per_sample)
        name = _SAMPLE_DTYPES.get(key)
        if name is None:
            raise CogReadError(f"unsupported sample format/bits: {key}")
        return np.dtype(name)

    @property
    def tiles_x(self) -> int:
        return (self.width + self.tile_width - 1) // self.tile_width

    @property
    def tiles_y(self) -> int:
        return (self.height + self.tile_height - 1) // self.tile_height

    @property
    def bounds(self) -> tuple[float, float, float, float] | None:
        """(minx, miny, maxx, maxy)；无地理参考返回 None。"""
        if self.transform is None:
            return None
        a, b, c, d, e, f = self.transform
        corners = [(0, 0), (self.width, 0), (0, self.height), (self.width, self.height)]
        xs = [a * col + b * row + c for col, row in corners]
        ys = [d * col + e * row + f for col, row in corners]
        return (min(xs), min(ys), max(xs), max(ys))


@dataclass(slots=True)
class LevelInfo:
    """COG 金字塔层（IFD 链中的一级）。"""

    width: int
    height: int
    tile_width: int
    tile_height: int
    compression: int
    predictor: int
    dtype: np.dtype
    tile_offsets: list[int] = field(default_factory=list)
    tile_byte_counts: list[int] = field(default_factory=list)

    @property
    def tiles_x(self) -> int:
        return (self.width + self.tile_width - 1) // self.tile_width

    @property
    def tiles_y(self) -> int:
        return (self.height + self.tile_height - 1) // self.tile_height


class CogReader:
    """只按需拉取 COG 瓦片的窗口读取器。"""

    def __init__(self, url_or_file: str | RangeFile, *, block_size: int = 1 << 18) -> None:
        if isinstance(url_or_file, RangeFile):
            self._rf = url_or_file
            self._owns = False
        else:
            self._rf = RangeFile(url_or_file, block_size=block_size)
            self._owns = True
        self.info = self._parse()
        self._tile_cache: dict[int, np.ndarray] = {}
        try:
            self.levels = self._parse_levels()
        except Exception:
            self.levels = []

    # ── 生命周期 ──────────────────────────────────────────────────────────
    def close(self) -> None:
        self._tile_cache.clear()
        if self._owns:
            self._rf.close()

    def __enter__(self) -> CogReader:
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    # ── 解析 ──────────────────────────────────────────────────────────────
    def _parse(self) -> TiffInfo:
        header = self._rf.read_at(0, 8)
        if len(header) < 8:
            raise CogReadError("file too small to be a TIFF")
        bo = header[:2]
        if bo == b"II":
            order = "<"
        elif bo == b"MM":
            order = ">"
        else:
            raise CogReadError(f"not a TIFF: bad byte order {bo!r}")
        magic = struct.unpack(order + "H", header[2:4])[0]
        if magic != 42:
            raise CogReadError(f"not a TIFF: magic {magic} != 42")
        ifd0 = struct.unpack(order + "I", header[4:8])[0]
        entries = self._read_ifd(ifd0, order)

        def one(tag: int, default: Any = None) -> Any:
            v = entries.get(tag)
            if v is None:
                return default
            return v[0] if isinstance(v, list) and v else v

        tile_offsets = entries.get(TAG_TILE_OFFSETS) or []
        tile_counts = entries.get(TAG_TILE_BYTE_COUNTS) or []
        if not tile_offsets or not tile_counts:
            raise CogReadError("strip-based TIFF not supported; need tiled COG")

        info = TiffInfo(
            byte_order=order,
            width=int(one(TAG_IMAGE_WIDTH, 0)),
            height=int(one(TAG_IMAGE_LENGTH, 0)),
            tile_width=int(one(TAG_TILE_WIDTH, 0)),
            tile_height=int(one(TAG_TILE_LENGTH, 0)),
            bits_per_sample=int(one(TAG_BITS_PER_SAMPLE, 1)),
            sample_format=int(one(TAG_SAMPLE_FORMAT, 1)),
            samples_per_pixel=int(one(TAG_SAMPLES_PER_PIXEL, 1)),
            compression=int(one(TAG_COMPRESSION, 1)),
            predictor=int(one(TAG_PREDICTOR, 1)),
            planar_config=int(one(TAG_PLANAR_CONFIG, 1)),
            tile_offsets=[int(x) for x in tile_offsets],
            tile_byte_counts=[int(x) for x in tile_counts],
            entries=entries,
            transform=_geotransform(entries),
            epsg=_epsg_from_geokeys(entries),
        )
        if info.samples_per_pixel != 1:
            raise CogReadError(f"multi-sample tiles not supported: spp={info.samples_per_pixel}")
        if info.compression not in (1, 8):
            raise CogReadError(
                f"unsupported compression {info.compression} (only 1=none, 8=deflate); "
                "use fetch_mode=download instead"
            )
        if info.predictor not in (1, 2):
            raise CogReadError(f"unsupported predictor {info.predictor}")
        log.debug(
            "cog.parsed",
            size=(info.width, info.height),
            tile=(info.tile_width, info.tile_height),
            tiles=info.tiles_x * info.tiles_y,
            compression=info.compression,
            predictor=info.predictor,
        )
        return info

    def _read_ifd(self, offset: int, order: str, depth: int = 0) -> dict[int, Any]:
        return self._read_ifd_entries(offset, order, depth)[0]

    def _read_ifd_entries(self, offset: int, order: str, depth: int = 0) -> tuple[dict[int, Any], int]:
        """读单个 IFD 的 tag 表，返回 (entries, 下一个 IFD 偏移)。"""
        if depth > 6 or offset <= 0:
            return {}, 0
        raw = self._rf.read_at(offset, 2)
        if len(raw) < 2:
            return {}, 0
        n = struct.unpack(order + "H", raw)[0]
        body = self._rf.read_at(offset + 2, n * 12)
        entries: dict[int, Any] = {}
        for i in range(n):
            chunk = body[i * 12 : i * 12 + 12]
            if len(chunk) < 12:
                break
            tag, typ, cnt, val = struct.unpack(order + "HHII", chunk)
            entries[tag] = self._read_values(tag, typ, cnt, val, order)
        nxt_raw = self._rf.read_at(offset + 2 + n * 12, 4)
        nxt = struct.unpack(order + "I", nxt_raw[:4])[0] if len(nxt_raw) >= 4 else 0
        return entries, nxt

    def _parse_levels(self) -> list[LevelInfo]:
        """沿 IFD 链解析全部金字塔层（overviews）。"""
        header = self._rf.read_at(0, 8)
        if len(header) < 8:
            return []
        order = "<" if header[:2] == b"II" else ">"
        off = struct.unpack(order + "I", header[4:8])[0]
        levels: list[LevelInfo] = []
        depth = 0
        while off and depth < 6:
            entries, off = self._read_ifd_entries(off, order, depth)
            depth += 1
            w = entries.get(TAG_IMAGE_WIDTH)
            h = entries.get(TAG_IMAGE_LENGTH)
            tw = entries.get(TAG_TILE_WIDTH)
            th = entries.get(TAG_TILE_LENGTH)
            toff = entries.get(TAG_TILE_OFFSETS)
            tcnt = entries.get(TAG_TILE_BYTE_COUNTS)
            if not (w and h and tw and th and toff and tcnt):
                continue
            bps = entries.get(TAG_BITS_PER_SAMPLE) or [16]
            sf = entries.get(TAG_SAMPLE_FORMAT) or [1]
            bps_v = int(bps[0] if isinstance(bps, list) else bps)
            sf_v = int(sf[0] if isinstance(sf, list) else sf)
            name = _SAMPLE_DTYPES.get((sf_v, bps_v))
            if name is None:
                continue
            comp = entries.get(TAG_COMPRESSION) or [1]
            pred = entries.get(TAG_PREDICTOR) or [1]
            levels.append(
                LevelInfo(
                    width=int(w[0] if isinstance(w, list) else w),
                    height=int(h[0] if isinstance(h, list) else h),
                    tile_width=int(tw[0] if isinstance(tw, list) else tw),
                    tile_height=int(th[0] if isinstance(th, list) else th),
                    compression=int(comp[0] if isinstance(comp, list) else comp),
                    predictor=int(pred[0] if isinstance(pred, list) else pred),
                    dtype=np.dtype(name),
                    tile_offsets=[int(x) for x in toff],
                    tile_byte_counts=[int(x) for x in tcnt],
                )
            )
        return levels

    def pick_level(self, target_width: int) -> int:
        """选最接近且不小于 target_width 的金字塔层索引。"""
        levels = self.levels
        if not levels:
            return 0
        best = 0
        for i, lv in enumerate(levels):
            if lv.width >= target_width:
                best = i
            else:
                break
        return best

    def read_window_level(self, level: int, col_off: int, row_off: int, width: int, height: int) -> np.ndarray:
        """在指定金字塔层上读窗口。"""
        levels = self.levels
        if not levels:
            return self.read_window(col_off, row_off, width, height)
        lv = levels[min(max(0, level), len(levels) - 1)]
        col_off = max(0, int(col_off))
        row_off = max(0, int(row_off))
        width = max(0, min(int(width), lv.width - col_off))
        height = max(0, min(int(height), lv.height - row_off))
        if width <= 0 or height <= 0:
            return np.zeros((0, 0), dtype=lv.dtype)

        out = np.zeros((height, width), dtype=lv.dtype)
        tx0 = col_off // lv.tile_width
        ty0 = row_off // lv.tile_height
        tx1 = (col_off + width - 1) // lv.tile_width
        ty1 = (row_off + height - 1) // lv.tile_height
        for ty in range(ty0, ty1 + 1):
            for tx in range(tx0, tx1 + 1):
                tile = self._get_tile_level(lv, tx, ty)
                if tile is None:
                    continue
                x0 = tx * lv.tile_width
                y0 = ty * lv.tile_height
                sx0 = max(0, col_off - x0)
                sy0 = max(0, row_off - y0)
                sx1 = min(tile.shape[1], col_off + width - x0)
                sy1 = min(tile.shape[0], row_off + height - y0)
                if sx1 <= sx0 or sy1 <= sy0:
                    continue
                dx0 = x0 + sx0 - col_off
                dy0 = y0 + sy0 - row_off
                out[dy0 : dy0 + (sy1 - sy0), dx0 : dx0 + (sx1 - sx0)] = tile[sy0:sy1, sx0:sx1]
        return out

    def _get_tile_level(self, lv: LevelInfo, tx: int, ty: int) -> np.ndarray | None:
        idx = ty * lv.tiles_x + tx
        if idx < 0 or idx >= len(lv.tile_offsets):
            return None
        key = ("lv", id(lv), idx)
        cache = self.__dict__.setdefault("_lvl_cache", {})
        if key in cache:
            return cache[key]
        off = lv.tile_offsets[idx]
        cnt = lv.tile_byte_counts[idx]
        if off <= 0 or cnt <= 0:
            return None
        raw = self._rf.read_at(off, cnt)
        if lv.compression == 8:
            import zlib

            raw = zlib.decompress(raw)
        dt = lv.dtype
        arr = np.frombuffer(raw, dtype=dt)
        need = lv.tile_width * lv.tile_height
        if arr.size < need:
            arr = np.pad(arr, (0, need - arr.size))
        tile = arr[:need].reshape(lv.tile_height, lv.tile_width).copy()
        if lv.predictor == 2 and dt.kind in "iu":
            tile = _undo_predictor2(tile)
        cache[key] = tile
        if len(cache) > 256:
            cache.clear()
        return tile

    def _read_values(self, tag: int, typ: int, cnt: int, val: int, order: str) -> Any:
        if typ not in _TYPES:
            return val
        fmt_char, size = _TYPES[typ]
        total = size * cnt
        if total <= 4:
            buf = struct.pack(order + "I", val)[:total]
        else:
            buf = self._rf.read_at(val, total)
            if len(buf) < total:
                return val
        if typ == 2:  # ASCII
            return buf.split(b"\x00")[0].decode("utf-8", "replace")
        if fmt_char == "II":  # RATIONAL
            count = total // 8
            parts = struct.unpack(order + "II" * count, buf[: count * 8])
            return [parts[i] / parts[i + 1] if parts[i + 1] else 0.0 for i in range(0, len(parts), 2)]
        count = total // size
        return list(struct.unpack(order + fmt_char * count, buf[: count * size]))

    # ── 读取 ──────────────────────────────────────────────────────────────
    def read_window(self, col_off: int, row_off: int, width: int, height: int) -> np.ndarray:
        """读取像素窗口 [row_off:row_off+height, col_off:col_off+width]。"""
        info = self.info
        col_off = max(0, int(col_off))
        row_off = max(0, int(row_off))
        width = max(0, min(int(width), info.width - col_off))
        height = max(0, min(int(height), info.height - row_off))
        if width <= 0 or height <= 0:
            return np.zeros((0, 0), dtype=info.dtype)

        out = np.zeros((height, width), dtype=info.dtype)
        tx0 = col_off // info.tile_width
        ty0 = row_off // info.tile_height
        tx1 = (col_off + width - 1) // info.tile_width
        ty1 = (row_off + height - 1) // info.tile_height

        for ty in range(ty0, ty1 + 1):
            for tx in range(tx0, tx1 + 1):
                tile = self._get_tile(tx, ty)
                if tile is None:
                    continue
                # 瓦片在输出中的位置
                r0 = ty * info.tile_height
                c0 = tx * info.tile_width
                src_r0 = max(0, row_off - r0)
                src_c0 = max(0, col_off - c0)
                dst_r0 = max(0, r0 - row_off)
                dst_c0 = max(0, c0 - col_off)
                h = min(info.tile_height - src_r0, height - dst_r0)
                w = min(info.tile_width - src_c0, width - dst_c0)
                if h > 0 and w > 0:
                    out[dst_r0 : dst_r0 + h, dst_c0 : dst_c0 + w] = tile[
                        src_r0 : src_r0 + h, src_c0 : src_c0 + w
                    ]
        return out

    def _get_tile(self, tx: int, ty: int) -> np.ndarray | None:
        info = self.info
        idx = ty * info.tiles_x + tx
        if idx < 0 or idx >= len(info.tile_offsets):
            return None
        if idx in self._tile_cache:
            return self._tile_cache[idx]

        offset = info.tile_offsets[idx]
        count = info.tile_byte_counts[idx] if idx < len(info.tile_byte_counts) else 0
        if offset <= 0 or count <= 0:
            return None

        raw = self._rf.read_at(offset, count)
        if len(raw) != count:
            log.debug("cog.tile.short", idx=idx, got=len(raw), want=count)
            return None

        tile = self._decode_tile(raw)
        if tile is not None:
            self._tile_cache[idx] = tile
            if len(self._tile_cache) > 256:
                self._tile_cache.pop(next(iter(self._tile_cache)))
        return tile

    def _decode_tile(self, raw: bytes) -> np.ndarray | None:
        info = self.info
        if info.compression == 8:
            try:
                raw = zlib.decompress(raw)
            except zlib.error as exc:
                log.debug("cog.tile.inflate-fail", error=str(exc))
                return None
        elif info.compression != 1:
            return None

        expected = info.tile_width * info.tile_height * (info.bits_per_sample // 8)
        if len(raw) < expected:
            # 边缘瓦片可能不满，填充
            raw = raw + b"\x00" * (expected - len(raw))
        arr = np.frombuffer(raw[:expected], dtype=info.dtype).reshape(
            info.tile_height, info.tile_width
        )

        if info.predictor == 2:
            arr = _undo_predictor2(arr)
        return arr

    # ── 诊断 ──────────────────────────────────────────────────────────────
    @property
    def stats(self) -> dict[str, Any]:
        return {
            "size": (self.info.width, self.info.height),
            "tile": (self.info.tile_width, self.info.tile_height),
            "tiles_total": self.info.tiles_x * self.info.tiles_y,
            "tiles_cached": len(self._tile_cache),
            "range_file": self._rf.stats,
        }

    # ── bbox -> 像素窗口 ──────────────────────────────────────────────────
    def window_for_bbox(
        self,
        bbox: tuple[float, float, float, float],
        *,
        bbox_epsg: int = 4326,
    ) -> tuple[int, int, int, int]:
        """把地理 bbox 转为 (col_off, row_off, width, height)。

        `bbox_epsg` 指定 bbox 坐标系；与栅格 CRS 不同时自动重投影。
        """
        info = self.info
        if info.transform is None:
            return (0, 0, info.width, info.height)

        minx, miny, maxx, maxy = bbox
        if info.epsg and info.epsg != bbox_epsg:
            minx, miny, maxx, maxy = _reproject_bbox(
                (minx, miny, maxx, maxy), bbox_epsg, info.epsg
            )

        a, b, c, d, e, f = info.transform
        # 假设无旋转（b == d == 0）
        if abs(b) > 1e-9 or abs(d) > 1e-9:
            log.debug("cog.rotated-approx")

        # 用 min/max 统一处理 a/e 的正负（e 通常为负，y 向上递减）
        def _span(lo_val: float, hi_val: float, scale: float, origin: float) -> tuple[int, int]:
            if not scale:
                return 0, info.width if scale == a else info.height
            p0 = (lo_val - origin) / scale
            p1 = (hi_val - origin) / scale
            return int(np.floor(min(p0, p1))), int(np.ceil(max(p0, p1)))

        col0, col1 = _span(minx, maxx, a, c)
        row0, row1 = _span(miny, maxy, e, f)

        col0 = max(0, min(col0, info.width))
        col1 = max(0, min(col1, info.width))
        row0 = max(0, min(row0, info.height))
        row1 = max(0, min(row1, info.height))
        return (col0, row0, max(0, col1 - col0), max(0, row1 - row0))


def _reproject_bbox(
    bbox: tuple[float, float, float, float],
    src_epsg: int,
    dst_epsg: int,
) -> tuple[float, float, float, float]:
    """把 bbox 从 src_epsg 重投影到 dst_epsg（取四角与边中点的包络）。"""
    from pyproj import Transformer

    minx, miny, maxx, maxy = bbox
    tf = Transformer.from_crs(f"EPSG:{src_epsg}", f"EPSG:{dst_epsg}", always_xy=True)
    xs: list[float] = []
    ys: list[float] = []
    for x in (minx, (minx + maxx) / 2, maxx):
        for y in (miny, (miny + maxy) / 2, maxy):
            px, py = tf.transform(x, y)
            if px == px and py == py:  # 过滤 NaN
                xs.append(px)
                ys.append(py)
    if not xs:
        return bbox
    return (min(xs), min(ys), max(xs), max(ys))


def _geotransform(entries: dict[int, Any]) -> tuple[float, float, float, float, float, float] | None:
    """从 ModelPixelScale(33550) + ModelTiepoint(33922) 推导仿射变换。"""
    scale = entries.get(33550)
    tie = entries.get(33922)
    if not scale or not tie or len(scale) < 3 or len(tie) < 6:
        return None
    sx, sy = float(scale[0]), float(scale[1])
    # tie: (i, j, k, x, y, z)
    i0, j0 = float(tie[0]), float(tie[1])
    x0, y0 = float(tie[3]), float(tie[4])
    # a, b, c, d, e, f  (x = a*col + c ; y = e*row + f)
    a = sx
    e = -sy
    c = x0 - a * i0
    f = y0 - e * j0
    return (a, 0.0, c, 0.0, e, f)


# GeoKey：ProjectedCSTypeGeoKey / GeographicTypeGeoKey
_GEOKEY_PROJECTED_CS = 3072
_GEOKEY_GEOGRAPHIC_CS = 2048


def _epsg_from_geokeys(entries: dict[int, Any]) -> int | None:
    """从 GeoKeyDirectory(34735) 提取 EPSG 代码。"""
    keys = entries.get(34735)
    if not keys or len(keys) < 4:
        return None
    n = int(keys[3])
    for i in range(n):
        base = 4 + i * 4
        if base + 3 >= len(keys):
            break
        key_id = int(keys[base])
        tiff_tag_loc = int(keys[base + 1])
        value = int(keys[base + 3])
        # TIFFTagLocation == 0 表示值直接在 ValueOffset
        if (
            tiff_tag_loc == 0
            and key_id in (_GEOKEY_PROJECTED_CS, _GEOKEY_GEOGRAPHIC_CS)
            and 1 <= value < 32767
        ):
            return value
    return None


def _undo_predictor2(arr: np.ndarray) -> np.ndarray:
    """撤销水平差分预测（Predictor=2）。"""
    out = arr.astype(np.int64, copy=True).cumsum(axis=1)
    # 保持原 dtype（无符号回绕语义与 TIFF 一致）
    return out.astype(arr.dtype, copy=False)


def read_window(
    url: str,
    col_off: int,
    row_off: int,
    width: int,
    height: int,
) -> np.ndarray:
    """便捷函数：一次性读取窗口。"""
    with CogReader(url) as r:
        return r.read_window(col_off, row_off, width, height)


__all__ = ["CogReader", "TiffInfo", "read_window"]
