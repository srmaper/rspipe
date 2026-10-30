"""云掩膜单测。"""

from __future__ import annotations

import numpy as np

from rspipe.process.clouds import (
    apply_mask,
    build_mask,
    decode_qa_pixel,
    decode_scl,
    dilate_mask,
    mask_to_u8,
)


def _qa_with_blob(cloud_frac: float = 0.0) -> np.ndarray:
    """构造连续云块的 QA_PIXEL（现实情形，非随机散点）。"""
    qa = np.zeros((100, 100), dtype="uint16")
    qa[30:50, 30:60] |= 1 << 3  # cloud
    qa[70:80, 70:90] |= 1 << 4  # shadow
    return qa


def test_qa_pixel_decode_bits():
    qa = _qa_with_blob()
    mask, fracs = decode_qa_pixel(qa)
    # 云 20x30=600，影 10x20=200 -> invalid 800/10000 = 8%
    assert abs(fracs["cloud_fraction"] - 0.06) < 1e-6
    assert abs(fracs["shadow_fraction"] - 0.02) < 1e-6
    assert mask.sum() == 10000 - 800


def test_qa_keep_snow_water():
    qa = np.zeros((10, 10), dtype="uint16")
    qa[0:5, :] |= 1 << 5  # snow
    qa[5:, :] |= 1 << 6  # water
    mask_keep, _ = decode_qa_pixel(qa, keep_snow=True, keep_water=True)
    assert mask_keep.all()
    mask_drop, _ = decode_qa_pixel(qa, keep_snow=False, keep_water=False)
    assert not mask_drop.any()


def test_scl_decode():
    scl = np.full((100, 100), 4, dtype="uint8")
    scl[20:40, 20:50] = 9  # cloud high
    scl[60:70, 10:30] = 3  # shadow
    mask, fracs = decode_scl(scl)
    assert fracs["cloud_fraction"] == (20 * 30) / 10000
    assert fracs["shadow_fraction"] == (10 * 20) / 10000
    assert mask.sum() == 10000 - 600 - 200


def test_dilate_erodes_clear_region():
    qa = _qa_with_blob()
    mask0, _ = decode_qa_pixel(qa)
    mask3 = dilate_mask(mask0, radius=3)
    # 膨胀只可能减少保留区
    assert mask3.sum() <= mask0.sum()
    # 边缘带被剔除
    assert (mask0 & ~mask3).sum() > 0


def test_dilate_zero_radius_noop():
    m = np.ones((5, 5), dtype=bool)
    assert dilate_mask(m, radius=0) is m


def test_build_mask_qa_and_scl():
    qa = _qa_with_blob()
    r = build_mask(qa, kind="qa_pixel", buffer_pixels=2)
    assert r.mask.dtype == bool
    assert 0 < r.clear_fraction < 1
    assert r.meta["kind"] == "qa_pixel"

    scl = np.full((50, 50), 4, dtype="uint8")
    r2 = build_mask(scl, kind="scl", buffer_pixels=0)
    assert r2.clear_fraction == 1.0


def test_build_mask_none():
    qa = np.zeros((8, 8), dtype="uint16")
    r = build_mask(qa, kind="none")
    assert r.mask.all()
    assert r.cloud_fraction == 0.0


def test_apply_mask_shapes():
    mask = np.zeros((4, 4), dtype=bool)
    mask[:, :2] = True
    arr2 = np.ones((4, 4), dtype="float32")
    out2 = apply_mask(arr2, mask, nodata=float("nan"))
    assert np.isnan(out2[:, 2:]).all()
    assert np.isfinite(out2[:, :2]).all()

    arr3 = np.ones((3, 4, 4), dtype="float32")
    out3 = apply_mask(arr3, mask, nodata=-1.0)
    assert (out3[:, :, 2:] == -1.0).all()


def test_mask_to_u8_values():
    m = np.array([[True, False], [False, True]])
    u = mask_to_u8(m, clear=255, invalid=0)
    assert u.dtype == np.uint8
    assert u[0, 0] == 255 and u[0, 1] == 0
