"""流水线状态：增量水位线 + 运行记录（SQLite）。

水位线记录 `(provider, collection, aoi_id) -> last_datetime`，
用于增量检索；运行记录用于排查与监控。
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from rspipe.logging import get_logger

log = get_logger("rspipe.pipeline.state")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS watermark (
    provider    TEXT NOT NULL,
    collection  TEXT NOT NULL,
    aoi_id      TEXT NOT NULL,
    last_dt     TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    PRIMARY KEY (provider, collection, aoi_id)
);

CREATE TABLE IF NOT EXISTS processed (
    scene_id     TEXT NOT NULL,
    provider     TEXT NOT NULL,
    collection   TEXT NOT NULL,
    processed_at TEXT NOT NULL,
    cog_path     TEXT,
    item_path    TEXT,
    status       TEXT NOT NULL,
    error        TEXT,
    PRIMARY KEY (provider, scene_id)
);
CREATE INDEX IF NOT EXISTS idx_processed_at ON processed (processed_at);

CREATE TABLE IF NOT EXISTS runs (
    run_id        TEXT PRIMARY KEY,
    started_at    TEXT NOT NULL,
    finished_at   TEXT,
    status        TEXT NOT NULL,
    scenes_found  INTEGER DEFAULT 0,
    scenes_ok     INTEGER DEFAULT 0,
    scenes_failed INTEGER DEFAULT 0,
    message       TEXT
);
"""


def _now() -> datetime:
    return datetime.now(UTC)


def _iso(dt: datetime) -> str:
    return dt.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _parse(text: str) -> datetime:
    return datetime.fromisoformat(text.replace("Z", "+00:00"))


@dataclass(slots=True)
class Watermark:
    provider: str
    collection: str
    aoi_id: str
    last_dt: datetime
    updated_at: datetime | None = None


@dataclass(slots=True)
class ProcessedRecord:
    scene_id: str
    provider: str
    collection: str
    processed_at: datetime
    cog_path: str | None = None
    item_path: str | None = None
    status: str = "ok"
    error: str | None = None


@dataclass(slots=True)
class RunRecord:
    run_id: str
    started_at: datetime
    finished_at: datetime | None = None
    status: str = "running"
    scenes_found: int = 0
    scenes_ok: int = 0
    scenes_failed: int = 0
    message: str = ""


