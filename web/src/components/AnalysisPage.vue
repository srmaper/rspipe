<template>
  <div class="analysis">
    <div id="bar">
      <span class="brand"><i></i> 数据分析</span>
      <button class="back" @click="$emit('home')">← 首页</button>
      <button class="back" @click="goDownload">转下载页</button>
      <span class="sp"></span>
      <span class="muted">{{ sceneA ? sceneA : '' }}</span>
    </div>

    <div id="top">
      <!-- 左栏 -->
      <div id="left">
        <button class="primary guide-btn" id="tour-guide" @click="tourOpen = true">操作指引</button>
        <button class="primary guide-btn" id="tour-guide-modal" @click="guideOpen = true">分析指南</button>

        <div class="card" id="tour-setup">
          <h3>分析设置</h3>

          <label>模式</label>
          <div class="seg">
            <button :class="{ primary: mode === 'single' }" @click="mode = 'single'">单景指数</button>
            <button :class="{ primary: mode === 'change' }" @click="mode = 'change'">两期变化</button>
          </div>

          <label>影像{{ mode === 'change' ? ' A（前期）' : '' }}</label>
          <select v-model="sceneA">
            <option value="">选择已下载影像…</option>
            <option v-for="s in scenes" :key="s.scene_id" :value="s.scene_id">{{ s.scene_id }}</option>
          </select>

          <template v-if="mode === 'change'">
            <label>影像 B（后期）</label>
            <select v-model="sceneB">
              <option value="">选择…</option>
              <option v-for="s in scenes" :key="s.scene_id" :value="s.scene_id">{{ s.scene_id }}</option>
            </select>
          </template>

          <label>指数（按类别分组）</label>
          <select v-model="index">
            <option value="" disabled>选择指数…</option>
            <optgroup v-for="g in indexGroups" :key="g.label" :label="g.label">
              <option v-for="i in g.items" :key="i.name" :value="i.name">
                {{ i.name.toUpperCase() }} · {{ i.desc }}
              </option>
            </optgroup>
          </select>
          <div v-if="missingBands.length" class="warn">
            缺少波段：{{ missingBands.join(', ') }}
          </div>

          <label>
            {{ mode === 'change' ? '变化阈值' : '分类阈值' }}：{{ threshold }}
            <span class="muted" v-if="curMeta.label">（{{ curMeta.label }}）</span>
          </label>
          <input
            type="range"
            :min="sliderRange[0]"
            :max="sliderRange[1]"
            :step="sliderRange[2]"
            v-model.number="threshold"
          />

          <div class="row">
            <button class="primary" id="tour-run" :disabled="!canRun || running" @click="run">
              {{ running ? '计算中…' : '开始计算' }}
            </button>
          </div>
          <div v-if="error" class="warn">{{ error }}</div>
        </div>

        <div class="card">
          <h3>图例</h3>
          <span class="lbar" :style="{ background: legendCss }"></span>
          <div class="lv"><span>{{ legendLow }}</span><span>{{ legendHigh }}</span></div>
          <div class="hint" style="white-space: pre-line">{{ legendNote }}</div>
        </div>

      </div>

      <!-- 中央地图 -->
      <div id="center" data-tour="map">
        <MapCanvas ref="mapRef" :preview-scene="preview" :tile-template="tileTemplate" />
        <div class="layerbar" v-if="preview">
          <button :class="{ primary: sourceOn }" :disabled="!hasSource" @click="toggleSource">原始影像</button>
          <button :class="{ primary: resultVisible }" @click="toggleResult">结果影像</button>
          <button v-if="mode === 'change'" :disabled="comparing" @click="openSwipe">
            {{ comparing ? "准备中…" : "双屏比对" }}
          </button>
          <button :class="{ primary: vectorOn }" :disabled="vectorBusy" @click="toggleVector">
            {{ vectorBusy ? "生成中…" : "阈值矢量" }}
          </button>
          <button :class="{ primary: aoiOn }" :disabled="!hasAoi" @click="toggleAoi">AOI</button>
          <button @click="closeLayers">关闭</button>
        </div>
        <div class="badge" v-if="preview">{{ preview.scene_id }}</div>
      </div>

      <!-- 右栏 -->
      <div id="right">
        <div class="card" id="tour-result" style="flex:4 1 0;min-height:0;display:flex;flex-direction:column">
          <h3>分析结果</h3>
          <div class="reslist">
            <div v-if="!stats" class="hint">选择影像与指数后点击「开始计算」。</div>
            <table v-if="stats" class="kv">
              <tr v-for="(v, k) in statRows" :key="k"><th>{{ k }}</th><td>{{ v }}</td></tr>
            </table>
          </div>
        </div>

        <div class="card" id="tour-history" style="flex:6 1 0;min-height:0;display:flex;flex-direction:column">
          <h3>历史分析</h3>
          <div class="reslist">
            <div v-if="!history.length" class="hint">还没有分析记录，算一次就会出现在这里。</div>
            <div
              v-for="h in history"
              :key="h.key + h.index"
              class="hist"
              :class="{ active: preview && preview.scene_id === h.key }"
            >
              <div class="hrow" @click="loadHistory(h)">
                <span class="grow hname">
                  {{ h.mode === 'change' ? h.scene_a + ' ↔ ' + h.scene_b : h.scene_a }}
                </span>
                <span class="badge">{{ h.index.toUpperCase() }}</span>
              </div>
              <div class="hint">{{ histLine(h) }} · {{ fmtTime(h.created) }}</div>
              <div class="row" style="margin-top:6px">
                <button @click="loadHistory(h)">加载</button>
                <button @click="downloadResult(h.key)">下载</button>
                <button class="danger" @click="deleteHistory(h)">删除</button>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>

    <MessageBox ref="msgBox" />
    <GuideModal v-if="guideOpen" @close="guideOpen = false" />
    <TourGuide v-if="tourOpen" :steps="TOUR_STEPS" @close="tourOpen = false" />
    <SwipeCompare
      v-if="swipe"
      :tile-a="swipe.tileA"
      :tile-b="swipe.tileB"
      :bounds="swipe.bounds"
      :label-a="'A · ' + shortDate(sceneA)"
      :label-b="'B · ' + shortDate(sceneB)"
      :title="'双屏对比 · ' + index.toUpperCase()"
      @close="swipe = null"
    />
  </div>
