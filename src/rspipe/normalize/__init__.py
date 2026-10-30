"""归一化：辐射定标、波段统一、重投影、COG 转换。"""

from __future__ import annotations

from rspipe.normalize.cog import cog_info, to_cog, validate_cog
from rspipe.normalize.harmonize import harmonize, normalize_dn
from rspipe.normalize.io import read_band, read_stack, reproject_to_grid, stack_bands, write_array
from rspipe.normalize.profile import StepPlan, plan_steps, plan_summary, profile_from_scene
from rspipe.normalize.reflectance import (
    BandScaling,
    ScalingSet,
    resolve_scaling,
    scaling_from_spec,
)

__all__ = [
    "BandScaling",
    "ScalingSet",
    "StepPlan",
    "cog_info",
    "harmonize",
    "normalize_dn",
    "plan_steps",
    "plan_summary",
    "profile_from_scene",
    "read_band",
    "read_stack",
    "reproject_to_grid",
    "resolve_scaling",
    "scaling_from_spec",
    "stack_bands",
    "to_cog",
    "validate_cog",
    "write_array",
]
