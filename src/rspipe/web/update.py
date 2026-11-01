"""版本更新检查：查询 GitHub Releases，判断是否有新版本。"""

from __future__ import annotations

import json
import time
import urllib.request
from pathlib import Path
from typing import Any

REPO = "srmaper/rspipe"
API = f"https://api.github.com/repos/{REPO}/releases/latest"
CACHE_TTL = 6 * 3600  # 6 小时


def _parse_ver(v: str) -> tuple[int, ...]:
    parts: list[int] = []
    for chunk in str(v).strip().lstrip("vV").split("."):
        digits = "".join(ch for ch in chunk if ch.isdigit())
        parts.append(int(digits) if digits else 0)
    return tuple(parts)


def is_newer(latest: str, current: str) -> bool:
    """1.2.0 > 1.1.9；1.1.1 > 1.1；无法解析时按 0 处理。"""
    a, b = _parse_ver(latest), _parse_ver(current)
    n = max(len(a), len(b))
    a = a + (0,) * (n - len(a))
    b = b + (0,) * (n - len(b))
    return a > b


def _decorate(info: dict[str, Any], current: str) -> dict[str, Any]:
    latest = str(info.get("latest") or "")
    out = dict(info)
    out["current"] = current
    out["has_update"] = bool(info.get("ok") and latest and is_newer(latest, current))
    return out


def check_update(
    data_root: Path,
    current: str,
    *,
    force: bool = False,
    ttl: int = CACHE_TTL,
    timeout: float = 6.0,
) -> dict[str, Any]:
    """查最新 release（带磁盘缓存）。网络失败时返回 ok=False，不抛异常。"""
    cache = Path(data_root) / "index" / "update_check.json"
    if not force and cache.exists():
        try:
            cached = json.loads(cache.read_text(encoding="utf-8"))
            if time.time() - float(cached.get("checked", 0)) < ttl:
                return _decorate(cached, current)
        except Exception:
            pass

    info: dict[str, Any] = {"checked": time.time(), "ok": False}
    try:
        req = urllib.request.Request(
            API,
            headers={
                "Accept": "application/vnd.github+json",
                "User-Agent": f"rspipe/{current}",
            },
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            rel = json.loads(resp.read().decode("utf-8"))
        assets = [a for a in rel.get("assets") or [] if str(a.get("name", "")).endswith(".exe")]
        asset = assets[0] if assets else {}
        info.update(
            {
                "ok": True,
                "latest": rel.get("tag_name") or rel.get("name") or "",
                "name": rel.get("name") or "",
                "notes": (rel.get("body") or "")[:4000],
                "page": rel.get("html_url") or "",
                "published": rel.get("published_at") or "",
                "asset": asset.get("browser_download_url") or "",
                "asset_name": asset.get("name") or "",
                "asset_size": asset.get("size") or 0,
            }
        )
    except Exception as exc:  # 网络不可达 / 限流 / 解析失败
        info["error"] = f"{type(exc).__name__}: {exc}"

    try:
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(info, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass
    return _decorate(info, current)
