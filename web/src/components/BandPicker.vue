<template>
  <teleport to="body">
    <div class="mask" @click.self="$emit('cancel')">
      <div class="box card">
        <h3>{{ sid }}</h3>
        <div class="hint" style="margin-bottom:8px">选择要下载的波段（AOI 裁剪 · 8MB×6线程续传）</div>
        <div class="presets">
          <button @click="sel = CORE">常用 RGBN</button>
          <button @click="sel = ITEMS.map(v => v.k)">全选（{{ ITEMS.length }}）</button>
        </div>
        <div class="grid">
          <label v-for="it in ITEMS" :key="it.k" class="chk">
            <input type="checkbox" :value="it.k" v-model="sel" /> {{ it.n }}
            <span class="muted">{{ it.k }}</span>
            <span v-if="have.includes(it.k)" class="tag">已下载</span>
          </label>
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
import { ref, computed, onMounted } from 'vue'

const props = defineProps({
  sid: String,
  sentinel: Boolean,   // true=Sentinel-2；false=Landsat
  provider: String,
  areaKm2: { type: Number, default: 0 },
})
const emit = defineEmits(['confirm', 'cancel'])

const ITEMS = computed(() => {
  const s2 = [
    ['coastal', '海岸气溶胶'], ['blue', '蓝'], ['green', '绿'], ['red', '红'],
    ['rededge1', '红边1'], ['rededge2', '红边2'], ['rededge3', '红边3'],
    ['nir', '近红外'], ['nir08', '窄近红外'], ['nir09', '水汽'],
    ['swir16', '短波1'], ['swir22', '短波2'], ['scl', '场景分类'],
  ]
  const ls = [
    ['coastal', '海岸气溶胶'], ['blue', '蓝'], ['green', '绿'], ['red', '红'],
    ['nir08', '近红外'], ['swir16', '短波1'], ['swir22', '短波2'], ['qa_pixel', '云标志'],
  ]
  return (props.sentinel ? s2 : ls).map(([k, n]) => ({ k, n }))
})
const CORE = computed(() =>
  props.sentinel ? ['blue', 'green', 'red', 'nir', 'scl'] : ['blue', 'green', 'red', 'nir08', 'qa_pixel'])

const sel = ref([...CORE.value])
const have = ref([])

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
  emit('confirm', [...sel.value])
}
</script>

<style scoped>
.mask { position: fixed; inset: 0; background: rgba(0,0,0,0.45); z-index: 100; }
.box { position: absolute; left: 50%; top: 50%; transform: translate(-50%, -50%); width: 420px; max-height: 76vh; overflow: auto; }
.presets { display: flex; gap: 6px; margin-bottom: 8px; }
.grid { display: grid; grid-template-columns: 1fr 1fr; gap: 2px 10px; margin-bottom: 12px; }
.chk { display: flex; gap: 6px; align-items: center; color: var(--ink); font-size: 12px; margin: 2px 0; }
.chk input { width: auto; }
.est {
  margin: 4px 0 10px; padding: 7px 10px; border-radius: 8px;
  background: var(--track); border: 1px solid var(--line);
  font-size: 11.5px; color: var(--ink); line-height: 1.7;
}
.tag { font-size: 10px; color: #15803d; background: #f0fdf4; border: 1px solid #bbf7d0; border-radius: 8px; padding: 0 5px; margin-left: 2px; }
</style>
