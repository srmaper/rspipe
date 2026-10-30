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
.tag { font-size: 10px; color: #15803d; background: #f0fdf4; border: 1px solid #bbf7d0; border-radius: 8px; padding: 0 5px; margin-left: 2px; }
</style>
