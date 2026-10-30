"""辐射定标系数读取：MTL.txt / MTD_*.xml / STAC 属性 / 传感器兜底值。

系数优先级：
1. STAC item 属性（landsat:reflectance_*、s2:reflectance_*）
2. 产品元数据（MTL.txt / MTD_MSIL2A.xml）
3. configs/sensors.yaml 兜底值
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from rspipe.errors import MetadataError
from rspipe.logging import get_logger
from rspipe.models import SceneMeta, SensorSpec

log = get_logger("rspipe.normalize.reflectance")


@dataclass(slots=True)
class BandScaling:
    """单波段的定标系数。"""

    band: str  # 产品波段名，如 B4 / B02
    scale: float = 1.0
    offset: float = 0.0
    nodata: float | None = None

    def apply(self, arr: Any) -> Any:
        return arr.astype("float32") * self.scale + self.offset


@dataclass(slots=True)
class ScalingSet:
    """整景的定标系数集合。"""

    by_band: dict[str, BandScaling]
    source: str = "sensor-default"

    def for_band(self, band: str) -> BandScaling:
        for key in (band, band.upper(), band.lower(), band.lstrip("Bb")):
            if key in self.by_band:
                return self.by_band[key]
        # 宽松：B4 <-> B04 <-> 4（忽略前导零）
        num = re.sub(r"\D", "", band).lstrip("0") or "0"
        for key, val in self.by_band.items():
            if (re.sub(r"\D", "", key).lstrip("0") or "0") == num:
                return val
        raise MetadataError(f"no scaling for band {band!r} (have {sorted(self.by_band)})")


# ── MTL.txt（Landsat） ────────────────────────────────────────────────────────
_MTL_SCALAR_RE = re.compile(r"^\s*([A-Z0-9_]+)\s*=\s*(.+?)\s*$")


def parse_mtl(path: Path) -> dict[str, Any]:
    """解析 Landsat MTL.txt 为扁平 dict。"""
    if not path.exists():
        raise MetadataError(f"MTL not found: {path}")
    out: dict[str, Any] = {}
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        m = _MTL_SCALAR_RE.match(line)
        if not m:
            continue
        key, raw = m.group(1), m.group(2).strip().strip('"')
        if raw.upper() == "Y":
            out[key] = True
        elif raw.upper() == "N":
            out[key] = False
        else:
            try:
                out[key] = int(raw) if re.fullmatch(r"[-+]?\d+", raw) else float(raw)
            except ValueError:
                out[key] = raw
    return out


def scaling_from_mtl(path: Path, bands: list[str]) -> ScalingSet:
    """从 MTL.txt 读 REFLECTANCE_MULT/ADD_BAND_x 或 RADIANCE_MULT/ADD_BAND_x。"""
    meta = parse_mtl(path)
    by_band: dict[str, BandScaling] = {}
    for band in bands:
        num = re.sub(r"\D", "", band)
        if not num:
            continue
        # L2 地表反射率优先
        mult = meta.get(f"REFLECTANCE_MULT_BAND_{num}") or meta.get(f"REFLECTANCE_MULT_BAND_{int(num)}")
        add = meta.get(f"REFLECTANCE_ADD_BAND_{num}") or meta.get(f"REFLECTANCE_ADD_BAND_{int(num)}")
        if mult is None:
            mult = meta.get(f"RADIANCE_MULT_BAND_{num}") or meta.get(f"RADIANCE_MULT_BAND_{int(num)}")
            add = meta.get(f"RADIANCE_ADD_BAND_{num}") or meta.get(f"RADIANCE_ADD_BAND_{int(num)}")
        if mult is None:
            continue
        by_band[band] = BandScaling(
            band=band,
            scale=float(mult),
            offset=float(add) if add is not None else 0.0,
        )
    if not by_band:
        raise MetadataError(f"no reflectance/radiance scaling found in {path}")
    return ScalingSet(by_band=by_band, source=str(path))


# ── MTD_*.xml（Sentinel-2） ───────────────────────────────────────────────────
def parse_mtd_xml(path: Path) -> dict[str, Any]:
    """解析 S2 MTD_MSIL2A.xml，抽取量化值与逐波段偏移。"""
    if not path.exists():
        raise MetadataError(f"MTD not found: {path}")
    try:
        root = ET.parse(path).getroot()
    except ET.ParseError as exc:
        raise MetadataError(f"invalid MTD xml {path}: {exc}") from exc

    out: dict[str, Any] = {}
    for el in root.iter():
        tag = el.tag.split("}")[-1]
        if tag in (
            "QUANTIFICATION_VALUE",
            "BOA_ADD_OFFSET",
            "RADIO_ADD_OFFSET",
            "U",
            "Solar_Irradiance",
        ) and el.text and el.text.strip():
            try:
                out[tag] = float(el.text.strip())
            except ValueError:
                out[tag] = el.text.strip()
        # 逐波段偏移：BOA_QUANTIFICATION_VALUE_LIST / RADIO_ADD_OFFSET_LIST
        if tag in ("BOA_ADD_OFFSET_LIST", "RADIO_ADD_OFFSET_LIST") and el.text:
            out[tag] = [float(x) for x in el.text.split() if x.strip()]
    return out


# S2 L2A 13 波段顺序（与 BOA_ADD_OFFSET_LIST 对齐）
_S2_BAND_ORDER = [
    "B01", "B02", "B03", "B04", "B05", "B06", "B07",
    "B08", "B8A", "B09", "B10", "B11", "B12",
]


def scaling_from_mtd(path: Path, bands: list[str]) -> ScalingSet:
    meta = parse_mtd_xml(path)
    qv = meta.get("QUANTIFICATION_VALUE") or meta.get("BOA_QUANTIFICATION_VALUE") or 10000.0
    offsets = meta.get("BOA_ADD_OFFSET_LIST") or meta.get("RADIO_ADD_OFFSET_LIST") or []

    by_band: dict[str, BandScaling] = {}
    for band in bands:
        key = band.upper()
        idx = _S2_BAND_ORDER.index(key) if key in _S2_BAND_ORDER else -1
        offset = 0.0
        if 0 <= idx < len(offsets):
            offset = float(offsets[idx])
        elif "RADIO_ADD_OFFSET" in meta and len(offsets) == 0:
            offset = float(meta["RADIO_ADD_OFFSET"])
        by_band[band] = BandScaling(band=band, scale=1.0 / float(qv), offset=offset / float(qv))

    if not by_band:
        raise MetadataError(f"no scaling info in {path}")
    return ScalingSet(by_band=by_band, source=str(path))


# ── STAC 属性 ─────────────────────────────────────────────────────────────────
def scaling_from_stac(scene: SceneMeta, spec: SensorSpec, bands: list[str]) -> ScalingSet | None:
    props = scene.properties
    by_band: dict[str, BandScaling] = {}

    # earth-search 的 S2 PB>=04 数据已在 COG 里剔除 BOA_ADD_OFFSET（DN 0 即反射率 0），
    # 但 asset raster:bands 仍带着 ESA 原生标注 offset=-0.1 —— 元数据自相矛盾。
    # 该标志为真时必须忽略资产级 offset，否则会扣两次导致整图变暗 ~0.1 反射率。
    boa_offset_removed = bool(props.get("earthsearch:boa_offset_applied"))

    # 1) 逐资产 raster:bands（STAC raster 扩展，Planetary Computer / Earth Search 提供）
    for common in bands:
        asset = _find_asset(scene, common, spec)
        if asset is None:
            continue
        rbands = asset.extra.get("raster:bands") or []
        if not isinstance(rbands, list):
            continue
        for rb in rbands:
            if not isinstance(rb, dict):
                continue
            if "scale" in rb or "offset" in rb:
                by_band[common] = BandScaling(
                    band=common,
                    scale=float(rb.get("scale", 1.0)),
                    offset=0.0 if boa_offset_removed else float(rb.get("offset", 0.0)),
                    nodata=rb.get("nodata"),
                )
                break
    if by_band:
        return ScalingSet(by_band=by_band, source="stac-raster-bands")

    # 2) item 属性里的显式系数
    for band in bands:
        num = re.sub(r"\D", "", band)
        if not num:
            continue
        for prefix in ("landsat:reflectance", "landsat:radiance", "s2:reflectance"):
            mult = props.get(f"{prefix}_mult_band_{num}") or props.get(f"{prefix}_mult_band_{int(num)}")
            add = props.get(f"{prefix}_add_band_{num}") or props.get(f"{prefix}_add_band_{int(num)}")
            if mult is not None:
                by_band[band] = BandScaling(
                    band=band,
                    scale=float(mult),
                    offset=float(add) if add is not None else 0.0,
                )
                break

    if not by_band:
        return None
    return ScalingSet(by_band=by_band, source="stac-properties")


def _find_asset(scene: SceneMeta, common: str, spec: SensorSpec) -> Any:
    """按通用波段名定位资产（与 bands.BandResolver 同语义的轻量版）。"""
    from rspipe.bands import BandResolver

    try:
        return BandResolver(scene, spec).try_asset_for(common)
    except Exception:
        return None


# ── 传感器兜底 ────────────────────────────────────────────────────────────────
def scaling_from_spec(spec: SensorSpec, bands: list[str]) -> ScalingSet:
    return ScalingSet(
        by_band={
            b: BandScaling(
                band=b,
                scale=spec.reflectance_scale,
                offset=spec.reflectance_offset,
            )
            for b in bands
        },
        source=f"sensor-default:{spec.key}",
    )


# ── 统一入口 ──────────────────────────────────────────────────────────────────
def resolve_scaling(
    *,
    spec: SensorSpec,
    bands: list[str],
    scene: SceneMeta | None = None,
    mtl_path: Path | None = None,
    mtd_path: Path | None = None,
) -> ScalingSet:
    """按优先级解析定标系数集合。"""
    if scene is not None:
        s = scaling_from_stac(scene, spec, bands)
        if s is not None:
            log.debug("scaling.source", source=s.source)
            return s

    if mtl_path is not None and mtl_path.exists():
        try:
            s = scaling_from_mtl(mtl_path, bands)
            log.debug("scaling.source", source=s.source)
            return s
        except MetadataError:
            pass

    if mtd_path is not None and mtd_path.exists():
        try:
            s = scaling_from_mtd(mtd_path, bands)
            log.debug("scaling.source", source=s.source)
            return s
        except MetadataError:
            pass

    s = scaling_from_spec(spec, bands)
    log.info("scaling.source", source=s.source)
    return s


__all__ = [
    "BandScaling",
    "ScalingSet",
    "parse_mtd_xml",
    "parse_mtl",
    "resolve_scaling",
    "scaling_from_mtd",
    "scaling_from_mtl",
    "scaling_from_spec",
    "scaling_from_stac",
]
