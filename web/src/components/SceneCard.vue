<template>
  <div class="card scene" :class="{ active }">
    <div class="row" style="margin-top:0">
      <div class="grow" style="cursor:pointer; min-width:0" @click="$emit('select', scene)">
        <div style="font-weight:600;font-size:12.5px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap">{{ scene.scene_id }}</div>
        <div class="hint" style="overflow:hidden; text-overflow:ellipsis; white-space:nowrap">{{ fmtTime }} · {{ scene.provider }}</div>
      </div>
      <div style="flex:none">
        <span v-if="scene.tier" class="badge t1">{{ scene.tier }}</span>
        <span class="badge" :class="cloudCls">云 {{ aoiCloudPct }}%</span>
        <span class="badge c30">盖 {{ ovPct }}%</span>
      </div>
    </div>
    <div class="row" style="margin-top:8px">
      <button
        class="primary"
        :disabled="busy || downloadOk"
        @click="downloadOk ? null : $emit('download', scene)"
      >
        {{ downloadOk ? '✔ 下载完成' : btnText }}
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
const aoiCloudPct = computed(() => (props.scene.aoi_cloud == null ? '?' : Math.round(props.scene.aoi_cloud * 1000) / 10))
const ovPct = computed(() => Math.round((props.scene.overlap || 0) * 100))
const cloudCls = computed(() => (aoiCloudPct.value <= 10 ? 't1' : aoiCloudPct.value <= 30 ? 'c50' : 'c80'))
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
