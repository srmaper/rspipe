"""SQLite 检索索引：把 STAC item 索引进 SQLite，提供 bbox / 时间 / 云量检索。

接口刻意与 PostGIS 后端同形（`search` / `upsert` / `stats`），
便于后续替换成 PostGIS + pgstac 而不改调用方。
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from rspipe.errors import StacError
from rspipe.logging import get_logger

log = get_logger("rspipe.stac.index")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS items (
    id            TEXT NOT NULL,
    collection    TEXT NOT NULL,
    datetime      TEXT NOT NULL,
    bbox_minx     REAL,
    bbox_miny     REAL,
    bbox_maxx     REAL,
    bbox_maxy     REAL,
    cloud_cover   REAL,
    platform      TEXT,
    sensor_key    TEXT,
    href          TEXT NOT NULL,
    assets        TEXT,
    properties    TEXT,
    created_at    TEXT NOT NULL,
    PRIMARY KEY (collection, id)
);
CREATE INDEX IF NOT EXISTS idx_items_datetime   ON items (datetime);
CREATE INDEX IF NOT EXISTS idx_items_collection ON items (collection);
CREATE INDEX IF NOT EXISTS idx_items_cloud      ON items (cloud_cover);
CREATE INDEX IF NOT EXISTS idx_items_bbox       ON items (bbox_minx, bbox_miny, bbox_maxx, bbox_maxy);
"""


@dataclass(slots=True)
class IndexRecord:
    """索引中的一条记录。"""

    id: str
    collection: str
    datetime: str
    bbox: tuple[float, float, float, float]
    cloud_cover: float | None
    platform: str | None
    sensor_key: str | None
    href: str
    assets: dict[str, Any]
    properties: dict[str, Any]


