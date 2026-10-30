"""波段名解析：通用名 <-> 产品波段名 <-> STAC 资产 key。

不同数据源命名不一致：
- LandsatLook STAC : 资产 key 为通用名 `red` / `blue` / `nir08` / `swir16`
- CDSE / SAFE      : 资产 key 为产品名 `B02` / `B03` / `B04` / `B8A`
- MTL 定标字段      : 使用产品名 `BAND_4` / `B5`

这里统一做解析，调用方只需给通用名。
"""

from __future__ import annotations

from collections.abc import Iterable

from rspipe.errors import MetadataError
from rspipe.models import AssetRef, SceneMeta, SensorSpec

# 通用名 -> 常见别名（按数据源习惯）
_COMMON_ALIASES: dict[str, tuple[str, ...]] = {
    "coastal": ("coastal", "b01", "b1", "band_1", "cirrus_aerosol"),
    "blue": ("blue", "b02", "b2", "band_2"),
    "green": ("green", "b03", "b3", "band_3"),
    "red": ("red", "b04", "b4", "band_4"),
    "rededge1": ("rededge1", "b05", "b5", "band_5"),
    "rededge2": ("rededge2", "b06", "b6", "band_6"),
    "rededge3": ("rededge3", "b07", "b7", "band_7"),
    "nir": ("nir", "nir08", "nir_8", "b05", "b5", "band_5", "b08", "b8", "b8a", "nir_narrow"),
    "nir_narrow": ("nir_narrow", "b8a", "b08a", "nir08"),
    "swir1": ("swir1", "swir16", "swir_1", "b06", "b6", "band_6", "b11", "b110"),
    "swir2": ("swir2", "swir22", "swir_2", "b07", "b7", "band_7", "b12", "b120"),
    "cirrus": ("cirrus", "b09", "b9", "b10", "band_9"),
    "tir1": ("tir1", "b10", "b100", "st_b10"),
    "tir2": ("tir2", "b11", "b110", "st_b11"),
}

# QA / 掩膜资产的常见 key
QA_KEYS: tuple[str, ...] = (
    "qa_pixel",
    "qa_pixel",
    "QA_PIXEL",
    "qa",
    "fmask",
    "scl",
    "SCL",
    "scene_classification",
    "cloud_mask",
    "mask",
)
QA_ROLES: tuple[str, ...] = (
    "cloud",
    "cloud-shadow",
    "snow-ice",
    "water-mask",
    "data-mask",
    "mask",
    "qa",
)


def _norm(key: str) -> str:
    return key.strip().lower().replace("-", "_").replace(" ", "_")


class BandResolver:
    """把通用波段名解析到场景实际的资产。"""

    def __init__(self, scene: SceneMeta, spec: SensorSpec | None = None) -> None:
        self.scene = scene
        self.spec = spec
        self._by_norm: dict[str, AssetRef] = {_norm(a.key): a for a in scene.assets}

    # ── 波段资产 ──────────────────────────────────────────────────────────
    def asset_for(self, common: str) -> AssetRef:
        """通用名 -> 资产。找不到抛 MetadataError。"""
        found = self.try_asset_for(common)
        if found is None:
            raise MetadataError(
                f"scene {self.scene.scene_id!r}: cannot resolve band {common!r}; "
                f"available assets: {sorted(self._by_norm)}"
            )
        return found

    def try_asset_for(self, common: str) -> AssetRef | None:
        candidates = self._candidates(common)
        for cand in candidates:
            if cand in self._by_norm:
                return self._by_norm[cand]
        # 宽松匹配：任何包含候选串的资产 key
        for cand in candidates:
            for norm_key, asset in self._by_norm.items():
                if cand and cand in norm_key and asset.is_data:
                    return asset
        return None

    def _candidates(self, common: str) -> list[str]:
        out: list[str] = [_norm(common)]
        out.extend(_norm(a) for a in _COMMON_ALIASES.get(_norm(common), ()))
        if self.spec is not None:
            # sensor spec 映射到产品名
            prod = self.spec.band_map.get(common) or self.spec.band_map.get(_norm(common))
            if prod:
                out.append(_norm(prod))
                out.append(_norm(f"sr_{prod}"))
                out.append(_norm(f"b{prod.lstrip('Bb')}"))
        # 去重保序
        seen: set[str] = set()
        uniq: list[str] = []
        for x in out:
            if x and x not in seen:
                seen.add(x)
                uniq.append(x)
        return uniq

    def data_assets(self, commons: Iterable[str]) -> dict[str, AssetRef]:
        """返回 {通用名: 资产}，只含能解析到的。"""
        out: dict[str, AssetRef] = {}
        for c in commons:
            a = self.try_asset_for(c)
            if a is not None:
                out[c] = a
        return out

    # ── QA / 掩膜 ─────────────────────────────────────────────────────────
    def qa_asset(self) -> AssetRef | None:
        # 先按角色找
        for role in QA_ROLES:
            a = self.scene.asset_by_role(role)
            if a is not None and a.is_raster:
                return a
        # 再按 key 找
        for key in QA_KEYS:
            a = self._by_norm.get(_norm(key))
            if a is not None:
                return a
        # 最后退到 spec 的 qa_band
        if self.spec and self.spec.qa_band:
            return self._by_norm.get(_norm(self.spec.qa_band))
        return None

    def thumbnail(self) -> AssetRef | None:
        for role in ("thumbnail", "overview", "rendered_preview"):
            a = self.scene.asset_by_role(role)
            if a is not None:
                return a
        return None

    def metadata_assets(self) -> list[AssetRef]:
        return [a for a in self.scene.assets if "metadata" in a.roles or not a.is_raster]


def resolve_band_map(
    scene: SceneMeta, spec: SensorSpec | None, commons: Iterable[str]
) -> dict[str, AssetRef]:
    return BandResolver(scene, spec).data_assets(commons)


__all__ = [
    "QA_KEYS",
    "QA_ROLES",
    "BandResolver",
    "resolve_band_map",
]
