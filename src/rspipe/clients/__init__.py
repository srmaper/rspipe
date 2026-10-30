"""数据源客户端。

主路径：`StacClient`（pystac-client，对接 LandsatLook / CDSE STAC）
补充路径：`UsgsM2MClient`、`CdseODataClient`
"""

from __future__ import annotations

from rspipe.auth import (
    AuthLike,
    NoAuth,
    StaticTokenAuth,
    TokenProvider,
)
from rspipe.clients.base import ProviderClient
from rspipe.clients.cdse_odata import CdseODataClient
from rspipe.clients.factory import build_client, build_enabled_clients
from rspipe.clients.stac import StacClient, build_stac_client
from rspipe.clients.usgs_m2m import UsgsM2MClient

__all__ = [
    "AuthLike",
    "CdseODataClient",
    "NoAuth",
    "ProviderClient",
    "StacClient",
    "StaticTokenAuth",
    "TokenProvider",
    "UsgsM2MClient",
    "build_client",
    "build_enabled_clients",
    "build_stac_client",
]