</template>

<script setup>
import { computed, nextTick, onMounted, ref, watch } from 'vue'
import MapCanvas from './MapCanvas.vue'
import MessageBox from './MessageBox.vue'
import GuideModal from './GuideModal.vue'
import SwipeCompare from './SwipeCompare.vue'
import TourGuide from './TourGuide.vue'

defineEmits(['home'])

const INDICES = [
  { name: 'ndvi', desc: '植被指数', group: '植被', need: ['red', 'nir'] },
  { name: 'evi', desc: '增强植被', group: '植被', need: ['blue', 'red', 'nir'] },
  { name: 'savi', desc: '土壤调整植被', group: '植被', need: ['red', 'nir'] },
  { name: 'ndre', desc: '红边叶绿素', group: '植被', need: ['nir', 'rededge1'] },
  { name: 'ndwi', desc: '水体指数', group: '水体与水质', need: ['green', 'nir'] },
  { name: 'mndwi', desc: '改进水体', group: '水体与水质', need: ['green', 'swir16'] },
  { name: 'awei', desc: '自动水体提取', group: '水体与水质', need: ['green', 'nir', 'swir16', 'swir22'] },
  { name: 'fai', desc: '浮游藻类（水体）', group: '水体与水质', need: ['green', 'red', 'nir', 'swir16'] },
  { name: 'ndbi', desc: '建筑指数', group: '城市与裸土', need: ['swir16', 'nir'] },
  { name: 'bsi', desc: '裸土指数', group: '城市与裸土', need: ['swir16', 'red', 'nir', 'blue'] },
  { name: 'nbr', desc: '燃烧指数', group: '燃烧与灾害', need: ['nir', 'swir22'] },
  { name: 'nbr2', desc: '燃烧指数2', group: '燃烧与灾害', need: ['swir16', 'swir22'] },
  { name: 'ndmi', desc: '植被水分', group: '水分与干旱', need: ['nir', 'swir16'] },
  { name: 'ndsi', desc: '积雪', group: '积雪与盐分', need: ['green', 'swir16'] },
  { name: 'si', desc: '盐分', group: '积雪与盐分', need: ['red', 'swir16'] },
]

