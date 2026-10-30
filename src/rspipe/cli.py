"""rspipe 命令行入口。"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import typer
from rich.console import Console
from rich.table import Table

from rspipe import __version__
from rspipe.config import load_pipeline_config
from rspipe.errors import RspipeError
from rspipe.logging import configure_logging
from rspipe.quality import rank_scenes

app = typer.Typer(
    name="rspipe",
    help="遥感卫星数据处理流水线：接入 / 归一化 / COG / 去云 / 匀色 / 镶嵌 / STAC",
    add_completion=False,
    no_args_is_help=True,
)
console = Console()
err_console = Console(stderr=True)

CONFIG_DIR = typer.Option("configs", "--config-dir", "-c", help="配置目录")
ENV_FILE = typer.Option(".env", "--env-file", help="凭证文件（.env）")
DATA_ROOT = typer.Option(None, "--data-root", help="覆盖数据根目录")


def _boot(config_dir: str, env_file: str, data_root: str | None) -> Any:
    cfg = load_pipeline_config(config_dir, env_file=env_file)
    if data_root:
        from rspipe.storage import build_layout

        cfg.data_root = Path(data_root).expanduser().resolve()
        build_layout(cfg.data_root)
    from rspipe.config import Settings

    configure_logging(cfg.logging.level, json_logs=cfg.logging.json_logs)
    settings = Settings(_env_file=env_file if Path(env_file).exists() else None)
    return cfg, settings


def _pipeline(config_dir: str, env_file: str, data_root: str | None) -> Any:
    from rspipe.pipeline import Pipeline

    cfg, settings = _boot(config_dir, env_file, data_root)
    return Pipeline.from_config(cfg, settings)


def _fail(msg: str) -> None:
    err_console.print(f"[red]error[/red] {msg}")
    raise typer.Exit(code=1)


@app.callback()
def main_callback(
    version: bool = typer.Option(False, "--version", help="显示版本并退出"),
) -> None:
    if version:
        console.print(f"rspipe {__version__}")
        raise typer.Exit()


# ── search ────────────────────────────────────────────────────────────────────
@app.command("search")
def cmd_search(
    config_dir: str = CONFIG_DIR,
    env_file: str = ENV_FILE,
    data_root: str | None = DATA_ROOT,
    bbox: str | None = typer.Option(None, help="minx,miny,maxx,maxy (EPSG:4326)"),
    start: str | None = typer.Option(None, help="起始日期 ISO8601"),
    end: str | None = typer.Option(None, help="结束日期 ISO8601"),
    cloud: float | None = typer.Option(None, "--cloud", help="整景云量上限 %%"),
    cloud_aoi: float | None = typer.Option(
        None, "--cloud-aoi", help="AOI 窗口实测云量上限 %%（重算 QA，较慢）"
    ),
    collections: str | None = typer.Option(None, help="逗号分隔的集合名，覆盖 providers.yaml"),
    limit: int = typer.Option(20, help="最多返回景数"),
    full: bool = typer.Option(False, "--full", help="忽略水位线，全量检索"),
) -> None:
    """按 AOI / 时间窗 / 云量检索影像。"""
    try:
        pipe = _pipeline(config_dir, env_file, data_root)
        if bbox:
            values = [float(x) for x in bbox.split(",")]
            if len(values) != 4:
                _fail("--bbox 需要 4 个数值: minx,miny,maxx,maxy")
            geom, bb = None, tuple(values)
        else:
            geom, bb = pipe._load_aoi()

        start_dt = _parse_dt(start)
        end_dt = _parse_dt(end) or datetime.now(UTC)
        scenes: list[Any] = []
        for name, client in pipe.clients.items():
            if collections:
                client.cfg.collections = [c.strip() for c in collections.split(",") if c.strip()]
            try:
                found = client.search(
                    bbox=bb,
                    geometry=geom if bb is None else None,
                    start=start_dt,
                    end=end_dt,
                    max_cloud_cover=cloud if cloud is not None else pipe.config.pipeline.max_cloud_cover,
                    max_items=limit,
                )
                scenes.extend(found)
            except RspipeError as exc:
                err_console.print(f"[yellow]warn[/yellow] {name}: {exc}")

        if bb is not None and cloud_aoi is not None:
            ranked = rank_scenes(scenes, bb, prefer_tier1=True, min_overlap=0.01)
            thresh = cloud_aoi / 100.0
            kept = [r for r in ranked if r.aoi_cloud is None or r.aoi_cloud <= thresh]
            console.print(
                f"AOI 云量≤ {cloud_aoi:.0f}%：保留 {len(kept)}/{len(ranked)}（其余被过滤）"
            )
            _print_ranked(kept[:limit])
        else:
            _print_scenes(scenes[:limit])
    except RspipeError as exc:
        _fail(str(exc))


# ── run ───────────────────────────────────────────────────────────────────────
@app.command("run")
def cmd_run(
    config_dir: str = CONFIG_DIR,
    env_file: str = ENV_FILE,
    data_root: str | None = DATA_ROOT,
    full: bool = typer.Option(False, "--full", help="忽略水位线，全量处理"),
    force: bool = typer.Option(False, "--force", help="重算已处理的景"),
    limit: int | None = typer.Option(None, help="本次最多处理景数"),
    bbox: str | None = typer.Option(None, help="minx,miny,maxx,maxy，覆盖 pipeline.aoi"),
    aoi: str | None = typer.Option(None, "--aoi", help="AOI GeoJSON 路径"),
    start: str | None = typer.Option(None, help="起始日期 ISO8601"),
    end: str | None = typer.Option(None, help="结束日期 ISO8601"),
    scene_id: str | None = typer.Option(
        None, "--scene-id", help="只用指定景（可逗号分隔多个），跳过发现"
    ),
    cloud_aoi: float | None = typer.Option(
        None, "--cloud-aoi", help="AOI 窗口实测云量上限 %%（Tier1 优先 + 云量排序）"
    ),
    bands: str | None = typer.Option(None, help="逗号分隔的通用波段名，如 red,green,blue,nir"),
    fetch_mode: str | None = typer.Option(
        None, "--fetch-mode", help="overriding providers.yaml：vsi | download | windowed"
    ),
) -> None:
    """执行一次增量更新（发现 -> 处理 -> 发布 STAC）。"""
    try:
        pipe = _pipeline(config_dir, env_file, data_root)
        cfg = pipe.config
        if bbox:
            values = [float(x) for x in bbox.split(",")]
            if len(values) != 4:
                _fail("--bbox 需要 4 个数值")
            cfg.pipeline.aoi = None
            pipe._cli_bbox = tuple(values)
        if aoi:
            cfg.pipeline.aoi = aoi
            pipe._cli_bbox = None
        if start:
            cfg.pipeline.start = start
        if end:
            cfg.pipeline.end = end
        if bands:
            wanted = [b.strip() for b in bands.split(",") if b.strip()]
            if not wanted:
                _fail("--bands 不能为空")
            cfg.pipeline.bands = wanted
        if fetch_mode:
            from rspipe.models import FetchMode

            try:
                fm = FetchMode(fetch_mode.lower())
            except ValueError:
                _fail("--fetch-mode 取值: vsi | download | windowed")
            for prov in cfg.providers.values():
                prov.fetch_mode = fm
        if limit:
            cfg.schedule.max_scenes_per_run = limit

        if scene_id:
            ids = [s.strip() for s in scene_id.split(",") if s.strip()]
            report = pipe.run_scene_ids(ids)
        elif cloud_aoi is not None:
            report = pipe.run_once(force_full=full, force=force, aoi_cloud_max=cloud_aoi / 100.0)
        else:
            report = pipe.run_once(force_full=full, force=force)
        _print_report(report)
        if report.status == "failed":
            raise typer.Exit(code=1)
    except RspipeError as exc:
        _fail(str(exc))


# ── mosaic ────────────────────────────────────────────────────────────────────
@app.command("mosaic")
def cmd_mosaic(
    config_dir: str = CONFIG_DIR,
    env_file: str = ENV_FILE,
    data_root: str | None = DATA_ROOT,
    date: str | None = typer.Option(None, help="输出目录日期键，默认今天"),
    feather: str | None = typer.Option(None, help="distance | first | mean"),
    scenes: str | None = typer.Option(None, help="逗号分隔的 scene_id 列表"),
) -> None:
    """对已处理景做匀色 + 羽化镶嵌。"""
    try:
        pipe = _pipeline(config_dir, env_file, data_root)
        ids = [s.strip() for s in scenes.split(",") if s.strip()] if scenes else None
        out = pipe.build_mosaic(scene_ids=ids, date=date, feather=feather)
        console.print(f"[green]mosaic[/green] -> {out}")
    except RspipeError as exc:
        _fail(str(exc))


# ── schedule ──────────────────────────────────────────────────────────────────
@app.command("schedule")
def cmd_schedule(
    config_dir: str = CONFIG_DIR,
    env_file: str = ENV_FILE,
    data_root: str | None = DATA_ROOT,
) -> None:
    """按配置的 cron 常驻运行定时更新。"""
    try:
        pipe = _pipeline(config_dir, env_file, data_root)
        console.print(f"调度启动，cron={pipe.config.schedule.cron}；Ctrl-C 退出")
        pipe.schedule()
    except RspipeError as exc:
        _fail(str(exc))


# ── status ────────────────────────────────────────────────────────────────────
@app.command("status")
def cmd_status(
    config_dir: str = CONFIG_DIR,
    env_file: str = ENV_FILE,
    data_root: str | None = DATA_ROOT,
) -> None:
    """查看处理进度、水位线与索引统计。"""
    try:
        cfg, _settings = _boot(config_dir, env_file, data_root)
        from rspipe.pipeline.state import PipelineState
        from rspipe.stac.store import StacIndex
        from rspipe.storage.layout import DataLayout

        layout = DataLayout(cfg.data_root)
        state = PipelineState(layout.runs_db)
        index = StacIndex(layout.catalog_index)

        table = Table(title=f"rspipe status  (data root: {cfg.data_root})")
        table.add_column("item")
        table.add_column("value")
        table.add_row("processed scenes", str(state.processed_count()))
        stats = index.stats()
        table.add_row("indexed items", str(stats["total"]))
        table.add_row("collections", ", ".join(index.collections()) or "-")
        for run in state.recent_runs(limit=5):
            table.add_row(
                f"run {run.run_id}",
                f"{run.status} found={run.scenes_found} ok={run.scenes_ok} failed={run.scenes_failed}",
            )
        console.print(table)

        wm_table = Table(title="watermark")
        wm_table.add_column("provider")
        wm_table.add_column("collection")
        wm_table.add_column("last")
        for rec in state.recent_processed(limit=5):
            wm_table.add_row(rec.provider, rec.collection, rec.scene_id)
        console.print(wm_table)
    except RspipeError as exc:
        _fail(str(exc))


# ── stac ──────────────────────────────────────────────────────────────────────
@app.command("stac")
def cmd_stac(
    config_dir: str = CONFIG_DIR,
    env_file: str = ENV_FILE,
    data_root: str | None = DATA_ROOT,
    action: str = typer.Argument("list", help="list | validate"),
) -> None:
    """STAC 目录操作。"""
    try:
        cfg, _settings = _boot(config_dir, env_file, data_root)
        from rspipe.stac.catalog import CatalogPublisher
        from rspipe.stac.items import validate_item
        from rspipe.storage.layout import DataLayout

        layout = DataLayout(cfg.data_root)
        pub = CatalogPublisher(layout.catalog)
        if action == "list":
            for item in pub.iter_items():
                console.print(f"{item.collection_id}/{item.id}")
        elif action == "validate":
            bad = 0
            for item in pub.iter_items():
                ok = validate_item(item)
                if not ok:
                    bad += 1
                    err_console.print(f"[red]invalid[/red] {item.id}")
            console.print(f"validated {bad} invalid")
            if bad:
                raise typer.Exit(code=1)
        else:
            _fail(f"unknown action: {action}")
    except RspipeError as exc:
        _fail(str(exc))


# ── 工具 ──────────────────────────────────────────────────────────────────────
def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    text = value.replace("Z", "+00:00")
    dt = datetime.fromisoformat(text)
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt


def _print_scenes(scenes: list[Any]) -> None:
    table = Table(title=f"{len(scenes)} scenes")
    table.add_column("scene_id")
    table.add_column("provider")
    table.add_column("datetime")
    table.add_column("cloud%", justify="right")
    table.add_column("tier")
    table.add_column("sensor")
    table.add_column("assets", justify="right")
    for s in scenes:
        table.add_row(
            s.scene_id,
            s.provider,
            s.datetime.strftime("%Y-%m-%d"),
            f"{s.cloud_cover:.2f}" if s.cloud_cover is not None else "-",
            s.tier or "-",
            s.sensor_key or "-",
            str(len(s.assets)),
        )
    console.print(table)


def _print_ranked(ranked: list[Any]) -> None:
    table = Table(title=f"{len(ranked)} scenes (AOI 实测云量)")
    table.add_column("scene_id")
    table.add_column("tier")
    table.add_column("AOI云%", justify="right")
    table.add_column("景云%", justify="right")
    table.add_column("重叠%", justify="right")
    table.add_column("provider")
    for r in ranked:
        s = r.scene
        table.add_row(
            s.scene_id,
            r.tier or "-",
            f"{r.aoi_cloud*100:.2f}" if r.aoi_cloud is not None else "n/a",
            f"{s.cloud_cover:.2f}" if s.cloud_cover is not None else "-",
            f"{r.overlap*100:.1f}",
            s.provider,
        )
    console.print(table)


def _print_report(report: Any) -> None:
    table = Table(title=f"run {report.run_id}  [{report.status}]")
    table.add_column("scene_id")
    table.add_column("status")
    table.add_column("tier")
    table.add_column("AOI云%", justify="right")
    table.add_column("有效%", justify="right")
    table.add_column("elapsed", justify="right")
    for r in report.scenes:
        table.add_row(
            r.scene_id,
            r.status,
            "-",
            f"{r.aoi_cloud_cover*100:.1f}" if r.aoi_cloud_cover is not None else "-",
            f"{r.valid_fraction*100:.1f}" if r.valid_fraction is not None else "-",
            f"{sum(x.elapsed_s for x in r.steps):.1f}s",
        )
    console.print(table)
    console.print(
        f"[green]discovered[/green]={report.discovered} "
        f"[green]processed[/green]={report.processed} "
        f"[yellow]skipped[/yellow]={report.skipped} "
        f"[red]failed[/red]={report.failed}"
    )
    for e in report.errors[:5]:
        err_console.print(f"[red]error[/red] {e}")


if __name__ == "__main__":
    app()
