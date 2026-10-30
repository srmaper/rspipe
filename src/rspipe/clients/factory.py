"""数据源客户端工厂：按 ProviderConfig 分发到具体客户端。"""

from __future__ import annotations

from rspipe.auth import (
    AuthLike,
    NoAuth,
    StaticTokenAuth,
    TokenProvider,
)
from rspipe.clients.base import ProviderClient
from rspipe.clients.cdse_odata import CdseODataClient
from rspipe.clients.stac import StacClient
from rspipe.clients.usgs_m2m import UsgsM2MClient
from rspipe.config import PipelineConfig, ProviderConfig, Settings
from rspipe.errors import ConfigError
from rspipe.models import ProviderType


def build_client(cfg: ProviderConfig, settings: Settings) -> ProviderClient:
    """按 `cfg.type` 构造客户端实例。"""
    if cfg.type is ProviderType.STAC:
        return StacClient(cfg, settings)
    if cfg.type is ProviderType.USGS_M2M:
        return UsgsM2MClient(cfg, settings)
    if cfg.type is ProviderType.CDSE_ODATA:
        return CdseODataClient(cfg, settings)
    raise ConfigError(f"unsupported provider type: {cfg.type!r}")


def build_enabled_clients(
    config: PipelineConfig,
    settings: Settings,
    *,
    strict: bool = False,
) -> dict[str, ProviderClient]:
    """构造全部启用的数据源客户端。

    `strict=False`（默认）时，缺少凭证的客户端会被跳过并记 warning，
    让仅用公开端点（如 LandsatLook）的配置开箱即用。
    `strict=True` 时凭证缺失直接抛 `CredentialError`。
    """
    from rspipe.errors import CredentialError
    from rspipe.logging import get_logger

    log = get_logger("rspipe.clients.factory")
    out: dict[str, ProviderClient] = {}
    for cfg in config.enabled_providers():
        try:
            out[cfg.name] = build_client(cfg, settings)
        except CredentialError as exc:
            if strict:
                raise
            log.warning(
                "provider.skipped",
                provider=cfg.name,
                reason="missing-credentials",
                detail=str(exc),
            )
    return out


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
]
