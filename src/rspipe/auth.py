"""数据源认证：OAuth2 client_credentials、USGS M2M token。"""

from __future__ import annotations

import threading
import time
from typing import Any

import httpx

from rspipe.errors import CredentialError
from rspipe.logging import get_logger

log = get_logger("rspipe.auth")


class TokenProvider:
    """带自动刷新的 OAuth2 client_credentials token 提供者。"""

    def __init__(
        self,
        token_url: str,
        client_id: str,
        client_secret: str,
        *,
        scope: str | None = None,
        skew_s: int = 30,
        timeout: float = 30.0,
    ) -> None:
        if not client_id or not client_secret:
            raise CredentialError("oauth2 requires both client_id and client_secret")
        self.token_url = token_url
        self.client_id = client_id
        self.client_secret = client_secret
        self.scope = scope
        self.skew_s = skew_s
        self.timeout = timeout
        self._lock = threading.Lock()
        self._token: str | None = None
        self._expires_at: float = 0.0

    def get_token(self, *, force: bool = False) -> str:
        with self._lock:
            if not force and self._token and time.time() < self._expires_at - self.skew_s:
                return self._token
            self._refresh()
            assert self._token is not None
            return self._token

    def _refresh(self) -> None:
        data: dict[str, str] = {
            "grant_type": "client_credentials",
            "client_id": self.client_id,
            "client_secret": self.client_secret,
        }
        if self.scope:
            data["scope"] = self.scope
        try:
            resp = httpx.post(self.token_url, data=data, timeout=self.timeout)
            resp.raise_for_status()
            payload: dict[str, Any] = resp.json()
        except httpx.HTTPError as exc:
            raise CredentialError(f"oauth2 token request failed: {exc}") from exc

        token = payload.get("access_token")
        if not token:
            raise CredentialError(f"oauth2 response missing access_token: {payload}")
        self._token = str(token)
        self._expires_at = time.time() + float(payload.get("expires_in", 600))
        log.debug("oauth2 token refreshed", expires_in=payload.get("expires_in"))

    def auth_header(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.get_token()}"}


class StaticTokenAuth:
    """静态 token（如 USGS M2M）。"""

    def __init__(self, token: str, *, scheme: str = "Bearer") -> None:
        if not token:
            raise CredentialError("token must not be empty")
        self.token = token
        self.scheme = scheme

    def auth_header(self) -> dict[str, str]:
        return {"Authorization": f"{self.scheme} {self.token}"}


class NoAuth:
    """公开端点。"""

    def auth_header(self) -> dict[str, str]:
        return {}


AuthLike = TokenProvider | StaticTokenAuth | NoAuth