const GROUP_ORDER = ['植被', '水体与水质', '城市与裸土', '燃烧与灾害', '水分与干旱', '积雪与盐分']

const guideOpen = ref(false)
const tourOpen = ref(false)

const TOUR_STEPS = [
  {
    selector: '#tour-setup',
    title: '选择影像与指数',
    text: '先选模式（单景指数 / 两期变化），再选已下载影像与指数。只列出波段齐全的指数；缺波段会提示。阈值用于分类统计或变化判定。',
  },
  {
    selector: '#tour-run',
    title: '开始计算',
    text: '点击后计算指数并生成伪彩栅格，右侧显示统计（均值/中位数/有效面积/超阈值面积等），地图自动叠加结果。',
  },
  {
    selector: '#tour-swipe',
    fallback: '#tour-setup',
    title: '双屏对比（两期）',
    text: '两期变化模式下可用：分别计算两期后弹出全屏对比，中间黄色分割条可左右拖动查看变化。',
  },
  {
    selector: '#center',
    title: '地图与图例',
    text: '左上依次切换「原始影像 / 结果影像 / 阈值矢量 / AOI」，两期变化模式还有「双屏比对」；右下切换底图，底部显示经纬度与比例尺。',
  },
  {
    selector: '#tour-result',
    title: '分析结果',
    text: '这里显示统计结果表：均值、中位数、P10/P90、有效面积与占比、超阈值面积等；结果栅格保存在 data/analysis。',
  },
  {
    selector: '#tour-history',
    title: '历史分析',
    text: '每次计算都会留一条记录（指数、阈值、面积、时间）。点卡片或「加载」可回看该次结果并把左侧参数一起还原；「下载」导出 zip（栅格+统计+快视图+矢量），「删除」清掉本地产物。',
    padding: 6,
  },
]
const resultVisible = ref(true)
const indexGroups = computed(() =>
  GROUP_ORDER.map((g) => ({ label: g, items: availableIndices.value.filter((i) => i.group === g) }))
    .filter((g) => g.items.length),
)
const comparing = ref(false)
const logs = ref([])

function goDownload() {
  location.hash = '#/download'
}
function logLn(s) {
  logs.value.push(`[${new Date().toLocaleTimeString()}] ${s}`)
  if (logs.value.length > 300) logs.value = logs.value.slice(-200)
  console.debug('[rspipe]', s)
}
const swipe = ref(null)
const history = ref([])
const msgBox = ref(null)
function showToast(m) { msgBox.value?.toast(m) }
const sourceOn = ref(true)      // 原始影像（分析目录里的 source_4326.tif）
const aoiOn = ref(true)         // 下载 AOI 边界
const hasSource = ref(false)
const hasAoi = ref(false)
const vectorOn = ref(false)
const vectorBusy = ref(false)
const vectorKey = ref('')
const vectorName = ref('ndvi')
const vectorOp = ref('above')
const mode = ref('single')
const scenes = ref([])
const sceneA = ref('')
const sceneB = ref('')
const index = ref('ndvi')
const threshold = ref(0.3)
const meta = ref({})
const running = ref(false)
const error = ref('')
const stats = ref(null)
const preview = ref(null)
const tileTemplate = ref('')
const mapRef = ref(null)

const curMeta = computed(() => meta.value[index.value] || {})
const sliderRange = computed(() => {
  const m = curMeta.value
  if (mode.value === 'change') {
    const t = m.change_threshold ?? 0.1
    return [0, Math.max(0.5, t * 3), 0.01]
  }
  const s = m.slider
  return Array.isArray(s) ? s : [-0.8, 0.8, 0.05]
})
const current = computed(() => scenes.value.find((s) => s.scene_id === sceneA.value) || null)
const availableIndices = computed(() => {
  const bands = new Set(current.value?.bands || [])
  return INDICES.filter((i) => i.need.every((b) => bands.has(b) || (b === 'nir' && bands.has('nir08'))))
})
const missingBands = computed(() => {
  const bands = new Set(current.value?.bands || [])
  const def = INDICES.find((i) => i.name === index.value)
  if (!def || !current.value) return []
  return def.need.filter((b) => !bands.has(b) && !(b === 'nir' && bands.has('nir08')))
})
const canRun = computed(() =>
  mode.value === 'single' ? !!sceneA.value : !!sceneA.value && !!sceneB.value && sceneA.value !== sceneB.value)

