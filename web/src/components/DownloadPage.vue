<template>
  <div id="download-page">
    <div id="bar">
      <span class="brand"><i></i> rspipe 影像</span>
      <button class="back" @click="$emit('home')">← 首页</button>
      <span class="sp"></span>
    </div>

    <div id="top">
      <!-- 左栏 -->
      <div id="left">
        <AoiCard :bbox="bbox" :aoi-name="aoiName" :active="aoiTool"
                 :area="aoiArea" :limit="AREA_LIMIT"
                 @set="onToolBtn" @clear="clearAoi" />
        <SearchCard v-model:cfg="sCfg" :loading="searching" :bbox-ready="!!bbox" @search="onSearch" />
            <div class="card" id="logbox">
          <h3>日志</h3>
          <div id="log" ref="logEl">{{ logs.join('\n') }}</div>
        </div>
      </div>

      <!-- 中央地图 -->
      <div id="center">
        <MapCanvas
          ref="mapRef"
          :footprints="footprints"
          :selected-id="activeScene?.scene_id"
          :preview-scene="previewLayer"
          @bbox="onBboxPick"
          @coord="onCoord"
        />
      <div id="tsearch" v-show="aoiTool === 'search'">
        <input v-model="addrQuery" placeholder="输入地名"
               @keydown.enter="addrSearch" />
        <button @click="addrSearch">搜索</button>
      </div>
        <div class="channelbar" v-if="previewLayer">
          <button v-for="c in channelsList" :key="c.v" :class="{ primary: channels === c.v }" @click="setChannels(c.v)">
            {{ c.label }}
          </button>
          <button :class="{ primary: extentVisible }" @click="toggleExtent">范围</button>
          <button @click="clearPreview">关闭</button>
        </div>
      </div>

      <!-- 右栏 -->
      <div id="right">
        <div class="card" style="flex:1; display:flex; flex-direction:column; min-height:0">
          <h3>检索结果</h3>
          <div id="scenes" class="scenelist">
            <div v-if="!groups.length" class="hint">先设置 AOI 并检索。结果按 Tier1 > AOI实测云量 > 覆盖度 排序。</div>
            <div v-for="g in groups" :key="g.provider + g.collection" class="group">
              <div class="group-title">{{ labelOf(g) }} <span class="muted">({{ g.kept }}/{{ g.total }})</span></div>
              <SceneCard
                v-for="s in g.scenes"
                :key="g.provider + s.scene_id"
                :scene="s"
                :active="activeScene?.scene_id === s.scene_id"
                :previewing="previewLayer?.scene_id === s.scene_id"
                :download-ok="rawOkIds.has(s.scene_id) || s.has_raw"
                :job="jobFor(s)"
                @select="selectScene"
                @preview="startPreview"
                @download="startDownload"
              />
            </div>
          </div>
        </div>
        <TasksCard :jobs="jobs" @preview="onTaskPreview" @meta="onTaskMeta" @download="onTaskDownload" @delete="onTaskDelete" />
      </div>
    </div>

    <SceneDrawer v-if="detail" :detail="detail" @close="detail = null" />
    <MessageBox ref="msgBox" />
    <BandPicker
      v-if="bandPick"
      :sid="bandPick.sid"
      :sentinel="bandPick.sentinel"
      :provider="bandPick.provider"
      @confirm="onBandConfirm"
      @cancel="bandPick = null"
    />
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onBeforeUnmount } from 'vue'
import MapCanvas from './MapCanvas.vue'
import AoiCard from './AoiCard.vue'
import SearchCard from './SearchCard.vue'
import SceneCard from './SceneCard.vue'
import SceneDrawer from './SceneDrawer.vue'
import BandPicker from './BandPicker.vue'
import MessageBox from './MessageBox.vue'
import TasksCard from './TasksCard.vue'

const mapRef = ref(null)
const groups = ref([])
const footprints = computed(() => {
  const fs = []
  for (const g of groups.value) for (const s of g.scenes) fs.push(s)
  return fs
})
const searching = ref(false)
const rawOkIds = ref(new Set())   // raw 下载完成的景 id —— 之后才允许"生成预览"
const logs = ref([])
const logEl = ref(null)
function logLn(s) {
  logs.value.push(`[${new Date().toLocaleTimeString()}] ${s}`)
  // 最新日志滚动到底部
  requestAnimationFrame(() => { if (logEl.value) logEl.value.scrollTop = logEl.value.scrollHeight })
}

