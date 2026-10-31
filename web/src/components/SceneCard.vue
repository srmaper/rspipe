<template>
  <div class="card scene" :class="{ active }">
    <div class="row" style="margin-top:0">
      <div class="grow" style="cursor:pointer; min-width:0" @click="$emit('select', scene)">
        <div style="font-weight:600;font-size:12.5px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap">{{ scene.scene_id }}</div>
        <div class="hint" style="overflow:hidden; text-overflow:ellipsis; white-space:nowrap">{{ fmtTime }} · {{ scene.provider }}</div>
      </div>
      <div style="flex:none">
        <span v-if="scene.tier" class="badge t1">{{ scene.tier }}</span>
        <span class="badge" :class="cloudCls" :title="cloudTip">云 {{ cloudText }}</span>
        <span class="badge c30">盖 {{ ovPct }}%</span>
      </div>
    </div>
    <div class="row" style="margin-top:8px">
      <button
        class="primary"
        :disabled="busy || downloadOk"
        :title="'按当前 AOI 裁剪下载（≤500 km²）'"
        @click="downloadOk ? null : $emit('download', scene, 'aoi')"
      >
        {{ downloadOk ? '✔ 已下载' : busy ? btnText : 'AOI 下载' }}
      </button>
      <button
        :disabled="busy"
        title="下载整景全分辨率（体积可能达数 GB）"
        @click="$emit('download', scene, 'full')"
      >
        整景下载
      </button>
    </div>
    <div v-if="job && !downloadOk" class="pbar"><div :style="{ width: (job.progress || 0) * 100 + '%' }"></div></div>
    <div v-if="job && !downloadOk" class="hint">{{ job.message }}</div>
  </div>
</template>

<script setup>
import { computed } from 'vue'

const props = defineProps({
  scene: Object,
  active: Boolean,
  previewing: Boolean,
  downloadOk: Boolean,   // raw 下载已完成 → 下一动作才是预览
  job: Object,
})
defineEmits(['select', 'download'])

const busy = computed(() => props.job && ['queued', 'running'].includes(props.job.status))
const fmtTime = computed(() => (props.scene.datetime || '').slice(0, 10))
const cloudPct = computed(() => {
  const s = props.scene
  if (s.aoi_cloud != null) return Math.round(s.aoi_cloud * 1000) / 10
  if (s.cloud_cover != null) return Math.round(s.cloud_cover * 10) / 10
  return null
})
const cloudText = computed(() => {
  if (cloudPct.value == null) return '?'
  return (props.scene.aoi_cloud != null ? '' : '≈') + cloudPct.value + '%'
})
const cloudTip = computed(() =>
  props.scene.aoi_cloud != null ? 'AOI 内实测云+云影占比' : '整景云量（未做 AOI 实测，仅供参考）',
)
const ovPct = computed(() => Math.round((props.scene.overlap || 0) * 100))
const cloudCls = computed(() => {
  const v = cloudPct.value ?? 0
  return v <= 10 ? 't1' : v <= 30 ? 'c50' : 'c80'
})
const jobText = computed(() => {
  const j = props.job
  if (!j) return ''
  if (j.status === 'error') return '✘ 失败'
  return `${Math.round((j.progress || 0) * 100)}%`
})
const btnText = computed(() => {
  const t = '⬇ 下载影像'
  return props.job ? jobText.value + ' ' + t : t
})
</script>

<style scoped>
.scene { margin-bottom: 9px; }
.scene.active { border-color: var(--accent); box-shadow: 0 0 0 1px var(--accent); }
</style>
