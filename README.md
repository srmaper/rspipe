# rspipe — 遥感影像处理与预览平台

Landsat / Sentinel-2 的**接入 → 归一化 → COG → 去云 → 匀色 → 镶嵌 → STAC → 检索预览下载**全链路，
含一个 Vue + MapLibre 的 Web 平台。

| 能力 | 模块 |
|---|---|
| 数据接入、归一化、COG 转换 | `clients` `ingest` `normalize` |
| 去云、匀色、无缝镶嵌、STAC | `process` `stac` |
| 定时增量更新 | `pipeline`（水位线 + cron） |
| 选景质量评估（AOI 实测云量 / Tier） | `quality.py` |
| 检索 / 预览 / 下载 Web 平台 | `web/` + `web/`（前端） |

---

## 界面预览

| 首页 | 下载影像 |
|---|---|
| ![首页](docs/screenshots/01-home.png) | ![下载](docs/screenshots/02-download.png) |

| 数据分析 | 使用指南 |
|---|---|
| ![分析](docs/screenshots/03-analysis.png) | ![指南](docs/screenshots/04-guide.png) |

---

## 快速开始

### 1. Web 平台（推荐）

```powershell
scripts\web_server.cmd   # 杀旧实例并启动，监听 8000（崩溃自动拉起）
```

浏览器打开 **http://127.0.0.1:8000**（用 `127.0.0.1` 而不是 `localhost`，见"已知问题"）。

页面流程：**圈矩形 / 画多边形 / 地名搜索** 设 AOI → **检索** → 卡片选波段 **下载** →
任务卡 **预览 / 元数据 / 下载 zip / 删除**。

页面路由：

| 路径 | 页面 |
|---|---|
| `#/` | 首页（下载影像 / 数据分析 两个入口） |
| `#/download` | 检索 · AOI 圈选 · 选波段下载 · 真彩/假彩/NDVI 预览 · 导出 zip |
| `#/analysis` | 指数分析（NDVI/NDWI/NDBI/NBR/BSI/NDRE）、两期变化、双屏对比 |

> 仓库不含 `data/`（下载与计算产物，运行时生成，见 `data/README.md`）。

### 2. CLI

```bash
rspipe search --cloud 20 --limit 10          # 检索（AOI 实测云量排序）
rspipe run --full                            # 单次增量：发现→处理→发布
rspipe mosaic --date 20261023                # 匀色 + 羽化镶嵌
rspipe status                                # 水位线 / 已处理景
rspipe schedule                              # 常驻定时更新
```

### 3. 城市批量下载脚本

```powershell
python scripts/download_taiyuan.py raw|processed|all
```

脚本内 `TARGETS`（provider/collection/scene_id）+ `AOI` 常量换成目标城市即可复用。

### 4. Python API

```python
from rspipe.config import load_pipeline_config, Settings
from rspipe.pipeline import Pipeline

cfg = load_pipeline_config("configs")
pipe = Pipeline.from_config(cfg, Settings())
scenes = pipe.discover(force_full=True)
result = pipe.process_scene(scenes[0])
```

---

## 安装

```bash
pip install -e ".[dev]"          # 开发（pytest / ruff / mypy）
pip install -e ".[m2m]"          # USGS M2M 客户端
pip install -e ".[s2cloudless]"  # ML 去云

cd web && npm install && npm run build   # 前端（构建产物由 FastAPI 托管）
```

Python ≥ 3.11；Windows 上 rasterio wheel 自带 GDAL。

## 凭证

复制 `.env.example` 为 `.env`：

| 数据源 | 检索 | 资产 | 需要 |
|---|---|---|---|
| `earth-search` | 公开 | 公开 | 无 |
| `planetary-computer` | 公开 | 公开（自动 SAS 签名） | 无 |
| `usgs-landsat` | 公开 | 需 EROS 账号 | `USGS_M2M_USERNAME` / `USGS_M2M_TOKEN` |
| `cdse-s2` | OAuth2 | OAuth2 | `CDSE_CLIENT_ID` / `CDSE_CLIENT_SECRET` |

缺凭证的数据源自动跳过并告警。

---

