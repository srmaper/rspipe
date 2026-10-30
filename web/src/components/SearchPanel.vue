<template>
  <div class="panel p">
    <label>时间范围</label>
    <div class="row">
      <input type="date" v-model="start" />
      <span class="muted">—</span>
      <input type="date" v-model="end" />
    </div>
    <label>整景云量上限：{{ cloud }}%</label>
    <input type="range" min="0" max="80" v-model.number="cloud" />
    <label>传感器</label>
    <label class="chk"><input type="checkbox" value="earth-search|sentinel-2-l2a" v-model="checks" /> Sentinel-2 (earth-search)</label>
    <label class="chk"><input type="checkbox" value="planetary-computer|landsat-c2-l2" v-model="checks" /> Landsat 8/9 (planetary-computer)</label>
    <button class="primary" style="width:100%;margin-top:10px" :disabled="loading" @click="go">
      {{ loading ? '检索中…' : '🔍 检索' }}
    </button>
    <div class="muted" style="margin-top:6px" v-if="selectedAoi">AOI: {{ selectedAoi }}</div>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'

const emit = defineEmits(['search'])
defineProps({ loading: Boolean, selectedAoi: String })

const start = ref('')
const end = ref('')
const cloud = ref(30)
const checks = ref(['earth-search|sentinel-2-l2a', 'planetary-computer|landsat-c2-l2'])

onMounted(() => {
  const d = new Date(Date.now() - 35 * 864e5)
  start.value = d.toISOString().slice(0, 10)
  end.value = new Date().toISOString().slice(0, 10)
})

function go() {
  const providers = checks.value.map(v => {
    const [p, c] = v.split('|')
    return [p, c]
  })
  emit('search', { start: start.value + 'T00:00:00Z', end: end.value + 'T23:59:59Z', cloud: cloud.value, providers })
}
</script>

<style scoped>
.p { padding: 10px; }
.chk { display: flex; gap: 6px; align-items: center; color: var(--text); font-size: 13px; }
.chk input { width: auto; }
</style>
