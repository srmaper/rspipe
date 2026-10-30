"""APScheduler 定时调度入口。"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

from rspipe.errors import PipelineError
from rspipe.logging import get_logger

if TYPE_CHECKING:  # pragma: no cover
    from rspipe.pipeline.runner import Pipeline

log = get_logger("rspipe.pipeline.scheduler")


def build_cron_trigger(cron: dict[str, Any]) -> CronTrigger:
    """从配置里的 cron 映射构造 CronTrigger。"""
    allowed = {
        "year",
        "month",
        "day",
        "week",
        "day_of_week",
        "hour",
        "minute",
        "second",
        "timezone",
    }
    kwargs = {k: v for k, v in (cron or {}).items() if k in allowed and v is not None}
    if not kwargs:
        raise PipelineError(f"invalid cron config: {cron!r}")
    try:
        return CronTrigger(**kwargs)
    except Exception as exc:
        raise PipelineError(f"cannot build cron trigger from {cron!r}: {exc}") from exc


def run_scheduler(
    pipeline: Pipeline,
    *,
    max_instances: int = 1,
    coalesce: bool = True,
) -> None:
    """常驻运行定时任务。Ctrl-C 退出。"""
    cron = pipeline.config.schedule.cron
    trigger = build_cron_trigger(cron)

    scheduler = BlockingScheduler(timezone=str(cron.get("timezone", "UTC")))

    def job() -> None:
        log.info("scheduler.tick")
        try:
            report = pipeline.run_once()
            log.info("scheduler.done", summary=report.summary)
        except Exception as exc:
            log.error("scheduler.failed", error=str(exc))

    scheduler.add_job(
        job,
        trigger=trigger,
        id="rspipe-update",
        name="rspipe incremental update",
        max_instances=max_instances,
        coalesce=coalesce,
        replace_existing=True,
    )

    log.info(
        "scheduler.start",
        cron=cron,
        next_run=str(scheduler.get_job("rspipe-update").trigger),
    )
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        log.info("scheduler.stopped")


__all__ = ["build_cron_trigger", "run_scheduler"]
