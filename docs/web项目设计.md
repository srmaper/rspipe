# 遥感影像 Web 平台设计（检索 + 下载 + 预览）

> **实现状态（2026-10-30 更新）**：本文为最初设计稿，实际实现与设计有以下差异——
>
> - 前端未使用 Element Plus，采用自研浅色样式（`web/src/style.css`）
> - 页面为 hash 路由三页：`#/` 首页、`#/download` 下载、`#/analysis` 分析
> - 预览不走 TiTiler，自研瓦片端点（COG → 4326 预览栅格 → raw → 204）
> - 新增：下载后处理（4326 预览栅格 + AOI 记录）、指数分析（NDVI/NDWI/NDBI/NBR/BSI/NDRE）、两期变化、双屏对比
> - 任务中心 sqlite 持久化，重启可续；服务脚本崩溃自动拉起


> 前端 Vue 3 + MapLibre GL JS · 后端 FastAPI 复用 rspipe · 数据源 STAC 优先、地理空间数据云做适配器  
> 版本 v0.1 设计稿 · 2026-10-27

---

## 1. 总体架构

```
┌─────────────────────────── 浏览器 ───────────────────────────┐
│  Vue 3 (Vite) + Pinia + Element Plus                         │
│  MapLibre GL —— 底图/瓦片/AOI 绘制/结果足迹/影像瓦片叠加       │
└──────────────┬───────────────────────────────────────────────┘
               │ REST + WebSocket(任务进度)
┌──────────────┴─────────────── FastAPI (薄壳) ─────────────────┐
│  /api/search  /api/scenes  /api/preview  /api/jobs  /api/tiles│
│  直接调用 rspipe：clients.stac / quality.rank_scenes /         │
│  ingest.cogreader / normalize.cog / process.* / storage.*      │
└──────────────┬───────────────────────────────────────────────┘
               │                    ┌─────────────────────┐
               │ Range 读云端 COG   │ Jobs 线程池 + SQLite │
               ▼                    └─────────────────────┘
  earth-search (S3) · planetary-computer (Azure)
  [适配器预留] 地理空间数据云 gscloud.cn
```

**核心原则**：不重造轮子。检索/选景/定标/去云/COG 全部走现成 `rspipe` 库；  
Web 层只做：搜索代理、AOI 解析、瓦片代理、任务队列、进度推送。

---

## 2. 数据源策略

| 数据源                    | 接入方式         | 说明                                                                                                    |
| ---------------------- | ------------ | ----------------------------------------------------------------------------------------------------- |
| **earth-search**       | STAC（已通）     | S2 全历史，AWS 免登录                                                                                        |
| **planetary-computer** | STAC+SAS（已通） | Landsat 8/9、S2、Dem 等                                                                                  |
| 地理空间数据云                | 适配器（二期）      | `gscloud.cn` 无官方开放 API：一期用官网接口逆向/或人工导出 CSV 解析；优先级低于 STAC——先把 STAC 两条线跑顺，GSC 作为查询 Landsat 7/5/4 等"补充源" |
| cdse-s2                | 适配器（已通，需凭据）  | OAuth2                                                                                                |

理由：地理空间数据云的核心价值是 Landsat 系列老存档（L4/L5/L7），  
但 usgs-m2m 更权威；GSC 逆向接口易失效，仅列为可选适配器，不阻塞一期。

---

## 3. 页面与交互设计（3 页 + 1 抽屉）

### 3.1 主界面布局（单页）

```
┌─────────────────────────────────────────────────────────────┐
│ 顶栏: logo | 数据源开关[S2✔][L8/9✔] | 数据源:STAC▾ | 任务🔔3 │
├──────────┬──────────────────────────────────┬───────────────┤
│ 左侧面板  │        MapLibre 地图             │ 右侧结果面板   │
│ ·检索条件  │  · AOI 绘制(矩形/多边形)          │ · 景卡片列表    │
│  时间范围  │  · 候选景足迹(半透明黄)            │  (缩略图/云量/  │
│  云量滑杆  │  · 选中景足迹(亮绿+coverage)       │  时间/传感器)   │
│  传感器   │  · 影像瓦片叠加层(选中 rash)       │ · 排序/筛选     │
│  [检索]   │  · 底图: OSM/Satellite 切换        │ · 对比 checkbox │
│ ·AOI列表  │  · 比例尺/经纬度                   │ · [下载][预览]  │
│  历史缓存  │                                  │                │
└──────────┴──────────────────────────────────┴───────────────┘
                          │ 点击景卡片
                          ▼
              右侧抽屉: 影像详情
              · 大图预览 (瓦片渲染, 通道选择 RGB/假彩/NDVI)
              · 元数据表 (rspi:* + eo:cloud_cover + footprint)
              · [下载 raw DN] [下载 processed COG] [加入批量]
```

### 3.2 关键交互流

1. **画 AOI**：MapLibre `gl-draw`（矩形/多边形）→ GeoJSON → 存入 Pinia
2. **检索**：POST `/api/search` → 后端走 `clients.stac.search()` + `rank_scenes()`  
   （AOI 实测云量在服务端计算，Tier1 优先）→ 返回榜单（含 aoi_cloud/valid 建议列）
