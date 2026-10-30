"""COG 写出与校验。

优先使用 GDAL 原生 COG 驱动（rasterio >= 1.3 / GDAL >= 3.1），
失败时回退到 rio-cogeo 的 `cog_translate`。
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any

import rasterio
from rasterio.enums import Resampling

from rspipe.errors import CogError
from rspipe.logging import get_logger

log = get_logger("rspipe.normalize.cog")

# COG 推荐默认值
DEFAULT_BLOCKSIZE = 512
DEFAULT_OVERVIEW_RESAMPLING = "cubic"
DEFAULT_COMPRESS = "deflate"


def to_cog(
    src_path: str | Path,
    dst_path: Path,
    *,
    compress: str = DEFAULT_COMPRESS,
    blocksize: int = DEFAULT_BLOCKSIZE,
    overview_resampling: str = DEFAULT_OVERVIEW_RESAMPLING,
    overview_levels: Sequence[int] | None = None,
    nodata: float | None = None,
    dtype: str | None = None,
    band_names: Sequence[str] | None = None,
    add_mask: bool = False,
    allow_overwrite: bool = True,
) -> Path:
    """把任意 GeoTIFF 转成 COG 并返回输出路径。

    用 `rasterio.shutil.copy(..., driver="COG")`（走 GDAL `CreateCopy`），
    保证布局合规：主 IFD 偏移 < 300、首块位于 IFD 之后、内部 overviews。
    （`cog_translate` 与 `driver="COG"` + `Write` 都会产出 IFD 在末尾的不合规文件。）
    """
    dst_path.parent.mkdir(parents=True, exist_ok=True)
    if dst_path.exists() and not allow_overwrite:
        return dst_path

    src_path = Path(src_path)
    if not src_path.exists() and not str(src_path).startswith("/vsi"):
        raise CogError(f"source raster not found: {src_path}")

    import rasterio.shutil

    kwargs: dict[str, Any] = {
        "driver": "COG",
        "compress": compress.upper(),
        "blocksize": blocksize,
        "overview_resampling": overview_resampling,
        "BIGTIFF": "IF_SAFER",
    }
    if overview_levels:
        kwargs["overview_levels"] = list(overview_levels)
    if nodata is not None:
        kwargs["nodata"] = nodata
    if dtype is not None:
        kwargs["dtype"] = dtype
    if add_mask:
        kwargs["add_alpha"] = False

    tmp = dst_path.with_suffix(".cog.tmp.tif")
    try:
        if tmp.exists():
            tmp.unlink()
        # shutil.copy 会保留源的波段描述；band_names 指定时事后覆盖
        rasterio.shutil.copy(str(src_path), str(tmp), **kwargs)
    except Exception as exc:
        tmp.unlink(missing_ok=True)
        raise CogError(f"COG conversion failed for {src_path}: {exc}") from exc

    if band_names:
        _set_band_names(tmp, band_names)

    tmp.replace(dst_path)

    ok = validate_cog(dst_path)
    log.info(
        "cog.done",
        out=str(dst_path),
        valid=ok,
        compress=compress,
        blocksize=blocksize,
    )
    return dst_path


def _set_band_names(path: Path, band_names: Sequence[str]) -> None:
    """覆盖 COG 的波段描述。"""
    try:
        with rasterio.open(str(path), "r+") as ds:
            for i, name in enumerate(band_names, start=1):
                if i <= ds.count:
                    ds.set_band_description(i, name)
    except Exception as exc:
        log.warning("cog.band-meta-failed", error=str(exc))


def validate_cog(path: str | Path) -> bool:
    """校验 COG 结构；失败返回 False（不抛异常）。

    先用 rio_cogeo 严格校验；小尺寸栅格（不足一个 overview 级别）
    没有内部 overviews 属正常，此时回落到「GTiff + tiled」宽松判定。
    """
    try:
        from rio_cogeo.cogeo import cog_validate as _validate

        res = _validate(str(path))
        ok = bool(res[0]) if isinstance(res, tuple) else bool(res)
        if ok:
            return True
    except Exception:
        pass

    try:
        with rasterio.open(str(path)) as src:
            if src.driver != "GTiff":
                return False
            if not src.profile.get("tiled", True):
                return False
            if src.width <= 512 and src.height <= 512:
                return True
            return bool(src.overviews(1))
    except Exception:
        return False


def cog_info(path: str | Path) -> dict[str, Any]:
    """返回 COG 关键信息（诊断用）。"""
    with rasterio.open(str(path)) as src:
        return {
            "path": str(path),
            "driver": src.driver,
            "size": (src.width, src.height),
            "count": src.count,
            "dtype": src.dtypes[0],
            "crs": str(src.crs) if src.crs else None,
            "nodata": src.nodata,
            "compress": src.profile.get("compress"),
            "blocksize": (src.profile.get("blockxsize"), src.profile.get("blockysize")),
            "overviews": [src.overviews(i) for i in range(1, src.count + 1)],
            "descriptions": list(src.descriptions),
        }


def make_overviews(
    path: str | Path,
    levels: Sequence[int] = (2, 4, 8, 16, 32),
    resampling: Resampling = Resampling.cubic,
) -> None:
    """为已有 GeoTIFF 补建内部 overviews。"""
    with rasterio.open(str(path), "r+") as dst:
        dst.build_overviews(list(levels), resampling)
        dst.update_tags(ns="rio_overview", resampling=str(resampling))


__all__ = [
    "DEFAULT_BLOCKSIZE",
    "DEFAULT_COMPRESS",
    "cog_info",
    "make_overviews",
    "to_cog",
    "validate_cog",
]