const bbox = ref(null)
const aoiName = ref('')
const aois = ref([])
const aoiTool = ref(null)       // 'rect' | 'poly' | 'search' | null —— 三按钮互斥单选

const activeScene = ref(null)
const previewLayer = ref(null)
const channels = ref('rgb')
const extentVisible = ref(true)
const channelsList = [
  { v: 'rgb', label: '真彩色' },
  { v: 'false', label: '假彩 NIR' },
  { v: 'ndvi', label: 'NDVI' },
]
const jobs = ref([])
const bandPick = ref(null)
const detail = ref(null)
const addrQuery = ref('')
const sCfg = ref({
  start: new Date(Date.now() - 35 * 864e5).toISOString().slice(0, 10),
  end: new Date().toISOString().slice(0, 10),
  cloud: 10,
  providers: [
    { provider: 'earth-search', collection: 'sentinel-2-l2a' },
    { provider: 'planetary-computer', collection: 'landsat-c2-l2' },
  ],
})
const msgBox = ref(null)
const aoiArea = ref(0)          // AOI 面积 km²
const AREA_LIMIT = 500          // 单次下载面积上限 km²
let toastTimer = null
function showToast(m, kind = 'info') {
  msgBox.value?.toast(m, kind)
}

// 经纬度 → 局部米制平面，Shoelace 求面积（km²）
function ringAreaKm2(ring) {
  if (!ring || ring.length < 3) return 0
  const lat0 = ring.reduce((s, p) => s + p[1], 0) / ring.length
  const kx = 111320 * Math.cos((lat0 * Math.PI) / 180)
  const ky = 110540
  let sum = 0
  for (let i = 0; i < ring.length; i++) {
    const [x1, y1] = ring[i]
    const [x2, y2] = ring[(i + 1) % ring.length]
    sum += (x1 * kx) * (y2 * ky) - (x2 * kx) * (y1 * ky)
  }
  return Math.abs(sum) / 2 / 1e6
}

function bboxRing(b) {
  return [[b[0], b[1]], [b[2], b[1]], [b[2], b[3]], [b[0], b[3]]]
}

function setAoiArea(ring, b) {
  aoiArea.value = ring && ring.length >= 3 ? ringAreaKm2(ring) : ringAreaKm2(bboxRing(b))
}

function areaBlocked() {
  if (aoiArea.value <= AREA_LIMIT) return false
  showToast(`范围 ${aoiArea.value.toFixed(0)} km² 超出上限 ${AREA_LIMIT} km²，请缩小 AOI`, 'error', 3600)
  logLn(`AOI 面积 ${aoiArea.value.toFixed(1)} km² 超限`)
  return true
}

let wsJobWatchers = {}
onMounted(async () => {
  const r = await fetch('/api/aoi')
  aois.value = await r.json()
  // 进度轮询（1s），替代 WS 的简单实现
  setInterval(async () => {
    const r2 = await fetch('/api/jobs')
    jobs.value = await r2.json()
    // raw 已完成集合随任务列表重建（重启/刷新后恢复"✔ 下载完成"状态）
    const okRaw = new Set(jobs.value.filter(j => j.type === 'raw' && j.status === 'ok').map(j => j.scene_id))
    if (okRaw.size) rawOkIds.value = new Set([...rawOkIds.value, ...okRaw])
  }, 1500)
})
onBeforeUnmount(() => {})

