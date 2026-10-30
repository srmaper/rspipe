<template>
  <teleport to="body">
    <div class="mask" @click.self="$emit('close')">
      <div class="drawer">
        <div class="row">
          <h3 class="grow">{{ d.scene_id }}</h3>
          <button style="flex:none; min-height:26px; padding:2px 9px" @click="$emit('close')">✕</button>
        </div>
        <table class="kv">
          <tr><th>数据源</th><td>{{ d.provider }} / {{ d.collection }}</td></tr>
          <tr><th>时间</th><td>{{ (d.datetime || '').slice(0, 19).replace('T', ' ') }}</td></tr>
          <tr><th>整景云量</th><td>{{ d.cloud_cover == null ? '—' : d.cloud_cover.toFixed(2) + '%' }}</td></tr>
          <tr><th>Tier</th><td>{{ d.tier || '—' }}</td></tr>
          <tr v-if="d.bbox"><th>bbox</th><td>{{ d.bbox.map(v => v.toFixed(4)).join(', ') }}</td></tr>
          <tr v-for="(v, k) in rspiFields" :key="k">
            <th>{{ k }}</th><td>{{ v }}</td>
          </tr>
        </table>
        <div v-if="bandRows.length" class="bands">
          <div class="bh">波段资产</div>
          <div v-for="b in bandRows" :key="b.key" class="brow" :class="{ got: downloaded.has(b.key) }">
            <span class="bkey">{{ b.key }}<span v-if="label(b.key)" class="bcn">（{{ label(b.key) }}）</span></span>
            <span class="bmeta">
              <span class="bres">{{ b.resolution ? b.resolution + ' m' : '—' }}</span>
              <span v-if="downloaded.has(b.key)" class="tag">已下载</span>
            </span>
          </div>
        </div>
      </div>
    </div>
  </teleport>
</template>

<script setup>
import { computed } from 'vue'

const props = defineProps({ detail: Object })
defineEmits(['close'])
const d = computed(() => props.detail || {})
const downloaded = computed(() => new Set(d.value.local_bands || []))
const BAND_CN = {
  // Sentinel-2
  coastal: '海岸气溶胶',
  blue: '蓝 · 真彩/水体',
  green: '绿 · 真彩/植被',
  red: '红 · 真彩/植被',
  rededge1: '红边1 · 叶绿素',
  rededge2: '红边2 · 叶绿素',
  rededge3: '红边3 · 叶绿素',
  nir: '近红外 · NDVI',
  nir08: '窄近红外 · 植被',
  nir09: '水汽 · 卷云',
  swir16: '短波红外1 · 水分/火点',
  swir22: '短波红外2 · 矿物',
  scl: '场景分类 · 去云',
  aot: '气溶胶光学厚度',
  wvp: '水汽总量',
  visual: '真彩预览图',
  // Landsat
  qa_pixel: '像元质量 · 去云',
  qa_aerosol: '气溶胶质量',
  qa_radsat: '饱和质量',
  qa: '地表温度质量',
  lwir11: '热红外 · 地表温度',
  lwir: '热红外',
  // 通用
  preview_4326: '4326 预览栅格',
  composite: '合成反射率',
}

const label = (key) => BAND_CN[String(key || '').toLowerCase()] || ''

const bandRows = computed(() => {
  const info = d.value.band_info
  if (Array.isArray(info) && info.length) return info
  return (d.value.bands || []).map(k => ({ key: k, resolution: null }))
})
const rspiFields = computed(() => {
  const out = {}
  for (const [k, v] of Object.entries(d.value.properties || {})) {
    if (String(k).startsWith('rspi:')) out[k.slice(6)] = Array.isArray(v) ? JSON.stringify(v) : String(v)
  }
  return out
})
</script>

<style scoped>
.mask { position: fixed; inset: 0; background: rgba(0,0,0,0.45); z-index: 100; }
.drawer {
  position: absolute; left: 50%; top: 50%; transform: translate(-50%, -50%);
  width: 560px; max-width: calc(100vw - 48px);
  max-height: 80vh; overflow-y: auto;
  background: var(--panel); border: 1px solid var(--line); border-radius: 10px;
  padding: 16px; box-shadow: 0 18px 60px rgba(16, 24, 40, .28);
}
.kv { width: 100%; font-size: 12px; border-collapse: collapse; }
.kv th { text-align: left; color: var(--muted); padding: 4px 6px; white-space: nowrap; vertical-align: top; }
.bands { margin-top: 12px; border-top: 1px solid var(--line); padding-top: 8px; }
.bh { font-size: 11.5px; color: var(--muted); margin-bottom: 4px; }
.brow { display: flex; justify-content: space-between; padding: 3px 0; border-bottom: 1px dashed var(--line); font-size: 12px; }
.bkey { color: var(--ink); }
.bcn { color: var(--muted); font-size: 11.5px; }
.bres { color: var(--muted); font-variant-numeric: tabular-nums; margin-right: 6px; }
.bmeta { display: flex; align-items: center; }
.brow.got .bkey { color: #15803d; font-weight: 600; }
.tag { font-size: 10px; color: #15803d; background: #f0fdf4; border: 1px solid #bbf7d0; border-radius: 8px; padding: 0 5px; }
.kv td { border-bottom: 1px solid var(--line); word-break: break-all; }
h3 { margin: 0; font-size: 15px; }
</style>
