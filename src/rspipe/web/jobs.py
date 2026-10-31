"""任务执行器：raw 全波段 / processed 成果，后台线程 + 内存注册表 + WS 推送。"""

from __future__ import annotations

import logging
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

from rspipe.errors import JobCancelledError

log = logging.getLogger(__name__)


# 常见底层错误的短文案（报错直出 toast，必须是人话）
_FALLBACK_ERR = "任务失败"


def _friendly_error(msg: str) -> str:
    m = (msg or "").strip()
    if "Permission denied" in m or "PermissionError" in m:
        return "成果文件被占用（可能有同景任务并发写入），等待几秒后重试即可"
    if "Server disconnected" in m or "RemoteProtocolError" in m or "range fetch failed" in m:
        return "网络连接被服务端断开，重试即可（已自动重传多次仍失败）"
    if "not found" in m.lower() and "raw" in m.lower():
        return "该景在此数据源已下架，请换一景"
    first = m.splitlines()[0] if m else _FALLBACK_ERR
    return (first[:180] + "…") if len(first) > 180 else first or _FALLBACK_ERR


@dataclass
class Job:
    id: str
    kind: str
    scene_id: str
    provider: str
    collection: str
    aoi_bbox: tuple[float, float, float, float]
    status: str = "queued"  # queued/running/ok/error
    progress: float = 0.0  # 0..1
    message: str = ""
    detail: dict = field(default_factory=dict)
    created: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "type": self.kind,
            "scene_id": self.scene_id,
            "provider": self.provider,
            "collection": self.collection,
            "status": self.status,
            "progress": round(self.progress, 4),
            "message": self.message,
            "detail": self.detail,
            "created": self.created,
        }


