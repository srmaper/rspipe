"""流水线编排、增量发现与定时调度。"""

from __future__ import annotations

from rspipe.pipeline.runner import Pipeline, PipelineReport
from rspipe.pipeline.scheduler import build_cron_trigger, run_scheduler
from rspipe.pipeline.state import PipelineState, ProcessedRecord, RunRecord, Watermark
from rspipe.pipeline.steps import SceneContext, SceneProcessor

__all__ = [
    "Pipeline",
    "PipelineReport",
    "PipelineState",
    "ProcessedRecord",
    "RunRecord",
    "SceneContext",
    "SceneProcessor",
    "Watermark",
    "build_cron_trigger",
    "run_scheduler",
]
