"""共享 fixture：合成栅格 + 临时数据目录。"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

from synthetic import (
    make_multiband,
    make_qa_pixel,
    make_scl,
    make_single_band,
)


@pytest.fixture
def tmp_data(tmp_path: Path) -> Path:
    d = tmp_path / "data"
    d.mkdir()
    return d


@pytest.fixture
def band_files(tmp_path: Path) -> list[Path]:
    """4 个同网格单波段 GeoTIFF（blue/green/red/nir，uint16 DN）。"""
    bands = ["B02", "B03", "B04", "B05"]
    return [make_single_band(tmp_path / f"{b}.tif", seed=i) for i, b in enumerate(bands)]


@pytest.fixture
def stack_file(tmp_path: Path) -> Path:
    return make_multiband(
        tmp_path / "stack.tif",
        count=4,
        band_names=["blue", "green", "red", "nir"],
        seed=7,
    )


@pytest.fixture
def qa_file(tmp_path: Path) -> Path:
    return make_qa_pixel(tmp_path / "qa.tif", cloud_fraction=0.2, seed=3)


@pytest.fixture
def scl_file(tmp_path: Path) -> Path:
    return make_scl(tmp_path / "scl.tif", cloud_fraction=0.2, seed=4)