## Web API

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/search` | bbox + 时间 + 云量 → 按 Tier/AOI 实测云量/覆盖度排序 |
| GET | `/api/scenes/{provider}/{id}` | 场景详情（含 `local_bands`、`raw_bounds`） |
| POST | `/api/jobs` | 建任务：`raw`（可选 `bands` 白名单）/ `processed` |
| GET | `/api/jobs` | 任务列表（sqlite 持久化，重启不丢） |
| DELETE | `/api/jobs/{id}` | 删除任务并清理该景本地产物 |
| GET | `/api/tiles/{id}/{z}/{x}/{y}.png` | 瓦片（本地 COG → 本地 raw → 204） |
| GET | `/api/export/{id}` | 打包 zip（COG + composite.tif + raw + quicklook + README + STAC json） |
| POST | `/api/geocode` | 地名 → 边界（geoscope 主源，Nominatim 兜底，点/线自动 100m 缓冲） |
| GET | `/api/analysis/scenes` | 已下载影像清单（含可用波段、范围） |
| POST | `/api/analysis/run` | 计算单景指数（ndvi/ndwi/ndbi/nbr/bsi/ndre）+ 阈值分类统计 |
| POST | `/api/analysis/change` | 两期指数差值 + 变化面积（减少/增加） |
| GET | `/api/analysis/tiles/{key}/{name}/{z}/{x}/{y}.png` | 指数/变化结果伪彩瓦片 |
| GET | `/api/aoi` | `configs/aoi_*.geojson` 预设列表 |

**瓦片渲染**：本地 processed COG 优先，其次本地 raw 波段；都没有返回 204（不回落云端）。
按"瓦片 ∩ 影像范围"求交贴到 256 画布，无值区透明；每通道 2%~98% 拉伸。

---

## 处理链

由 `ProductProfile` 推导，COG 转换与辐射定标是条件步骤：

| 源形态 | 步骤链 |
|---|---|
| Earth Search / PC COG（L2 SR） | `stack → harmonize → cog_convert → cloud_mask → stac_publish` |
| CDSE `.SAFE`（S2 L2A） | `unpack → stack → harmonize → reproject → cog_convert → cloud_mask → stac_publish` |
| USGS `.tar`（L1 DN） | `unpack → stack → radiometric_normalize → reproject → cog_convert → cloud_mask → stac_publish` |

定标：S2 `ρ = DN/10000`；Landsat `ρ = DN×2.75e-05 − 0.2`。

---

## 配置

```
configs/
  pipeline.yaml   AOI、时间窗、云量阈值、波段、cron、水位线回溯
  providers.yaml  数据源端点、认证、抓取模式（vsi / download / windowed）
  sensors.yaml    波段映射、QA/SCL 位定义、兜底定标
  aoi_*.geojson   城市 AOI
```

## 数据布局

```
data/
  raw/{provider}/{scene_id}/    原始波段裁剪（uint16 DN）
  staging/{scene_id}/           中间产物（stack / norm / mask）
  derived/cog/                  成品 COG（float32 RGBNIR 反射率，已去云）
  catalog/{collection}/         STAC item（含 rspi:* 质量字段）
  index/                        catalog.sqlite + web_jobs.sqlite
  web_export/                   zip 导出缓存
```

---

## 验证

```bash
ruff check src tests scripts
pytest tests -q            # 90 项，无需网络
```

覆盖：云掩膜位解码与缓冲、直方图匹配、羽化镶嵌、辐射定标、COG 合规、STAC 生成、
SQLite 空间检索、水位线增量、CogReader 窗口/金字塔读取、瓦片渲染、选景排序。

---

## 项目结构

```
src/rspipe/
  auth.py         OAuth2 / M2M token
  bands.py        波段名解析（通用名 ↔ 产品名 ↔ 资产 key）
  config.py       YAML + .env + pydantic
  models.py       SceneMeta / AssetRef / ProductProfile / StepResult
  quality.py      Tier 解析、AOI 实测云量（缓存+并行）、选景排序
  cli.py          typer 命令行
  clients/        stac / usgs_m2m / cdse_odata / factory
  ingest/         fetcher / downloader / unpack / rangefile / cogreader / signing / httpproxy
  normalize/      reflectance / harmonize / reproject / cog / profile / io
  process/        clouds / color / mosaic
  stac/           items / catalog / store
  pipeline/       steps / runner / state / scheduler
  storage/        layout
  web/            app / schemas / jobs / download / preview / tiles / export / cleanup
web/              Vue3 + Vite + MapLibre 前端（dist 由 FastAPI 托管）
scripts/          城市下载脚本 + web_server.cmd
docs/             设计与问题记录
```

## 文档

- `docs/产品与数据源说明.md` — 波段含义、DN→彩色原理、产品规格
- `docs/web项目设计.md` — Web 平台架构与分期
- `docs/应用能力介绍.md` — 能做哪些应用（城市/农业/林业/水环境/灾害/执法等）、方法、产出与能力边界
- `docs/开发问题与经验.md` — 踩坑清单与规避方法