function applyFetchedAoi(a) {
  const g = a.geometry
  const ring = g.type === 'MultiPolygon' ? g.coordinates[0][0] : g.coordinates[0]
  const xs = ring.map(p => p[0]); const ys = ring.map(p => p[1])
  bbox.value = [Math.min(...xs), Math.min(...ys), Math.max(...xs), Math.max(...ys)]
  aoiName.value = a.name
  mapRef.value?.fitBbox(bbox.value)
  mapRef.value?.setAoiPolygon(g)
  setAoiArea(ring, bbox.value)
}
function applyAoi(id) {
  const a = aois.value.find(v => v.id === id)
  if (a) { applyFetchedAoi(a); logLn(`AOI 预设: ${a.name}`) }
}
function onToolBtn(m) {
  // 三选一：同键再点=取消；search 只控显隐，rect/poly 驱动地图圈选（互斥）
  aoiTool.value = (aoiTool.value === m) ? null : m
  mapRef.value?.setDrawMode(m === 'search' ? null : m)
  const tips = {
    rect: '按住左键拖出矩形',
    poly: '单击加点，双击/回车收尾，右键撤销',
    search: '输入地名回车，边界落为 AOI',
  }
  if (tips[m] && aoiTool.value) { aoiName.value = ''; logLn(tips[m]) }
}
function clearAoi() {
  bbox.value = null; aoiName.value = ''
  aoiTool.value = null
  mapRef.value?.setDrawMode(null)
  mapRef.value?.setAoiPolygon(null)
  logLn('已清除 AOI')
}
async function onTaskPreview(j) {
  // 任务卡无 footprint：补一次详情拿 bbox 再缩放
  let s = { scene_id: j.scene_id, provider: j.provider, collection: j.collection }
  try {
    const r = await fetch(`/api/scenes/${j.provider}/${encodeURIComponent(j.scene_id)}`)
    if (r.ok) {
      const d = await r.json()
      s = { ...s, bbox: d.bbox, footprint: d.footprint, raw_bounds: d.raw_bounds }
    }
  } catch (e) { /* 忽略，退回 AOI */ }
  startPreview(s)
}
async function onTaskMeta(j) {
  await selectScene({ scene_id: j.scene_id, provider: j.provider, collection: j.collection })
}
async function onTaskDownload(j) {
  showToast('正在打包…')
  logLn(`打包 ${j.scene_id} …`)
  try {
    const r = await fetch(`/api/export/${j.scene_id}`)
    if (!r.ok) {
      const t = await r.text()
      showToast('打包失败: ' + t.slice(0, 80))
      logLn(`打包失败 ${r.status}`)
      return
    }
    const blob = await r.blob()
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `${j.scene_id}.zip`
    document.body.appendChild(a)
    a.click()
    a.remove()
    setTimeout(() => URL.revokeObjectURL(url), 60000)
    logLn(`打包完成 ${(blob.size / 1048576).toFixed(1)} MB，已开始下载`)
    showToast('已开始下载')
  } catch (e) {
    showToast('打包失败: ' + (e.message || e))
  }
}
async function onTaskDelete(j) {
  const yes = await msgBox.value?.confirm({
    title: '删除确认',
    message: `将删除 ${j.scene_id} 的全部本地产物（raw / staging / COG / STAC / 导出缓存），不可恢复。`,
    okText: '删除',
  })
  if (!yes) return
  try {
    const r = await fetch(`/api/jobs/${j.id}`, { method: 'DELETE' })
    if (r.ok) {
      jobs.value = jobs.value.filter(x => x.id !== j.id)
      rawOkIds.value = new Set([...rawOkIds.value].filter(v => v !== j.scene_id))
      if (previewLayer?.value?.scene_id === j.scene_id) clearPreview()
      logLn(`已删除 ${j.scene_id}`)
    } else {
      const detail = await r.text()
      showToast('删除失败: ' + detail.slice(0, 100))
    }
  } catch (e) {
    showToast('删除失败: ' + (e.message || e))
  }
}
function onBboxPick(b, ring) {
  setAoiArea(ring, b)
  bbox.value = b
  logLn(`AOI=${b.map(v => v.toFixed(3)).join(', ')}`)
}
async function onSearch(cfg) {
  if (!ensureBbox()) return
  if (areaBlocked()) return
  searching.value = true
  groups.value = []
  logLn(`检索 ${cfg.start.slice(0,10)}~${cfg.end.slice(0,10)} 云≤${cfg.cloud}%`)
  try {
    const body = { ...cfg, bbox: bbox.value }
    const r = await fetch('/api/search', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    })
    if (!r.ok) {
      const txt = await r.text()
      logLn(`检索 ${r.status}`)
      showToast('检索失败 ' + r.status + (txt.includes('detail') ? '' : ''))
      return
    }
    const data = await r.json()
    groups.value = data.groups || []
    for (const g of groups.value) logLn(`${g.collection} ${g.kept}/${g.total}`)
  } catch (e) {
    logLn(`检索失败: ${e}`)
    showToast('检索失败: ' + e)
  } finally {
    searching.value = false
  }
}
async function addrSearch() {
  const q = (addrQuery.value || '').trim()
  if (!q) return
  logLn(`搜索 ${q} …`)
  try {
    const r = await fetch('/api/geocode', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ q }),
    })
    const j = await r.json()
    if (!j.geometry || !j.geometry.type) { logLn('未找到边界'); showToast('未找到「' + q + '」的边界'); return }
    if (!['Polygon', 'MultiPolygon'].includes(j.geometry.type)) {
      logLn('非面要素，后端自动 100 米缓冲')
    }
    logLn(`边界 ${j.display}`)
    applyFetchedAoi({ id: 'addr', name: j.display, geometry: j.geometry })
  } catch (e) {
    logLn('搜索失败: ' + (e.message || e))
    showToast('搜索失败: ' + (e.message || e))
  }
}
async function selectScene(s) {
  activeScene.value = s
  detail.value = null
  try {
    const r = await fetch(`/api/scenes/${s.provider}/${encodeURIComponent(s.scene_id)}`)
    detail.value = await r.json()
  } catch { detail.value = s }
}
function ensureBbox() {
  if (bbox.value) return true
  const vp = mapRef.value?.viewportBbox?.()
  if (!vp) { showToast('先设置 AOI'); return false }
  bbox.value = vp
  logLn('未设 AOI → 按地图当前视野取景')
  return true
}
function geomBbox(g) {
  if (!g || !g.coordinates) return null
  const rings = g.type === 'MultiPolygon' ? g.coordinates.flat(1) : g.coordinates
  let minx = Infinity, miny = Infinity, maxx = -Infinity, maxy = -Infinity
  for (const ring of rings) {
    for (const p of ring) {
      if (p[0] < minx) minx = p[0]
      if (p[0] > maxx) maxx = p[0]
      if (p[1] < miny) miny = p[1]
      if (p[1] > maxy) maxy = p[1]
    }
  }
  return Number.isFinite(minx) ? [minx, miny, maxx, maxy] : null
}
function startPreview(s) {
  // 缩放优先级：raw 裁剪实际范围 > AOI > 景范围
  const provider = s.provider
  const collection = s.collection || ''
  const sceneBounds = s.bbox || geomBbox(s.footprint) || null
  // 数据实际范围（瓦片只覆盖这块）——source.bounds 必须用它，否则 MapLibre 会把瓦片拉伸错位
  const dataBounds = s.raw_bounds || s.cog_bounds || bbox.value || sceneBounds
  previewLayer.value = { ...s, provider, collection, bounds: dataBounds, ready: true }
  activeScene.value = s
  channels.value = 'rgb'
  if (dataBounds) mapRef.value?.fitBbox(dataBounds)
  logLn(`预览 ${s.scene_id} · 缩放到${s.raw_bounds || s.cog_bounds ? '裁剪范围' : bbox.value ? ' AOI' : '景范围'}`)
}
function startDownload(s) {
  if (!ensureBbox()) return
  if (areaBlocked()) return
  // 先弹波段选择窗，确认后才开始下载
  bandPick.value = { sid: s.scene_id, provider: s.provider, sentinel: s.collection.includes('sentinel'), scene: s }
}
function onBandConfirm(bands) {
  const { scene } = bandPick.value
  bandPick.value = null
  logLn(`raw ${scene.scene_id} ${bands.length} 波段`)
  startJob(scene, 'raw', null, bands)
}
function jobFor(s) {
  return jobs.value.find(j => j.scene_id === s.scene_id && j.type !== 'processed' && j.status === 'running')
    || jobs.value.filter(j => j.scene_id === s.scene_id).sort((a, b) => (b.id || 0) < (a.id || 0) ? -1 : 1)[0] || null
}
async function startJob(s, type, onOk, bands) {
  const r = await fetch('/api/jobs', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ type, provider: s.provider, collection: s.collection, scene_id: s.scene_id, aoi_bbox: bbox.value, bands: bands && bands.length ? bands : undefined }),
  })
  if (!r.ok) {
    const txt = await r.text()
    logLn(任务提交失败 )
    showToast('提交失败: ' + txt.slice(0, 80))
    return
  }
  const watcher = setInterval(() => {
    const mine = jobs.value.find(v => v.scene_id === s.scene_id && v.type === type)
    if (mine?.status === 'ok') {
      clearInterval(watcher)
      if (type === 'raw') {
        // raw 完成 → 该景升级为"可预览"，按钮自动变为"生成预览"
        rawOkIds.value = new Set([...rawOkIds.value, s.scene_id])
      }
      onOk?.()
    }
    if (mine?.status === 'error') {
      clearInterval(watcher)
      logLn(`失败 ${s.scene_id}`)
      showToast(mine.message)
    }
  }, 1200)
}
function toggleExtent() {
  extentVisible.value = !extentVisible.value
  mapRef.value?.setExtentVisible(extentVisible.value)
}
function setChannels(v) {
  channels.value = v
  const p = previewLayer.value
  mapRef.value?.setPreviewTiles(p.scene_id, v, p.provider, p.collection, p.bounds)
}
function clearPreview() { previewLayer.value = null; mapRef.value?.clearPreview() }
const labelOf = g => (g.collection.includes('landsat') ? 'Landsat 8/9' : 'Sentinel-2') + ' · ' + g.provider