class StacIndex:
    """基于 SQLite 的 STAC item 索引。"""

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    # ── 连接 ──────────────────────────────────────────────────────────────
    @contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(str(self.db_path), timeout=30.0)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _init_schema(self) -> None:
        with self._conn() as conn:
            conn.executescript(_SCHEMA)

    # ── 写入 ──────────────────────────────────────────────────────────────
    def upsert(self, record: IndexRecord) -> None:
        now = datetime.utcnow().isoformat() + "Z"
        with self._conn() as conn:
            conn.execute(
                """
                INSERT INTO items (id, collection, datetime,
                                   bbox_minx, bbox_miny, bbox_maxx, bbox_maxy,
                                   cloud_cover, platform, sensor_key,
                                   href, assets, properties, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (collection, id) DO UPDATE SET
                    datetime=excluded.datetime,
                    bbox_minx=excluded.bbox_minx,
                    bbox_miny=excluded.bbox_miny,
                    bbox_maxx=excluded.bbox_maxx,
                    bbox_maxy=excluded.bbox_maxy,
                    cloud_cover=excluded.cloud_cover,
                    platform=excluded.platform,
                    sensor_key=excluded.sensor_key,
                    href=excluded.href,
                    assets=excluded.assets,
                    properties=excluded.properties
                """,
                (
                    record.id,
                    record.collection,
                    record.datetime,
                    record.bbox[0],
                    record.bbox[1],
                    record.bbox[2],
                    record.bbox[3],
                    record.cloud_cover,
                    record.platform,
                    record.sensor_key,
                    record.href,
                    json.dumps(record.assets, ensure_ascii=False),
                    json.dumps(record.properties, ensure_ascii=False),
                    now,
                ),
            )
        log.debug("index.upsert", id=record.id, collection=record.collection)

    def delete(self, collection: str, item_id: str) -> bool:
        with self._conn() as conn:
            cur = conn.execute(
                "DELETE FROM items WHERE collection=? AND id=?", (collection, item_id)
            )
            return cur.rowcount > 0

    def delete_collection(self, collection: str) -> int:
        with self._conn() as conn:
            cur = conn.execute("DELETE FROM items WHERE collection=?", (collection,))
            return cur.rowcount

    def bulk_upsert(self, records: Sequence[IndexRecord]) -> int:
        for r in records:
            self.upsert(r)
        return len(records)

    # ── 检索 ──────────────────────────────────────────────────────────────
    def search(
        self,
        *,
        bbox: tuple[float, float, float, float] | None = None,
        start: datetime | str | None = None,
        end: datetime | str | None = None,
        max_cloud_cover: float | None = None,
        collection: str | None = None,
        platform: str | None = None,
        sensor_key: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[IndexRecord]:
        """检索索引。bbox 为 EPSG:4326 (minx,miny,maxx,maxy)，与结果做相交判断。"""
        where: list[str] = []
        args: list[Any] = []

        if collection:
            where.append("collection = ?")
            args.append(collection)
        if platform:
            where.append("platform = ?")
            args.append(platform)
        if sensor_key:
            where.append("sensor_key = ?")
            args.append(sensor_key)
        if max_cloud_cover is not None:
            where.append("(cloud_cover IS NULL OR cloud_cover <= ?)")
            args.append(max_cloud_cover)
        if start is not None:
            where.append("datetime >= ?")
            args.append(_iso(start))
        if end is not None:
            where.append("datetime <= ?")
            args.append(_iso(end))
        if bbox is not None:
            minx, miny, maxx, maxy = bbox
            # 包围盒相交
            where.append(
                "(bbox_minx <= ? AND bbox_maxx >= ? AND bbox_miny <= ? AND bbox_maxy >= ?)"
            )
            args.extend([maxx, minx, maxy, miny])

        sql = "SELECT * FROM items"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY datetime DESC LIMIT ? OFFSET ?"
        args.extend([int(limit), int(offset)])

        with self._conn() as conn:
            rows = conn.execute(sql, args).fetchall()
        return [self._row_to_record(r) for r in rows]

    def count(self, **filters: Any) -> int:
        recs = self.search(limit=1_000_000, **filters)
        return len(recs)

    def stats(self) -> dict[str, Any]:
        with self._conn() as conn:
            total = conn.execute("SELECT COUNT(*) FROM items").fetchone()[0]
            by_coll = dict(
                conn.execute(
                    "SELECT collection, COUNT(*) FROM items GROUP BY collection"
                ).fetchall()
            )
            cloud = conn.execute(
                "SELECT AVG(cloud_cover), MIN(cloud_cover), MAX(cloud_cover) "
                "FROM items WHERE cloud_cover IS NOT NULL"
            ).fetchone()
        return {
            "total": total,
            "by_collection": by_coll,
            "cloud_cover": {
                "avg": cloud[0],
                "min": cloud[1],
                "max": cloud[2],
            },
        }

    def get(self, collection: str, item_id: str) -> IndexRecord | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM items WHERE collection=? AND id=?", (collection, item_id)
            ).fetchone()
        return self._row_to_record(row) if row else None

    def collections(self) -> list[str]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT DISTINCT collection FROM items ORDER BY collection"
            ).fetchall()
        return [r[0] for r in rows]

    @staticmethod
    def _row_to_record(row: sqlite3.Row) -> IndexRecord:
        bbox = (
            row["bbox_minx"] or 0.0,
            row["bbox_miny"] or 0.0,
            row["bbox_maxx"] or 0.0,
            row["bbox_maxy"] or 0.0,
        )
        return IndexRecord(
            id=row["id"],
            collection=row["collection"],
            datetime=row["datetime"],
            bbox=bbox,
            cloud_cover=row["cloud_cover"],
            platform=row["platform"],
            sensor_key=row["sensor_key"],
            href=row["href"],
            assets=json.loads(row["assets"] or "{}"),
            properties=json.loads(row["properties"] or "{}"),
        )


def record_from_item_dict(item: dict[str, Any], href: str) -> IndexRecord:
    """从 STAC Item dict 构造索引记录。"""
    props = item.get("properties") or {}
    bbox = item.get("bbox") or [0.0, 0.0, 0.0, 0.0]
    if len(bbox) < 4:
        bbox = [0.0, 0.0, 0.0, 0.0]
    dt = props.get("datetime") or item.get("datetime")
    if not dt:
        raise StacError(f"item {item.get('id')!r} has no datetime")
    return IndexRecord(
        id=str(item.get("id")),
        collection=str(item.get("collection") or "unknown"),
        datetime=str(dt),
        bbox=(float(bbox[0]), float(bbox[1]), float(bbox[2]), float(bbox[3])),
        cloud_cover=props.get("eo:cloud_cover"),
        platform=props.get("platform"),
        sensor_key=props.get("rspi:sensor_key") or props.get("rspi:source_collection"),
        href=href,
        assets=item.get("assets") or {},
        properties=props,
    )


def _iso(value: datetime | str) -> str:
    if isinstance(value, datetime):
        return value.isoformat().replace("+00:00", "Z")
    return str(value)


__all__ = ["IndexRecord", "StacIndex", "record_from_item_dict"]