const statRows = computed(() => {
  const s = stats.value || {}
  const rows = {}
  const add = (k, v) => { if (v !== undefined && v !== null) rows[k] = v }
  if (s.mean !== undefined) {
    add('均值', s.mean); add('中位数', s.median)
    add('P10 / P90', `${s.p10} / ${s.p90}`)
    add('有效面积', s.area_km2 + ' km²')
    if (s.valid_fraction !== undefined) add('有效占比', (s.valid_fraction * 100).toFixed(1) + '%')
  }
  if (s.above_area_km2 !== undefined) {
    add(`大于阈值 ${s.threshold}`, `${s.above_area_km2} km²（${(s.above_ratio * 100).toFixed(1)}%）`)
  }
  if (s.kind === 'change') {
    add('平均变化', s.mean_diff)
    add(`减少（< -${s.threshold}）`, s.decrease_km2 + ' km²')
    add(`增加（> ${s.threshold}）`, s.increase_km2 + ' km²')
    add('重叠有效面积', s.valid_km2 + ' km²')
  }
  return rows
})

const legendCss = computed(() => {
  const c = curMeta.value.colors || ['#8b6e50', '#e6d778', '#007837']
  const stops = mode.value === 'change' ? ['#c82828', '#f5f5f5', '#00823c'] : c
  return `linear-gradient(90deg, ${stops.join(',')})`
})
const legendLow = computed(() =>
  mode.value === 'change' ? '减少' : curMeta.value.low || '低',
)
const legendHigh = computed(() =>
  mode.value === 'change' ? '增加' : curMeta.value.high || '高',
)
const legendNote = computed(() => {
  const m = curMeta.value
  if (mode.value === 'change') {
    return `白=变化小于阈值 ±${threshold.value}\n红=指标下降，绿=上升`
  }
  const desc = INDICES.find((i) => i.name === index.value)?.desc || ''
  const range = m.vmin !== undefined ? `色带区间 ${m.vmin}~${m.vmax}` : ''
  const line1 = [index.value.toUpperCase(), desc, range].filter(Boolean).join(' · ')
  const line2 = m.label ? `>${threshold.value} 判为「${m.high}」` : ''
  return [line1, line2].filter(Boolean).join('\n')
})

async function loadScenes() {
  try {
    const r = await fetch('/api/analysis/scenes')
    scenes.value = await r.json()
    logLn(`已下载影像 ${scenes.value.length} 景`)
  } catch (e) {
    error.value = '读取已下载影像失败'
  }
}

async function run() {
  running.value = true
  error.value = ''
  logLn(mode.value === 'single'
    ? `计算 ${index.value.toUpperCase()} · ${sceneA.value}`
    : `两期变化 ${index.value.toUpperCase()} · A=${sceneA.value} B=${sceneB.value}`)
  stats.value = null
  try {
    if (mode.value === 'single') {
      const r = await fetch('/api/analysis/run', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          scene_id: sceneA.value,
          provider: current.value?.provider,
          index: index.value,
          threshold: threshold.value,
        }),
      })
      const j = await r.json()
      if (!j.ok) { error.value = j.error || '计算失败'; logLn('失败: ' + error.value); return }
      stats.value = { ...j.stats, ...j.classify }
      logLn(`完成 · 均值 ${j.stats.mean} · 有效 ${j.stats.area_km2} km²`)
      tileTemplate.value = j.tiles
      preview.value = {
        scene_id: sceneA.value, ready: true,
        bounds: j.stats.bounds,
      }
      resultVisible.value = true
      hasSource.value = !!j.has_source
      applySource(sceneA.value, j.stats.bounds)
      applyAoi(sceneA.value)
      mapRef.value?.setPreviewTiles(sceneA.value, 'rgb', '', '', j.stats.bounds)
      mapRef.value?.fitBbox(j.stats.bounds)
      vectorKey.value = sceneA.value
      vectorName.value = index.value
      vectorOp.value = 'above'
      if (vectorOn.value) loadVector()
    } else {
      const r = await fetch('/api/analysis/change', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          scene_a: sceneA.value, scene_b: sceneB.value,
          provider: current.value?.provider, index: index.value, threshold: threshold.value,
        }),
      })
      const j = await r.json()
      if (!j.ok) { error.value = j.error || '计算失败'; logLn('失败: ' + error.value); return }
      stats.value = j.stats
      logLn(`变化完成 · 减少 ${j.stats.decrease_km2} km² / 增加 ${j.stats.increase_km2} km²`)
      tileTemplate.value = j.tiles
      const bounds = statsBounds()
      preview.value = { scene_id: j.key, ready: true, bounds }
      resultVisible.value = true
      hasSource.value = !!j.has_source
      applySource(j.key, bounds)
      applyAoi(j.key)
      mapRef.value?.setPreviewTiles(j.key, 'rgb', '', '', bounds)
      if (bounds) mapRef.value?.fitBbox(bounds)
      vectorKey.value = j.key
      vectorName.value = index.value + '_change'
      vectorOp.value = 'abs'
      if (vectorOn.value) loadVector()
    }
  } catch (e) {
    error.value = '请求失败：' + (e.message || e)
    logLn(error.value)
  } finally {
    running.value = false
    loadHistoryList()
  }
}

