# data/ 数据目录

本目录存放运行时产物，**不入库**（见仓库根 `.gitignore`）。

| 目录 | 内容 |
|---|---|
| `raw/<provider>/<scene>/` | 下载的原始波段裁剪（uint16 DN）+ `preview_4326.tif` + `aoi.geojson` |
| `derived/cog/` | 处理成果 COG（float32 RGBNIR 反射率，已去云） |
| `staging/<scene>/` | 中间产物（stack / norm / mask），可随时删除 |
| `analysis/<scene>/` | 指数结果（NDVI/NDWI/NDBI/NBR/BSI/NDRE，EPSG:4326 float32） |
| `analysis/<A>__<B>/` | 两期变化结果 |
| `catalog/<collection>/` | STAC item（含 rspi:* 质量字段） |
| `index/` | `catalog.sqlite`（检索索引）、`web_jobs.sqlite`（任务历史） |
| `state/` | 增量水位线、运行记录 |
| `web_export/` | 打包导出缓存（zip） |
| `logs/` | 服务日志 |

首次运行自动创建；`rspipe status` 查看索引与水位线状态。
