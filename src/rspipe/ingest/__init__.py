"""数据接入：检索、资产获取、下载、解包。"""

from __future__ import annotations

from rspipe.ingest.downloader import download_file, head_size
from rspipe.ingest.fetcher import fetch_asset, is_remote, open_raster, to_vsi
from rspipe.ingest.unpack import copy_tree, find_metadata, unpack_archive

__all__ = [
    "copy_tree",
    "download_file",
    "fetch_asset",
    "find_metadata",
    "head_size",
    "is_remote",
    "open_raster",
    "to_vsi",
    "unpack_archive",
]