function shortDate(sid) {
  const m = String(sid || '').match(/(20\d{6})/)
  return m ? m[1] : sid
}

async function computeOne(sid) {
  const sc = scenes.value.find((s) => s.scene_id === sid)
  const r = await fetch('/api/analysis/run', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ scene_id: sid, provider: sc?.provider, index: index.value, threshold: threshold.value }),
  })
  const j = await r.json()
  if (!j.ok) throw new Error(j.error || '计算失败')
  return j
}

async function openSwipe() {
  comparing.value = true
  logLn(`双屏对比准备 · ${index.value.toUpperCase()}`)
  error.value = ''
  try {
    const ja = await computeOne(sceneA.value)
    const jb = await computeOne(sceneB.value)
    logLn('双屏对比就绪')
    swipe.value = {
      tileA: ja.tiles,
      tileB: jb.tiles,
      bounds: ja.stats.bounds || jb.stats.bounds || statsBounds(),
    }
  } catch (e) {
    error.value = '双屏对比失败：' + (e.message || e)
  } finally {
    comparing.value = false
  }
}

function toggleResult() {
  resultVisible.value = !resultVisible.value
  const p = preview.value
  if (!p) return
  if (resultVisible.value) mapRef.value?.setPreviewTiles(p.scene_id, 'rgb', '', '', p.bounds)
  else mapRef.value?.clearPreview()
}

