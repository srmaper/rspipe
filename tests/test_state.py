"""流水线状态（水位线 / 已处理清单 / 运行记录）单测。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from rspipe.pipeline.state import PipelineState, ProcessedRecord


def test_watermark_roundtrip(tmp_path: Path):
    st = PipelineState(tmp_path / "s.sqlite")
    assert st.get_watermark("p", "c", "a") is None
    now = datetime(2026, 5, 1, tzinfo=UTC)
    st.set_watermark("p", "c", "a", now)
    wm = st.get_watermark("p", "c", "a")
    assert wm is not None
    assert wm.last_dt == now
    assert wm.updated_at is not None


def test_watermark_advance_keeps_lookback(tmp_path: Path):
    st = PipelineState(tmp_path / "s.sqlite")
    end = datetime(2026, 5, 10, tzinfo=UTC)
    new = st.advance_watermark("p", "c", "a", end, lookback_days=3)
    assert new == end - timedelta(days=3)


def test_watermark_advance_never_goes_backwards(tmp_path: Path):
    st = PipelineState(tmp_path / "s.sqlite")
    later = datetime(2026, 5, 10, tzinfo=UTC)
    st.advance_watermark("p", "c", "a", later, lookback_days=3)
    earlier = datetime(2026, 5, 2, tzinfo=UTC)
    new = st.advance_watermark("p", "c", "a", earlier, lookback_days=3)
    # 不回退
    assert new == later - timedelta(days=3)


def test_processed_tracking(tmp_path: Path):
    st = PipelineState(tmp_path / "s.sqlite")
    assert not st.is_processed("p", "s1")
    st.mark_processed(
        ProcessedRecord(
            scene_id="s1",
            provider="p",
            collection="c",
            processed_at=datetime(2026, 5, 1, tzinfo=UTC),
            cog_path="/x/s1.tif",
            status="ok",
        )
    )
    assert st.is_processed("p", "s1")
    assert st.processed_count() == 1
    assert st.processed_count("p") == 1
    assert st.processed_count("other") == 0


def test_processed_failed_not_counted_as_done(tmp_path: Path):
    st = PipelineState(tmp_path / "s.sqlite")
    st.mark_processed(
        ProcessedRecord(
            scene_id="s1",
            provider="p",
            collection="c",
            processed_at=datetime(2026, 5, 1, tzinfo=UTC),
            status="failed",
            error="boom",
        )
    )
    assert not st.is_processed("p", "s1")


def test_processed_upsert_overwrites(tmp_path: Path):
    st = PipelineState(tmp_path / "s.sqlite")
    rec = ProcessedRecord(
        scene_id="s1",
        provider="p",
        collection="c",
        processed_at=datetime(2026, 5, 1, tzinfo=UTC),
        status="failed",
    )
    st.mark_processed(rec)
    st.mark_processed(
        ProcessedRecord(
            scene_id="s1",
            provider="p",
            collection="c",
            processed_at=datetime(2026, 5, 2, tzinfo=UTC),
            status="ok",
        )
    )
    assert st.processed_count() == 1
    assert st.is_processed("p", "s1")


def test_run_lifecycle(tmp_path: Path):
    st = PipelineState(tmp_path / "s.sqlite")
    run = st.start_run()
    assert run.status == "running"
    run.scenes_found = 5
    run.scenes_ok = 4
    run.scenes_failed = 1
    st.finish_run(run, status="partial", message="4/5")
    runs = st.recent_runs()
    assert len(runs) == 1
    assert runs[0].status == "partial"
    assert runs[0].scenes_ok == 4
    assert runs[0].finished_at is not None


def test_recent_processed_ordering(tmp_path: Path):
    st = PipelineState(tmp_path / "s.sqlite")
    for i in range(5):
        st.mark_processed(
            ProcessedRecord(
                scene_id=f"s{i}",
                provider="p",
                collection="c",
                processed_at=datetime(2026, 5, 1 + i, tzinfo=UTC),
                status="ok",
            )
        )
    recent = st.recent_processed(limit=3)
    assert [r.scene_id for r in recent] == ["s4", "s3", "s2"]
