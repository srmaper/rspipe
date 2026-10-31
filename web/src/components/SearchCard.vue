<template>
  <div class="card">
    <h3>检索条件</h3>
    <label style="margin-top:0">时间范围</label>
    <div class="dr">
      <input type="date" v-model="cfg.start" />
      <span class="sep">—</span>
      <input type="date" v-model="cfg.end" />
    </div>
    <label>整景云量上限：{{ cfg.cloud }}%</label>
    <input type="range" min="0" max="80" v-model.number="cfg.cloud" style="width:100%" />
    <label>云量口径</label>
    <select v-model="cfg.cloud_mode" style="width:100%">
      <option value="scene">整景云量（快，约 1 秒）</option>
      <option value="aoi">AOI 实测（准，首次 20–40 秒）</option>
    </select>
    <label>传感器</label>
    <label class="chk"><input type="checkbox" value="earth-search|sentinel-2-l2a" v-model="checks" /> Sentinel-2</label>
    <label class="chk"><input type="checkbox" value="planetary-computer|landsat-c2-l2" v-model="checks" /> Landsat 8/9</label>
    <div class="row">
      <button class="primary" :disabled="!bboxReady || loading" @click="go">
        {{ loading ? '检索中…' : '🔍 检索' }}
      </button>
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue'

const props = defineProps({ cfg: Object, loading: Boolean, bboxReady: Boolean })
const emit = defineEmits(['search'])

const cfg = props.cfg
const checks = computed({
  get: () => cfg.providers.map(p => (Array.isArray(p) ? `${p[0]}|${p[1]}` : `${p.provider}|${p.collection}`)),
  set: v => {
    cfg.providers = v.map(s => {
      const [provider, collection] = s.split('|')
      return [provider, collection]
    })
  },
})

function go() {
  emit('search', {
    start: cfg.start + 'T00:00:00Z',
    end: cfg.end + 'T23:59:59Z',
    cloud: cfg.cloud,
    cloud_mode: cfg.cloud_mode || 'scene',
    providers: cfg.providers,
  })
}
</script>

<style scoped>
.dr { display: flex; gap: 5px; align-items: center; }
.dr input[type="date"] {
  flex: 1; min-width: 0; padding: 3px 2px;
  font-size: 11.5px; font-family: inherit;
}
.dr .sep { flex: none; color: var(--muted); }
.chk { display: flex; gap: 6px; align-items: center; color: var(--ink); font-size: 12px; margin: 3px 0; }
.chk input { width: auto; }
</style>
