<template>
  <teleport to="body">
    <div class="mask" @click.self="$emit('cancel')">
      <div class="box card">
        <h3>{{ sid }}</h3>
        <div class="hint" style="margin-bottom:8px">
          选择要下载的波段（AOI 裁剪 · 8MB×6线程续传）· <b>常用分析必选波段已锁定</b>
        </div>

        <div class="presets">
          <button
            v-for="p in presets"
            :key="p.key"
            :class="{ primary: sameSet(sel, presetBands(p)) }"
            @click="applyPreset(p)"
          >{{ p.label }}</button>
        </div>

        <div class="groups">
          <div v-for="g in groups" :key="g.label" class="group">
            <div class="gtitle">{{ g.label }}</div>
            <div class="grid">
              <label
                v-for="it in g.items"
                :key="it.k"
                class="chk"
                :class="{ locked: required.includes(it.k) }"
                :title="required.includes(it.k) ? '常用分析（真彩/NDVI/去云）必选，不能取消' : ''"
              >
                <input
                  type="checkbox"
                  :value="it.k"
                  v-model="sel"
                  :disabled="required.includes(it.k)"
                />
                {{ it.n }}
                <span class="muted">{{ it.k }}</span>
                <span v-if="required.includes(it.k)" class="lock">必选</span>
                <span v-else-if="have.includes(it.k)" class="tag">已下载</span>
              </label>
            </div>
          </div>
        </div>

        <div class="est" v-if="estimate">
          预估：约 <b>{{ estimate.mb }}</b> MB · 下载约 <b>{{ estimate.time }}</b>
          <span class="muted">（按 {{ areaKm2.toFixed(0) }} km²、300 KB/s 估算）</span>
        </div>
        <div class="row">
          <button class="grow" @click="$emit('cancel')">取消</button>
          <button class="primary grow" @click="go">开始下载（{{ sel.length }}）</button>
        </div>
      </div>
    </div>
  </teleport>
</template>

<script setup>
import { ref, computed, onMounted, watch } from 'vue'

const props = defineProps({
  sid: String,
  sentinel: Boolean,   // true=Sentinel-2；false=Landsat
  provider: String,
  areaKm2: { type: Number, default: 0 },
})
const emit = defineEmits(['confirm', 'cancel'])

// 每个可下载类型（传感器）都按同一套结构描述：分组 + 预设 + 必选
// 以后新增数据源，只要在这里补一份 META 即可
const S2_META = {
  required: ['blue', 'green', 'red', 'nir', 'scl'],
  groups: [
    { label: '可见光与近红外', items: [['blue', '蓝'], ['green', '绿'], ['red', '红'], ['nir', '近红外']] },
    { label: '红边（作物 / 叶绿素）', items: [['rededge1', '红边1'], ['rededge2', '红边2'], ['rededge3', '红边3']] },
    { label: '短波红外（水体 / 建筑 / 燃烧）', items: [['swir16', '短波1'], ['swir22', '短波2']] },
    { label: '窄波段与大气', items: [['coastal', '海岸气溶胶'], ['nir08', '窄近红外'], ['nir09', '水汽']] },
    { label: '质量与分类', items: [['scl', '场景分类']] },
  ],
  presets: [
    { key: 'core', label: '常用 RGBN', bands: ['blue', 'green', 'red', 'nir', 'scl'] },
    { key: 'analysis', label: '分析全能', bands: ['blue', 'green', 'red', 'nir', 'rededge1', 'swir16', 'swir22', 'scl'] },
    { key: 'all', label: '全选' },
  ],
}

const LS_META = {
  required: ['blue', 'green', 'red', 'nir08', 'qa_pixel'],
  groups: [
    { label: '可见光与近红外', items: [['blue', '蓝'], ['green', '绿'], ['red', '红'], ['nir08', '近红外']] },
    { label: '短波红外（水体 / 建筑 / 燃烧）', items: [['swir16', '短波1'], ['swir22', '短波2']] },
    { label: '窄波段与质量', items: [['coastal', '海岸气溶胶'], ['qa_pixel', '云标志']] },
  ],
  presets: [
    { key: 'core', label: '常用 RGBN', bands: ['blue', 'green', 'red', 'nir08', 'qa_pixel'] },
    { key: 'analysis', label: '分析全能', bands: ['blue', 'green', 'red', 'nir08', 'swir16', 'swir22', 'qa_pixel'] },
    { key: 'all', label: '全选' },
  ],
}

