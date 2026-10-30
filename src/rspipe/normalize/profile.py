"""产品画像 -> 流水线步骤序列。

由 `ProductProfile` 推导出一景影像需要经过哪些处理步骤，
COG 转换 / 辐射定标是条件步骤。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from rspipe.models import ProductProfile, SceneMeta

StepName = Literal[
    "unpack",
    "stack",
    "radiometric_normalize",
    "harmonize",
    "reproject",
    "cog_convert",
    "cloud_mask",
    "color_balance",
    "mosaic",
    "stac_publish",
]


@dataclass(slots=True)
class StepPlan:
    """单个步骤及其参数。"""

    name: StepName
    enabled: bool = True
    params: dict[str, Any] | None = None
    reason: str = ""


# 步骤显示顺序（与执行顺序一致）
STEP_ORDER: tuple[StepName, ...] = (
    "unpack",
    "stack",
    "radiometric_normalize",
    "harmonize",
    "reproject",
    "cog_convert",
    "cloud_mask",
    "color_balance",
    "mosaic",
    "stac_publish",
)


def plan_steps(
    profile: ProductProfile,
    *,
    want_reproject: bool = False,
    want_color_balance: bool = True,
    want_mosaic: bool = False,
) -> list[StepPlan]:
    """推导步骤序列。"""
    plans: dict[StepName, StepPlan] = {
        n: StepPlan(name=n, enabled=False) for n in STEP_ORDER
    }

    def enable(name: StepName, reason: str, **params: Any) -> None:
        plans[name] = StepPlan(name=name, enabled=True, reason=reason, params=params or None)

    # 1. 解包
    if profile.needs_unpack:
        enable("unpack", f"source_format={profile.source_format.value}")

    # 2. 堆叠波段（多单波段资产 -> 单多波段）
    enable("stack", "multi-band assembly")

    # 3. 辐射定标（DN 源必做）
    if profile.needs_radiometric_normalize:
        enable("radiometric_normalize", "radiometry=dn")

    # 4. 波段/单位统一
    if profile.needs_harmonize:
        enable("harmonize", "source_format=cog (band/unit harmonization)")

    # 5. 重投影（可选）
    if want_reproject:
        enable("reproject", "target grid requested")

    # 6. COG 转换：始终产出规范化 COG 产物
    #    （源已是 COG 时只是「重编码归一化结果」，仍是必需步骤）
    if profile.needs_cog_convert:
        enable("cog_convert", f"source_format={profile.source_format.value} -> COG")
    else:
        enable("cog_convert", "emit normalized COG (source already COG)")

    # 7. 去云
    enable("cloud_mask", "QA/SCL based cloud masking")

    # 8. 匀色
    if want_color_balance:
        enable("color_balance", "histogram matching to reference")

    # 9. 镶嵌
    if want_mosaic:
        enable("mosaic", "seamless feathered mosaic")

    # 10. STAC 发布
    enable("stac_publish", "STAC item + index")

    ordered = [plans[n] for n in STEP_ORDER if plans[n].enabled]
    return ordered


def plan_summary(plans: list[StepPlan]) -> str:
    return " -> ".join(p.name for p in plans)


def profile_from_scene(scene: SceneMeta) -> ProductProfile:
    """从 SceneMeta 推断产品画像。"""
    return ProductProfile(
        source_format=scene.source_format,
        product_level=scene.product_level,
        radiometry=scene.radiometry,
        has_qa=scene.asset_by_role("data-mask", "cloud", "qa", "mask") is not None
        or any(a.key.lower() in ("qa_pixel", "scl", "fmask", "qa") for a in scene.assets),
        crs_known=True,
    )


def describe_profile(p: ProductProfile) -> dict[str, Any]:
    return {
        "source_format": p.source_format.value,
        "product_level": p.product_level.value,
        "radiometry": p.radiometry.value,
        "has_qa": p.has_qa,
        "needs_unpack": p.needs_unpack,
        "needs_radiometric_normalize": p.needs_radiometric_normalize,
        "needs_cog_convert": p.needs_cog_convert,
        "needs_harmonize": p.needs_harmonize,
    }


__all__ = [
    "STEP_ORDER",
    "StepName",
    "StepPlan",
    "describe_profile",
    "plan_steps",
    "plan_summary",
    "profile_from_scene",
]
