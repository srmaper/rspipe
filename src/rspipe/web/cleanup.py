"""场景本地产物删除（raw 裁剪 / staging / COG / STAC json / 导出缓存）。"""

from __future__ import annotations

import shutil
from pathlib import Path


def delete_scene_data(root: Path, provider: str, scene_id: str) -> dict:
    removed = {"paths": []}
    targets = [
        root / "raw" / provider / scene_id,
        root / "staging" / scene_id,
        root / "derived" / "cog" / f"{scene_id}.tif",
        root / "web_export" / f"{scene_id}.zip",
    ]
    # STAC item 分散在各城市 collection 下
    for item in (root / "catalog").rglob(f"{scene_id}.json"):
        targets.append(item)

    for t in targets:
        if not t.exists():
            continue
        try:
            if t.is_dir():
                shutil.rmtree(t)
            else:
                t.unlink()
            removed["paths"].append(str(t.relative_to(root)))
        except Exception as exc:
            removed["paths"].append(f"FAILED {t.name}: {exc}")
    return removed
