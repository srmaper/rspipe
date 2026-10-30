"""归档解包：.SAFE / .tar / .zip 及产品结构解析。"""

from __future__ import annotations

import shutil
import tarfile
import zipfile
from pathlib import Path

from rspipe.errors import UnpackError
from rspipe.logging import get_logger

log = get_logger("rspipe.ingest.unpack")


def unpack_archive(archive: Path, dest_dir: Path) -> Path:
    """解包归档到 `dest_dir`，返回产品根目录。"""
    if not archive.exists():
        raise UnpackError(f"archive not found: {archive}")
    dest_dir.mkdir(parents=True, exist_ok=True)
    name = archive.name.lower()

    if name.endswith((".tar", ".tar.gz", ".tgz")):
        return _untar(archive, dest_dir)
    if name.endswith(".zip") or name.endswith(".safe"):
        return _unzip(archive, dest_dir)
    if archive.is_dir():
        return archive
    # 普通文件：直接视为产品根
    log.info("unpack.skip", archive=str(archive), reason="not-an-archive")
    return archive


def _untar(archive: Path, dest_dir: Path) -> Path:
    try:
        with tarfile.open(archive, "r:*") as tf:
            tf.extractall(dest_dir, filter="data")
    except tarfile.TarError as exc:
        raise UnpackError(f"cannot untar {archive}: {exc}") from exc
    root = _find_product_root(dest_dir)
    log.info("unpack.done", kind="tar", root=str(root))
    return root


def _unzip(archive: Path, dest_dir: Path) -> Path:
    try:
        with zipfile.ZipFile(archive) as zf:
            zf.extractall(dest_dir)
    except zipfile.BadZipFile as exc:
        raise UnpackError(f"cannot unzip {archive}: {exc}") from exc
    root = _find_product_root(dest_dir)
    log.info("unpack.done", kind="zip", root=str(root))
    return root


def _find_product_root(dest_dir: Path) -> Path:
    """在解包结果里找产品根目录（含 .SAFE 或 MTD_*.xml / MTL.txt）。"""
    candidates: list[Path] = []
    for p in dest_dir.rglob("*"):
        if p.is_dir() and p.name.upper().endswith(".SAFE"):
            candidates.append(p)
    if candidates:
        return sorted(candidates, key=lambda p: len(p.parts))[0]

    for marker in ("MTD_MSIL2A.xml", "MTD_MSIL1C.xml", "MTD_SAFL1C.xml", "MTL.txt"):
        hits = list(dest_dir.rglob(marker))
        if hits:
            return hits[0].parent

    return dest_dir


def copy_tree(src: Path, dest: Path) -> Path:
    if src.resolve() == dest.resolve():
        return dest
    if src.is_dir():
        shutil.copytree(src, dest, dirs_exist_ok=True)
    else:
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest)
    return dest


def find_metadata(product_root: Path) -> Path | None:
    """定位产品元数据文件（MTL / MTD）。"""
    for pattern in ("MTL.txt", "MTD_*.xml", "*.json"):
        hits = sorted(product_root.rglob(pattern))
        if hits:
            return hits[0]
    return None


__all__ = ["copy_tree", "find_metadata", "unpack_archive"]