const meta = computed(() => (props.sentinel ? S2_META : LS_META))
const groups = computed(() =>
  meta.value.groups.map((g) => ({ ...g, items: g.items.map(([k, n]) => ({ k, n })) })),
)
const required = computed(() => meta.value.required)
const allKeys = computed(() => groups.value.flatMap((g) => g.items.map((i) => i.k)))
const presets = computed(() =>
  meta.value.presets.map((p) => ({ ...p, label: p.key === 'all' ? `全选（${allKeys.value.length}）` : p.label })),
)

function presetBands(p) {
  return p.key === 'all' ? allKeys.value : p.bands
}
function applyPreset(p) {
  sel.value = uniq([...required.value, ...presetBands(p)])
}
function sameSet(a, b) {
  return a.length === b.length && a.every((k) => b.includes(k))
}
function uniq(list) {
  return [...new Set(list)]
}

const sel = ref([...required.value])
const have = ref([])

watch(required, (r) => {
  // 切换传感器时保证必选始终在选中列表里
  sel.value = uniq([...r, ...sel.value])
})

onMounted(async () => {
  if (!props.provider) return
  try {
    const r = await fetch(`/api/scenes/${props.provider}/${encodeURIComponent(props.sid)}`)
    if (r.ok) {
      const d = await r.json()
      have.value = d.local_bands || []
    }
  } catch (e) { /* 忽略 */ }
})

// 各波段分辨率（米）
const RES = {
  coastal: 60, blue: 10, green: 10, red: 10, nir: 10,
  rededge1: 20, rededge2: 20, rededge3: 20,
  nir08: 20, nir09: 60, swir16: 20, swir22: 20, scl: 20,
  qa_pixel: 30,
}

const estimate = computed(() => {
  if (!props.areaKm2) return null
  const px10 = (props.areaKm2 * 1e6) / 100          // 10m 像元数
  let bytes = 0
  for (const k of sel.value) {
    const res = RES[k] || 30
    const px = px10 * (10 / res) ** 2
    bytes += px * 2 * 0.45                          // uint16 + deflate 约 45%
  }
  const mb = bytes / 1048576
  const sec = bytes / (300 * 1024)
  const time = sec < 60 ? `${Math.round(sec)} 秒` : `${(sec / 60).toFixed(1)} 分钟`
  return { mb: mb.toFixed(1), time }
})

function go() {
  emit('confirm', uniq([...required.value, ...sel.value]))
}
</script>

<style scoped>
.mask { position: fixed; inset: 0; background: rgba(0,0,0,0.45); z-index: 100; }
.box { position: absolute; left: 50%; top: 50%; transform: translate(-50%, -50%); width: 480px; max-height: 78vh; overflow: auto; }
.presets { display: flex; gap: 6px; margin-bottom: 8px; flex-wrap: wrap; }
.presets button { padding: 2px 10px; font-size: 11.5px; min-height: 26px; }
.groups { margin-bottom: 10px; }
.group + .group { margin-top: 8px; }
.gtitle { font-size: 11px; color: var(--muted); margin: 4px 0 2px; }
.grid { display: grid; grid-template-columns: 1fr 1fr; gap: 2px 10px; }
.chk { display: flex; gap: 6px; align-items: center; color: var(--ink); font-size: 12px; margin: 2px 0; }
.chk input { width: auto; }
.chk.locked { color: var(--muted); }
.chk.locked input { cursor: not-allowed; }
.lock {
  font-size: 10px; color: #1d4ed8; background: #eff6ff;
  border: 1px solid #bfdbfe; border-radius: 8px; padding: 0 5px; margin-left: 2px;
}
.est {
  margin: 4px 0 10px; padding: 7px 10px; border-radius: 8px;
  background: var(--track); border: 1px solid var(--line);
  font-size: 11.5px; color: var(--ink); line-height: 1.7;
}
.tag { font-size: 10px; color: #15803d; background: #f0fdf4; border: 1px solid #bbf7d0; border-radius: 8px; padding: 0 5px; margin-left: 2px; }
</style>