async function loadVector() {
  if (!vectorKey.value) return
  vectorBusy.value = true
  try {
    const r = await fetch('/api/analysis/vectorize', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        key: vectorKey.value,
        name: vectorName.value,
        threshold: threshold.value,
        op: vectorOp.value,
      }),
    })
    const gj = await r.json()
    const n = (gj.features || []).length
    mapRef.value?.setResultVector(gj)
    mapRef.value?.setVectorVisible(true)
    logLn(n ? `矢量结果 ${n} 个面` : '矢量结果为空（当前阈值下没有像元）')
  } catch (e) {
    logLn('矢量生成失败: ' + e)
  } finally {
    vectorBusy.value = false
  }
}
async function toggleVector() {
  if (!preview.value) return
  vectorOn.value = !vectorOn.value
  if (!vectorOn.value) {
    mapRef.value?.setVectorVisible(false)
    return
  }
  await loadVector()
}
function fmtTime(ts) {
  if (!ts) return ''
  const d = new Date(ts * 1000)
  return `${d.getMonth() + 1}/${d.getDate()} ${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`
}
async function loadHistoryList() {
  try {
    const r = await fetch('/api/analysis/history')
    history.value = (await r.json()).items || []
  } catch { history.value = [] }
}
function histLine(h) {
  const s = h.stats || {}
  const thr = h.threshold ?? s.threshold ?? '-'
  if (h.mode === 'change' || String(h.index).endsWith('_change')) {
    return `减少 ${s.decrease_km2 ?? '-'} / 增加 ${s.increase_km2 ?? '-'} km² · 阈值 ${thr}`
  }
  return `均值 ${s.mean ?? '-'} · 面积 ${s.area_km2 ?? '-'} km² · 阈值 ${thr}`
}
async function loadHistory(h) {
  const isChange = h.mode === 'change' || String(h.index).endsWith('_change')
  const base = String(h.index).replace(/_change$/, '')
  const name = isChange ? `${base}_change` : base

  // 左侧所有属性都跟着历史记录走
  mode.value = isChange ? 'change' : 'single'
  sceneA.value = h.scene_a || ''
  sceneB.value = isChange ? h.scene_b || '' : ''
  index.value = base
  await nextTick()   // 等 mode/index 的 watcher 跑完，再写回真实阈值

  const thr = h.threshold ?? (h.stats || {}).threshold
  if (thr !== null && thr !== undefined) threshold.value = thr
  else applyDefaultThreshold()

  resultVisible.value = true
  stats.value = { ...(h.stats || {}) }
  tileTemplate.value = `/api/analysis/tiles/${h.key}/${name}/{z}/{x}/{y}.png`
  const bounds = h.stats?.bounds
  preview.value = { scene_id: h.key, ready: true, bounds }
  hasSource.value = !!h.has_source
  applySource(h.key, bounds)
  applyAoi(h.key)
  if (bounds) {
    mapRef.value?.setPreviewTiles(h.key, 'rgb', '', '', bounds)
    mapRef.value?.fitBbox(bounds)
  }
  vectorKey.value = h.key
  vectorName.value = name
  vectorOp.value = isChange ? 'abs' : 'above'
  if (vectorOn.value) loadVector()
  logLn(`载入历史分析 ${h.key}`)
}
async function deleteHistory(h) {
  const yes = await msgBox.value?.confirm({
    title: '删除分析结果',
    message: `将删除 ${h.key} 的 ${h.index.toUpperCase()} 结果（栅格 + 统计 + 快视图），不可恢复。`,
    okText: '删除',
  })
  if (!yes) return
  try {
    const r = await fetch(`/api/analysis/${encodeURIComponent(h.key)}`, { method: 'DELETE' })
    if (!r.ok) {
      showToast('删除失败：' + (await r.text()).slice(0, 80))
      return
    }
    if (preview.value?.scene_id === h.key) {
      preview.value = null
      stats.value = null
      mapRef.value?.clearPreview()
    }
    logLn(`已删除分析结果 ${h.key}`)
    loadHistoryList()
  } catch (e) {
    showToast('删除失败: ' + e)
  }
}
function downloadResult(keyArg) {
  const key = typeof keyArg === 'string' ? keyArg : preview.value?.scene_id
  if (!key) return
  const a = document.createElement('a')
  a.href = `/api/analysis/export/${encodeURIComponent(key)}`
  a.download = ''
  document.body.appendChild(a)
  a.click()
  a.remove()
  logLn(`导出分析结果 ${key}.zip`)
}
function applySource(key, bounds) {
  if (!key) return
  const url = `/api/analysis/source/${encodeURIComponent(key)}/{z}/{x}/{y}.png`
  if (sourceOn.value) mapRef.value?.setSourceTiles(url, bounds)
}
async function applyAoi(key) {
  if (!key) return
  try {
    const r = await fetch(`/api/analysis/aoi/${encodeURIComponent(key)}`)
    const gj = await r.json()
    const f = (gj.features || [])[0]
    hasAoi.value = !!f
    mapRef.value?.setAoiPolygon(f ? f.geometry : null)
    mapRef.value?.setAoiVisible(aoiOn.value)
  } catch {
    hasAoi.value = false
  }
}
function toggleSource() {
  sourceOn.value = !sourceOn.value
  mapRef.value?.setSourceVisible(sourceOn.value)
}
function toggleAoi() {
  aoiOn.value = !aoiOn.value
  mapRef.value?.setAoiVisible(aoiOn.value)
}
function closeLayers() {
  preview.value = null
  hasSource.value = false
  hasAoi.value = false
  mapRef.value?.clearSource()
  mapRef.value?.clearPreview()
  mapRef.value?.clearResultVector()
  mapRef.value?.setAoiPolygon(null)
}