3. **预览**：选中景 → 前端发 `/api/preview/{scene}` → 后端 `SceneProcessor` 走  
   "本地 raw 快速通道"流程生成 COG → 返回 `/tiles/{scene}/{z}/{x}/{y}` 矢量-free 光栅瓦片  
   → MapLibre `raster` source 叠加，可选渲染通道（RGB / 假彩 NIR / NDVI 调色）
4. **下载**：POST `/api/jobs` 创建任务（raw 全波段或 processed）；  
   WebSocket 推送每个波段进度百分比（rip raw 下载器本来就是并行+续传）

### 3.3 结果排序默认

`rank_scenes` 打分列直接透出：Tier > AOI云%(实测) > 覆盖度%。  
每个景卡片三元数据条：`T1 | AOI云 0.8% | 覆盖 100%`。

---

## 4. 后端 API

| Method | Path                                     | 说明 → rspipe 复用点                                                                               |
| ------ | ---------------------------------------- | --------------------------------------------------------------------------------------------- |
| POST   | `/api/search`                            | body: bbox/geometry, date, cloud, collections → `clients.stac.search` + `quality.rank_scenes` |
| GET    | `/api/scenes/{id}`                       | STAC item 详情 → `clients.stac.item_to_scene`                                                   |
| GET    | `/api/preview/{scene_id}?collection=...` | 同步生成（或查缓存）processed COG → `pipeline.steps.SceneProcessor`（fast 路径）                            |
| GET    | `/api/tiles/{scene_id}/{z}/{x}/{y}`      | COG → PNG 瓦片，通道参数 `bands=r,g,b` / `ndvi` → `ingest.cogreader` + `process.color.apply_stretch` |
| POST   | `/api/jobs`                              | body: {scene_id, product: raw|processed, bands?} → 后台线程池                                      |
| GET    | `/api/jobs/{job_id}` + WS `/ws/jobs`     | 进度/状态（复用 `storage.layout` 与 sqlite）                                                           |
| GET    | `/api/aoi` / POST `/api/aoi`             | AOI 列表（读 `configs/aoi_*.geojson`）                                                             |

**瓦片协议**：不动 MapServer/GeoServer，直接用 `rio-tiler`(或 COGReader)  
返回 256×256 PNG：简单、零部署依赖、缓存走 HTTP `Cache-Control`。

---

## 5. 前端技术栈与目录

```
web/
├── package.json          # vue3 + vite + pinia + element-plus + @maplibre/maplibre-gl-js
└── src/
    ├── api/              # axios 封装 + WS 客户端
    ├── stores/           # aoi.ts scenes.ts jobs.ts map.ts (pinia)
    ├── views/
    │   ├── Main.vue       # 三栏布局容器
    │   └── SceneDrawer.vue# 详情抽屉
    ├── components/
    │   ├── SearchPanel.vue    # 检索条件表单
    │   ├── SceneCard.vue      # 结果卡片
    │   ├── JobList.vue        # 任务/进度
    │   ├── MapCanvas.vue      # MapLibre 初始化、AOI 绘制、足迹/瓦片图层管理
    │   └── BandSelector.vue   # 预览通道切换 (RGB/假彩/NDVI)
    └── utils/geo.ts      # bbox/墨卡托换算
```

**MapLibre 关键点**：

- 底图：`raster` style（OSM 瓦片 + 天地图授权可切换）
- AOI 绘制：`maplibre-gl-draw`（或轻量手绘 bbox 插件）
- 云端 COG 瓦片：`raster` source, `tiles: ['/api/tiles/{id}/{z}/{x}/{y}.png']`
- 足迹：`fill`+`line` layer, `set-data` 增量更新

---

## 6. 分期路线

| 阶段         | 内容                                             | 里程碑验收                                     |
| ---------- | ---------------------------------------------- | ----------------------------------------- |
| **M1 MVP** | STAC 双源检索、AOI 画框、结果榜单、单景瓦片预览(RGB)、processed 下载 | 输入"西安最近一月云≤5%"+ 画框 → 看到榜 → 点开看影像 → 下载 COG |
| **M2**     | 任务队列+WS 进度、raw 全波段下载、AOI 命名/保存、多景对比视图          | 批量下载深圳+太原成功、进度条可见                         |
| **M3**     | NDVI/假彩渲染、时序/镶嵌浏览、地理空间数据云适配器、导出报告              | 定制通道出图、镶嵌深锯结果                             |

---

## 7. 风险与对策

- **跨域**：开发期 Vite proxy `/api`，生产 Nginx `proxy_pass`
- **瓦片性能**：COG 窗口读本就快；瓦片内存 LRU + `Cache-Control: max-age=7d`  
  （每景 COG 生成一次后基本不变）
- **预览生成速度**：利用"本地 raw 快速通道"，首景首访 ~1.5 分钟（S2），后续命中缓存秒开
- **登录墙**：地理空间数据云逆向接口随时可能变；接口设计预留 `provider` 字段切换
- **浏览器缩放层级**：COG 缓存了 overview，`/tiles` 会自动选金字塔级，无卡顿
