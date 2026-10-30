<template>
  <div class="analysis">
    <div id="bar">
      <span class="brand"><i></i> 数据分析</span>
      <button class="back" @click="$emit('home')">← 首页</button>
      <span class="sp"></span>
      <span class="muted">{{ sceneA ? sceneA : '' }}</span>
    </div>

    <div id="top">
      <!-- 左栏 -->
      <div id="left">
        <div class="card guide-entry" @click="guideOpen = true">
          <h3>使用指南</h3>
          <div class="ge-desc">各分析工具所需的栅格、参数与结果说明</div>
          <button class="primary ge-btn">查看指南</button>
        </div>

        <div class="card">
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

          <label>指数</label>
          <select v-model="index">
            <option v-for="i in availableIndices" :key="i.name" :value="i.name">
              {{ i.name.toUpperCase() }} · {{ i.desc }}
            </option>
          </select>
          <div v-if="missingBands.length" class="warn">
            缺少波段：{{ missingBands.join(', ') }}
          </div>

          <label>{{ mode === 'change' ? '变化阈值：' + threshold : '分类阈值：' + threshold }}</label>
          <input type="range" min="-0.8" max="0.8" step="0.05" v-model.number="threshold" />

          <div class="row">
            <button class="primary" :disabled="!canRun || running" @click="run">
              {{ running ? '计算中…' : '开始计算' }}
            </button>
          </div>
          <div class="row" v-if="mode === 'change'">
            <button :disabled="!canRun || comparing" @click="openSwipe">
              {{ comparing ? '准备中…' : '双屏对比' }}
            </button>
          </div>
          <div v-if="error" class="warn">{{ error }}</div>
        </div>

        <div class="card">
          <h3>图例</h3>
          <span class="lbar" :style="{ background: legendCss }"></span>
          <div class="lv"><span>{{ legendLow }}</span><span>{{ legendHigh }}</span></div>
          <div class="hint">{{ legendNote }}</div>
        </div>
        <div class="card logbox">
          <h3>日志</h3>
          <div class="log" ref="logEl">{{ logs.join('\n') }}</div>
        </div>

      </div>

      <!-- 中央地图 -->
      <div id="center">
        <MapCanvas ref="mapRef" :preview-scene="preview" :tile-template="tileTemplate" />
        <div class="layerbar" v-if="preview">
          <button :class="{ primary: resultVisible }" @click="toggleResult">结果</button>
          <button :class="{ primary: extentVisible }" @click="toggleExtent">范围</button>
        </div>
        <div class="badge" v-if="preview">{{ preview.scene_id }}</div>
      </div>

      <!-- 右栏 -->
      <div id="right">
        <div class="card" style="flex:1;min-height:0;display:flex;flex-direction:column">
          <h3>分析结果</h3>
          <div class="reslist">
            <div v-if="!stats" class="hint">选择影像与指数后点击「开始计算」。</div>
            <table v-else class="kv">
              <tr v-for="(v, k) in statRows" :key="k"><th>{{ k }}</th><td>{{ v }}</td></tr>
            </table>
          </div>
        </div>
      </div>
    </div>

    <GuideModal v-if="guideOpen" @close="guideOpen = false" />
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
import { computed, onMounted, ref } from 'vue'
import MapCanvas from './MapCanvas.vue'
import GuideModal from './GuideModal.vue'
import SwipeCompare from './SwipeCompare.vue'

defineEmits(['home'])

const INDICES = [
  { name: 'ndvi', desc: '植被指数', need: ['red', 'nir'] },
  { name: 'ndwi', desc: '水体指数', need: ['green', 'nir'] },
  { name: 'ndbi', desc: '建筑指数', need: ['swir16', 'nir'] },
  { name: 'nbr', desc: '燃烧指数', need: ['nir', 'swir22'] },
  { name: 'bsi', desc: '裸土指数', need: ['swir16', 'red', 'nir', 'blue'] },
  { name: 'ndre', desc: '红边叶绿素', need: ['nir', 'rededge1'] },
]

