"""影像处理链：去云、匀色、无缝镶嵌。"""

from __future__ import annotations

from rspipe.process.clouds import (
    MaskResult,
    apply_mask,
    build_mask,
    decode_qa_pixel,
    decode_scl,
    dilate_mask,
    mask_to_u8,
    write_mask,
)
from rspipe.process.color import (
    ColorReport,
    balance_collection,
    balance_scene,
    band_stats,
    choose_reference,
    match_histogram,
    match_quantile,
)
from rspipe.process.mosaic import (
    MosaicResult,
    MosaicSource,
    compute_target_grid,
    distance_weight,
    mosaic,
    warp_to_grid,
)

__all__ = [
    "ColorReport",
    "MaskResult",
    "MosaicResult",
    "MosaicSource",
    "apply_mask",
    "balance_collection",
    "balance_scene",
    "band_stats",
    "build_mask",
    "choose_reference",
    "compute_target_grid",
    "decode_qa_pixel",
    "decode_scl",
    "dilate_mask",
    "distance_weight",
    "mask_to_u8",
    "match_histogram",
    "match_quantile",
    "mosaic",
    "warp_to_grid",
    "write_mask",
]