class JobCenter:
    """线程池 + 注册表（sqlite 持久化，重启不丢）+ WS 推送。"""

    def __init__(self, config, settings, workers: int = 2):
        from rspipe.pipeline import Pipeline
        from rspipe.storage.layout import DataLayout

        self._cfg = config
        self._settings = settings
        self._pipe = Pipeline.from_config(config, settings)
        self._layout = DataLayout(config.data_root)
        self.jobs: dict[str, Job] = {}
        self._snap: dict[str, dict] = {}
        self._pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="rsw")
        self._lock = threading.Lock()
        self._locks: dict[str, threading.Lock] = {}
        self._cancels: dict[str, threading.Event] = {}
        self._db_init()
        self._db_load()

    # ---- 持久化 ----
    def _db_path(self) -> Path:
        return Path(self._cfg.data_root) / "index" / "web_jobs.sqlite"

    def _db_init(self) -> None:
        import sqlite3

        p = self._db_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        con = sqlite3.connect(p)
        con.execute(
            "CREATE TABLE IF NOT EXISTS jobs ("
            "id TEXT PRIMARY KEY, kind TEXT, scene_id TEXT, provider TEXT,"
            "collection TEXT, aoi_bbox TEXT, status TEXT, progress REAL,"
            "message TEXT, detail TEXT, created REAL)"
        )
        con.commit()
        con.close()

    def _db_save(self, job: Job) -> None:
        import json as _json
        import sqlite3

        try:
            con = sqlite3.connect(self._db_path())
            con.execute(
                "INSERT OR REPLACE INTO jobs VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (
                    job.id,
                    job.kind,
                    job.scene_id,
                    job.provider,
                    job.collection,
                    _json.dumps(list(job.aoi_bbox)),
                    job.status,
                    job.progress,
                    job.message,
                    _json.dumps(job.detail),
                    job.created,
                ),
            )
            con.commit()
            con.close()
        except Exception:
            pass

    def _db_delete(self, ids) -> None:
        if not ids:
            return
        import sqlite3

        try:
            con = sqlite3.connect(self._db_path())
            con.executemany("DELETE FROM jobs WHERE id = ?", [(i,) for i in ids])
            con.commit()
            con.close()
        except Exception:
            pass

    def _db_load(self) -> None:
        import json as _json
        import sqlite3

        try:
            con = sqlite3.connect(self._db_path())
            rows = con.execute("SELECT * FROM jobs ORDER BY created DESC LIMIT 200").fetchall()
            con.close()
        except Exception:
            return
        for r in rows:
            try:
                job = Job(
                    id=r[0],
                    kind=r[1],
                    scene_id=r[2],
                    provider=r[3],
                    collection=r[4],
                    aoi_bbox=tuple(_json.loads(r[5])),
                    status=r[6],
                    progress=r[7],
                    message=r[8],
                    detail=_json.loads(r[9] or "{}"),
                    created=r[10],
                )
                # 服务重启时被打断的任务标记为失败（重试按钮仍在景卡片）
                if job.status in ("queued", "running"):
                    job.status = "error"
                    job.message = "已中断（可续传，已完成波段会跳过）"
                self.jobs[job.id] = job
            except Exception:
                continue

    # ---- 查询 ----
    def snapshot(self) -> list[dict]:
        return [j.to_dict() for j in sorted(self.jobs.values(), key=lambda j: -j.created)]

    def diff(self) -> dict[str, dict] | None:
        """返回与上次快照的增量（全量快照 diff 太小时返回 None）。"""
        snap = {j.id: j.to_dict() for j in self.jobs.values()}
        if snap == self._snap:
            return None
        self._snap = dict(snap)
        return snap

    def get(self, job_id: str) -> Job | None:
        return self.jobs.get(job_id)

    def submit(self, req) -> Job:
        # 同景 processed/raw 任务去重：并发跑同一 SceneProcessor 互相踩 staging 文件
        active = [
            j
            for j in self.jobs.values()
            if j.scene_id == req.scene_id
            and j.kind == req.type
            and j.status in ("queued", "running")
        ]
        if active:
            j = active[0]
            j.message = "已忽略重复提交（同景任务仍在队列/运行中）"
            return j
        # 清掉同景同类型的旧终态记录，避免列表堆积重复失败项
        stale = [
            jid
            for jid, j in self.jobs.items()
            if j.scene_id == req.scene_id and j.kind == req.type and j.status in ("ok", "error")
        ]
        for jid in stale:
            self.jobs.pop(jid, None)
        self._db_delete(stale)

        job = Job(
            id=uuid.uuid4().hex[:12],
            kind=req.type,
            scene_id=req.scene_id,
            provider=req.provider,
            collection=req.collection,
            aoi_bbox=tuple(req.aoi_bbox),
            detail={"bands": list(req.bands)} if req.bands else {},
        )
        self.jobs[job.id] = job
        self._cancels[job.id] = threading.Event()
        self._pool.submit(self._run, job)
        return job

    def pause(self, job_id: str) -> bool:
        """请求暂停：置取消位，下载线程会在下一个分片退出（已完成波段保留）。"""
        job = self.jobs.get(job_id)
        if job is None or job.status not in ("queued", "running"):
            return False
        ev = self._cancels.get(job_id)
        if ev is not None:
            ev.set()
        with self._lock:
            job.status = "paused"
            job.message = "已暂停（可继续，已完成波段会跳过）"
        self._db_save(job)
        return True

    def resume(self, job_id: str) -> bool:
        """继续：清取消位重新入队，已存在的波段文件会被跳过。"""
        job = self.jobs.get(job_id)
        if job is None or job.status not in ("paused", "error"):
            return False
        self._cancels[job_id] = threading.Event()
        with self._lock:
            job.status = "queued"
            job.message = "继续下载…"
        self._db_save(job)
        self._pool.submit(self._run, job)
        return True

    def _scene_lock(self, scene_id: str) -> threading.Lock:
        with self._lock:
            return self._locks.setdefault(f"scene:{scene_id}", threading.Lock())

    # ---- 执行 ----
    def _run(self, job: Job) -> None:
        # 同景串行化（不同类型任务也不允许同时写同一 staging 目录）
        with self._scene_lock(f"{job.provider}/{job.scene_id}"):
            self._run_inner(job)
        self._db_save(job)  # 终态兜底落盘

    def _tick_bytes(self, n: int, final: bool = True) -> None:
        """Range 回调：分片推进进度条与 MB 显示；整块完成才结算 bytes 计数。"""
        for job in list(self.jobs.values()):
            if job.status != "running":
                continue
            done = job.detail.get("bytes", 0) or 0
            partial = job.detail.get("partial", 0) or 0
            if final:
                done += n
                job.detail["bytes"] = done
                job.detail["partial"] = 0
            else:
                partial += n
                job.detail["partial"] = partial
            shown_mb = (done + partial) >> 20
            self._prog(job, min(0.85, job.progress + (n / 4e7)), f"下载中 {shown_mb} MB")

    def _run_inner(self, job: Job) -> None:
        try:
            from rspipe.ingest.rangefile import set_progress_hook

            set_progress_hook(self._tick_bytes)
            job.status = "running"
            from rspipe.pipeline.steps import SceneContext, SceneProcessor
            from rspipe.web.download import download_scene_raw

            if job.kind == "raw":
                download_scene_raw(
                    self._pipe,
                    self._layout,
                    job.scene_id,
                    job.provider,
                    job.collection,
                    tuple(job.aoi_bbox),
                    keys=job.detail.get("bands"),
                    progress=lambda f, m: self._prog(job, f, m),
                    should_stop=self._should_stop(job),
                )
                job.status = "ok"
                job.progress = 1.0
                job.message = "raw 完成"
            else:
                self._prog(job, 0.0, "准备 …")
                scene = self._pipe.clients[job.provider].search(ids=[job.scene_id], max_items=1)[0]
                scene.provider = job.provider
                ctx = SceneContext(
                    scene=scene,
                    config=self._cfg,
                    layout=self._layout,
                    auth=self._pipe._auth_for(job.provider),
                    spec=self._pipe._spec_for(scene),
                    band_names=tuple(self._cfg.pipeline.bands),
                    collection_id="default",
                    aoi_bbox=(
                        float(job.aoi_bbox[0]),
                        float(job.aoi_bbox[1]),
                        float(job.aoi_bbox[2]),
                        float(job.aoi_bbox[3]),
                    ),
                )
                import rspipe.pipeline.steps as steps_mod
                from rspipe.ingest.fetcher import fetch_asset as real_fetch
                from rspipe.web.download import local_raw

                orig = steps_mod.fetch_asset

                def fake(asset, dest, *, auth=None, mode=None, bbox=None, timeout=120.0):
                    p = local_raw(
                        self._layout, job.provider, job.scene_id, getattr(asset, "key", "")
                    )
                    if p:
                        return str(p)
                    return real_fetch(asset, dest, auth=auth, mode=mode, bbox=bbox, timeout=timeout)

                steps_mod.fetch_asset = fake
                try:
                    result = SceneProcessor(ctx).run(force=False)
                finally:
                    steps_mod.fetch_asset = orig
                if result.error:
                    job.status = "error"
                    job.message = _friendly_error(result.error)
                else:
                    job.status = "ok"
                    job.progress = 1.0
                    job.detail = {"cog": str(result.cog_path or "")}
                    job.message = "成果 COG 就绪"
        except JobCancelledError:
            job.status = "paused"
            job.message = "已暂停（可继续，已完成波段会跳过）"
            log.info("job %s paused", job.id)
        except Exception as exc:
            job.status = "error"
            job.message = _friendly_error(f"{type(exc).__name__}: {exc}")
            log.exception("job %s failed", job.id)

    def _should_stop(self, job: Job):
        ev = self._cancels.get(job.id)
        if ev is None:
            return None
        return ev.is_set

    def _prog(self, job: Job, f: float, m: str) -> None:
        with self._lock:
            job.progress = min(1.0, max(job.progress, f))
            job.message = m
        # 节流落盘：0.02 进度或终态才写库
        if (
            job.status in ("ok", "error")
            or abs(job.progress - job.detail.get("saved_prog", 0)) >= 0.02
        ):
            job.detail["saved_prog"] = job.progress
            self._db_save(job)