function statsBounds() {
  const s = scenes.value.find((x) => x.scene_id === sceneA.value)
  return s?.bounds || null
}

async function loadMeta() {
  try {
    const r = await fetch('/api/analysis/meta')
    const j = await r.json()
    meta.value = j.indices || {}
  } catch { /* 保留默认 */ }
}
function applyDefaultThreshold() {
  const m = meta.value[index.value] || {}
  const v = mode.value === 'change' ? m.change_threshold : m.threshold
  if (typeof v === 'number') threshold.value = v
}
watch(index, applyDefaultThreshold)
watch(mode, applyDefaultThreshold)
onMounted(async () => {
  await loadMeta()
  applyDefaultThreshold()
  loadScenes()
  loadHistoryList()
})
</script>

<style scoped>
.analysis { display: flex; flex-direction: column; height: 100vh; background: linear-gradient(180deg, #eef1f6, #e7ebf2); }
#bar { flex: none; display: flex; align-items: center; gap: 14px; padding: 9px 16px; background: var(--panel); border-bottom: 1px solid var(--line); }
#bar .brand { font-size: 14px; font-weight: 700; display: flex; align-items: center; gap: 9px; }
#bar .brand i { width: 20px; height: 20px; border-radius: 6px; background: linear-gradient(135deg, var(--accent), #22d3ee); display: inline-block; }
#bar .sp { flex: 1; }
.back { flex: none; }
#top { flex: 1; min-height: 0; display: flex; gap: 12px; padding: 12px; }
#left { width: 276px; flex: none; display: flex; flex-direction: column; gap: 12px; overflow: auto; }
#center { flex: 1; position: relative; min-width: 0; background: var(--panel); border: 1px solid var(--line); border-radius: 12px; overflow: hidden; box-shadow: var(--shadow); }
#right { width: 330px; flex: none; display: flex; flex-direction: column; gap: 12px; overflow: hidden; }
.reslist { flex: 1; min-height: 0; overflow: auto; }
.guide-btn { width: 100%; min-height: 34px; font-size: 13px; }
.hist {
  border: 1px solid var(--line); border-radius: 10px;
  padding: 8px 10px; margin-bottom: 8px; background: var(--panel);
}
.hist.active { border-color: var(--accent); box-shadow: 0 0 0 1px var(--accent) inset; }
.hrow { display: flex; align-items: center; gap: 8px; cursor: pointer; }
.hname { flex: 1; min-width: 0; font-size: 12px; font-weight: 600; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.hist .badge { font-size: 10.5px; }
.seg { display: flex; gap: 6px; margin-top: 2px; }
.seg button { flex: 1; }
.row { margin-top: 10px; }
.row button { width: 100%; }
.warn { margin-top: 8px; font-size: 11.5px; color: #b91c1c; background: #fef2f2; border: 1px solid #fecaca; border-radius: 8px; padding: 6px 8px; }
.kv { width: 100%; font-size: 12px; border-collapse: collapse; }
.kv th { text-align: left; color: var(--muted); padding: 4px 6px; white-space: nowrap; font-weight: 400; vertical-align: top; }
.kv td { border-bottom: 1px solid var(--line); word-break: break-all; }
.lbar { display: block; height: 10px; border-radius: 5px; }
.lv { display: flex; justify-content: space-between; font-size: 10.5px; color: var(--muted); margin-top: 2px; }
.layerbar {
  position: absolute; left: 12px; top: 12px; z-index: 31;
  display: flex; flex-direction: column; gap: 4px; width: 90px; box-sizing: border-box;
  background: rgba(255,255,255,.96); border: 1px solid var(--line);
  border-radius: 10px; padding: 5px 7px; box-shadow: 0 4px 18px rgba(20,30,45,.14);
}
.layerbar button { width: 100%; min-height: 26px; padding: 2px 6px; font-size: 11.5px; white-space: nowrap; }
#center > .badge {
  position: absolute; left: 50%; top: 10px; transform: translateX(-50%);
  z-index: 20; max-width: 70%;
  background: rgba(255,255,255,.95); border: 1px solid var(--line);
  border-radius: 8px; padding: 3px 10px; font-size: 11.5px;
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}
</style>