const guideOpen = ref(false)
const resultVisible = ref(true)
const comparing = ref(false)
const logs = ref([])
const logEl = ref(null)

function logLn(s) {
  logs.value.push(`[${new Date().toLocaleTimeString()}] ${s}`)
  if (logs.value.length > 300) logs.value = logs.value.slice(-200)
  requestAnimationFrame(() => {
    if (logEl.value) logEl.value.scrollTop = logEl.value.scrollHeight
  })
}
const swipe = ref(null)
const extentVisible = ref(true)
const mode = ref('single')
const scenes = ref([])
const sceneA = ref('')
const sceneB = ref('')
const index = ref('ndvi')
const threshold = ref(0.3)
const running = ref(false)
const error = ref('')
const stats = ref(null)
const preview = ref(null)
const tileTemplate = ref('')
const mapRef = ref(null)

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
  const c = {
    ndvi: ['#8b6e50', '#e6d778', '#007837'],
    ndwi: ['#967850', '#f5f5f5', '#1446a0'],
    ndbi: ['#007837', '#dcdcdc', '#c82828'],
    nbr: ['#282828', '#fac828', '#006e3c'],
    bsi: ['#f0f0f0', '#beaa8c', '#6e5032'],
    ndre: ['#8b6e50', '#ebdc78', '#00823c'],
  }[index.value] || ['#8b6e50', '#e6d778', '#007837']
  const stops = mode.value === 'change' ? ['#c82828', '#f5f5f5', '#00823c'] : c
  return `linear-gradient(90deg, ${stops.join(',')})`
})
const legendLow = computed(() => (mode.value === 'change' ? '减少' : '低'))
const legendHigh = computed(() => (mode.value === 'change' ? '增加' : '高'))
const legendNote = computed(() =>
  mode.value === 'change'
    ? `白=变化小于阈值 ±${threshold.value}；红=植被/指标下降；绿=上升`
    : `${index.value.toUpperCase()}：${INDICES.find((i) => i.name === index.value)?.desc || ''}`)

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
      mapRef.value?.setPreviewTiles(sceneA.value, 'rgb', '', '', j.stats.bounds)
      mapRef.value?.fitBbox(j.stats.bounds)
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
      mapRef.value?.setPreviewTiles(j.key, 'rgb', '', '', bounds)
      if (bounds) mapRef.value?.fitBbox(bounds)
    }
  } catch (e) {
    error.value = '请求失败：' + (e.message || e)
    logLn(error.value)
  } finally {
    running.value = false
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

function toggleExtent() {
  extentVisible.value = !extentVisible.value
  mapRef.value?.setExtentVisible(extentVisible.value)
}

function statsBounds() {
  const s = scenes.value.find((x) => x.scene_id === sceneA.value)
  return s?.bounds || null
}

onMounted(loadScenes)
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
.logbox { flex: none; height: 200px; display: flex; flex-direction: column; }
.log {
  flex: 1; min-height: 0; overflow: auto;
  background: #12161d; color: #c8d3e0;
  font: 11px/1.55 Consolas, "Microsoft YaHei UI", monospace;
  padding: 8px 10px; border-radius: 10px; white-space: pre-wrap;
}
.guide-entry { cursor: pointer; }
.guide-entry:hover { border-color: var(--accent); }
.ge-desc { font-size: 11.5px; color: var(--muted); line-height: 1.7; margin-bottom: 10px; }
.ge-btn { width: 100%; }
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
  display: flex; flex-direction: column; gap: 4px;
  background: rgba(255,255,255,.96); border: 1px solid var(--line);
  border-radius: 10px; padding: 4px; box-shadow: 0 4px 18px rgba(20,30,45,.14);
}
.layerbar button { min-height: 26px; padding: 2px 10px; font-size: 11.5px; white-space: nowrap; }
.badge {
  position: absolute; left: 50%; top: 10px; transform: translateX(-50%);
  z-index: 20; max-width: 70%;
  background: rgba(255,255,255,.95); border: 1px solid var(--line);
  border-radius: 8px; padding: 3px 10px; font-size: 11.5px;
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}
</style>