// 底部坐标条：监听鼠标事件由 MapCanvas 转发
window.addEventListener('mousemove', e => {})  // 实际由 MapCanvas 回调

defineEmits(['home'])
</script>

<style scoped>
#download-page { display: flex; flex-direction: column; height: 100vh; }
#bar {
  flex: none; display: flex; align-items: center; gap: 14px;
  padding: 9px 16px; background: var(--panel); border-bottom: 1px solid var(--line);
}
#bar .brand { font-size: 14px; font-weight: 700; letter-spacing: .5px; display: flex; align-items: center; gap: 9px; }
#bar .brand i { width: 20px; height: 20px; border-radius: 6px; flex: none; background: linear-gradient(135deg, var(--accent), #22d3ee); display: inline-block; }
#bar .tag { color: var(--muted); font-size: 11.5px; }
#bar .sp { flex: 1; }
#top { flex: 1; min-height: 0; display: flex; gap: 12px; padding: 12px; }
#left { width: 276px; flex: none; display: flex; flex-direction: column; gap: 12px; overflow: auto; }
#center {
  flex: 1; position: relative; min-width: 0; background: var(--panel);
  border: 1px solid var(--line); border-radius: 12px; overflow: hidden; box-shadow: var(--shadow);
}
#right { width: 330px; flex: none; display: flex; flex-direction: column; gap: 12px; overflow: hidden; }
.scenelist { flex: 1; min-height: 0; overflow: auto; }
.group { margin-bottom: 12px; }
.group-title { font-size: 12px; font-weight: 600; margin: 8px 0 6px; color: var(--accent); }
#log {
  flex: 1; min-height: 140px; max-height: 190px; overflow: auto;
  background: #12161d; color: #c8d3e0;
  font: 11px/1.55 Consolas, "Microsoft YaHei UI", monospace;
  padding: 8px 10px; border-radius: 10px; white-space: pre-wrap;
}
#logbox { flex: none; height: 210px; display: flex; flex-direction: column; }
#coords { position: absolute; z-index: 30; left: 50%; bottom: 12px; transform: translateX(-50%); }
#msg {
  position: absolute; z-index: 40; left: 50%; top: 50%; transform: translate(-50%, -50%);
  background: rgba(18,22,29,.9); color: #fff; border-radius: 10px;
  padding: 14px 22px; font-size: 13px; max-width: 70%; text-align: center;
  box-shadow: 0 12px 40px rgba(0,0,0,.25);
}
#tsearch {
  position: absolute; z-index: 31; top: 14px; left: 50%; transform: translateX(-50%);
  display: flex; gap: 6px; background: rgba(255,255,255,.96);
  border: 1px solid var(--line); border-radius: 10px; padding: 6px 8px;
  box-shadow: 0 4px 18px rgba(20,30,45,.14); backdrop-filter: blur(6px);
}
#tsearch input { border: 1px solid var(--line); border-radius: 7px; font: inherit; width: 240px; padding: 3px 10px; }
.channelbar {
  position: absolute; z-index: 31; top: 12px; left: 12px;
  display: flex; flex-direction: column; gap: 4px; background: rgba(255,255,255,.96);
  border: 1px solid var(--line); border-radius: 10px; padding: 5px 7px;
  box-shadow: 0 4px 18px rgba(20,30,45,.14);
}
.channelbar button { min-height: 26px; padding: 2px 10px; font-size: 11.5px; white-space: nowrap; }
.back { flex: none; }
</style>