class PipelineState:
    """水位线 + 已处理清单 + 运行记录。"""

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init()

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

    def _init(self) -> None:
        with self._conn() as conn:
            conn.executescript(_SCHEMA)

    # ── 水位线 ────────────────────────────────────────────────────────────
    def get_watermark(
        self, provider: str, collection: str, aoi_id: str
    ) -> Watermark | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM watermark WHERE provider=? AND collection=? AND aoi_id=?",
                (provider, collection, aoi_id),
            ).fetchone()
        if not row:
            return None
        return Watermark(
            provider=row["provider"],
            collection=row["collection"],
            aoi_id=row["aoi_id"],
            last_dt=_parse(row["last_dt"]),
            updated_at=_parse(row["updated_at"]) if row["updated_at"] else None,
        )

    def set_watermark(
        self, provider: str, collection: str, aoi_id: str, last_dt: datetime
    ) -> None:
        with self._conn() as conn:
            conn.execute(
                """
                INSERT INTO watermark (provider, collection, aoi_id, last_dt, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT (provider, collection, aoi_id) DO UPDATE SET
                    last_dt=excluded.last_dt,
                    updated_at=excluded.updated_at
                """,
                (provider, collection, aoi_id, _iso(last_dt), _iso(_now())),
            )
        log.info("watermark.set", provider=provider, collection=collection, last=_iso(last_dt))

    def advance_watermark(
        self,
        provider: str,
        collection: str,
        aoi_id: str,
        candidate: datetime,
        *,
        lookback_days: int = 3,
    ) -> datetime:
        """推进水位线到 `candidate`，但保留 `lookback_days` 重叠窗防漏景。"""
        adjusted = candidate - timedelta(days=lookback_days)
        existing = self.get_watermark(provider, collection, aoi_id)
        new_dt = max(adjusted, existing.last_dt) if existing else adjusted
        self.set_watermark(provider, collection, aoi_id, new_dt)
        return new_dt

    def search_start(
        self,
        provider: str,
        collection: str,
        aoi_id: str,
        *,
        default_lookback_days: int = 3,
    ) -> datetime | None:
        """增量检索的起始时间；无水位线返回 None（全量）。"""
        wm = self.get_watermark(provider, collection, aoi_id)
        return wm.last_dt if wm else None

    # ── 已处理清单 ────────────────────────────────────────────────────────
    def is_processed(self, provider: str, scene_id: str) -> bool:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT 1 FROM processed WHERE provider=? AND scene_id=? AND status='ok'",
                (provider, scene_id),
            ).fetchone()
        return row is not None

    def mark_processed(self, rec: ProcessedRecord) -> None:
        with self._conn() as conn:
            conn.execute(
                """
                INSERT INTO processed
                    (scene_id, provider, collection, processed_at, cog_path, item_path, status, error)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (provider, scene_id) DO UPDATE SET
                    processed_at=excluded.processed_at,
                    cog_path=excluded.cog_path,
                    item_path=excluded.item_path,
                    status=excluded.status,
                    error=excluded.error
                """,
                (
                    rec.scene_id,
                    rec.provider,
                    rec.collection,
                    _iso(rec.processed_at),
                    rec.cog_path,
                    rec.item_path,
                    rec.status,
                    rec.error,
                ),
            )
        log.debug(
            "processed.mark",
            scene=rec.scene_id,
            status=rec.status,
        )

    def processed_count(self, provider: str | None = None) -> int:
        sql = "SELECT COUNT(*) FROM processed"
        args: tuple[Any, ...] = ()
        if provider:
            sql += " WHERE provider=?"
            args = (provider,)
        with self._conn() as conn:
            return int(conn.execute(sql, args).fetchone()[0])

    def recent_processed(self, limit: int = 20) -> list[ProcessedRecord]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM processed ORDER BY processed_at DESC LIMIT ?", (limit,)
            ).fetchall()
        return [
            ProcessedRecord(
                scene_id=r["scene_id"],
                provider=r["provider"],
                collection=r["collection"],
                processed_at=_parse(r["processed_at"]),
                cog_path=r["cog_path"],
                item_path=r["item_path"],
                status=r["status"],
                error=r["error"],
            )
            for r in rows
        ]

    # ── 运行记录 ──────────────────────────────────────────────────────────
    def start_run(self, run_id: str | None = None) -> RunRecord:
        rid = run_id or _now().strftime("%Y%m%dT%H%M%S")
        rec = RunRecord(run_id=rid, started_at=_now(), status="running")
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO runs (run_id, started_at, status) VALUES (?, ?, ?)",
                (rid, _iso(rec.started_at), rec.status),
            )
        log.info("run.start", run_id=rid)
        return rec

    def finish_run(self, rec: RunRecord, *, status: str = "ok", message: str = "") -> None:
        rec.finished_at = _now()
        rec.status = status
        rec.message = message
        with self._conn() as conn:
            conn.execute(
                """
                UPDATE runs SET finished_at=?, status=?, scenes_found=?, scenes_ok=?,
                                scenes_failed=?, message=?
                WHERE run_id=?
                """,
                (
                    _iso(rec.finished_at),
                    status,
                    rec.scenes_found,
                    rec.scenes_ok,
                    rec.scenes_failed,
                    message,
                    rec.run_id,
                ),
            )
        log.info(
            "run.finish",
            run_id=rec.run_id,
            status=status,
            found=rec.scenes_found,
            ok=rec.scenes_ok,
            failed=rec.scenes_failed,
        )

    def recent_runs(self, limit: int = 20) -> list[RunRecord]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM runs ORDER BY started_at DESC LIMIT ?", (limit,)
            ).fetchall()
        return [
            RunRecord(
                run_id=r["run_id"],
                started_at=_parse(r["started_at"]),
                finished_at=_parse(r["finished_at"]) if r["finished_at"] else None,
                status=r["status"],
                scenes_found=r["scenes_found"] or 0,
                scenes_ok=r["scenes_ok"] or 0,
                scenes_failed=r["scenes_failed"] or 0,
                message=r["message"] or "",
            )
            for r in rows
        ]


__all__ = ["PipelineState", "ProcessedRecord", "RunRecord", "Watermark"]
