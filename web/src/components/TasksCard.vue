<template>
  <div class="card taskcard">
    <h3>下载任务</h3>
    <div class="tasks">
      <div v-if="!jobs.length" class="hint">暂无任务。点击景卡片的「下载影像」发起。</div>
      <div v-for="j in jobs.slice(0, 30)" :key="j.id" class="tlr">
        <div class="name">
          <span class="dot" :style="{ background: dotColor(j.status) }"></span>
          <span class="grow name-text">{{ j.scene_id }}</span>
          <span class="st">{{ statusText(j.status) }} {{ Math.round((j.progress || 0) * 100) }}%</span>
        </div>
        <div class="pbar"><div :style="{ width: (j.progress || 0) * 100 + '%' }"></div></div>
        <div v-if="lineText(j)" class="hint">{{ lineText(j) }}</div>
        <div class="row act" style="margin-top:6px">
          <button v-if="j.status === 'running'" @click="$emit('pause', j)">暂停</button>
          <button v-else-if="j.status === 'paused' || j.status === 'error'" @click="$emit('resume', j)">继续</button>
          <button v-else-if="j.status === 'ok'" @click="$emit('preview', j)">预览</button>
          <button v-else disabled>排队中</button>
          <button v-if="j.status === 'ok'" @click="$emit('meta', j)">元数据</button>
          <button v-if="j.status === 'ok'" @click="$emit('download', j)">下载</button>
          <button v-if="j.status !== 'running' && j.status !== 'queued'" class="danger" @click="$emit('delete', j)">删除</button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
defineProps({ jobs: Array })
defineEmits(['preview', 'meta', 'download', 'delete', 'pause', 'resume'])
const statusText = s =>
  ({ queued: '排队', running: '运行', paused: '已暂停', ok: '完成', error: '失败' }[s] || s)
const dotColor = s =>
  ({ ok: '#16a34a', running: '#2563eb', paused: '#d97706', error: '#dc2626', queued: '#aab2bd' }[s] ||
    '#aab2bd')

function fmtTime(ts) {
  if (!ts) return ''
  const d = new Date(ts * 1000)
  const p = (n) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`
}

// 第二行：波段数/体积/进度提示 + 发起时间
function lineText(j) {
  const time = fmtTime(j.created)
  if (j.status === 'error') return [j.message || '失败', time].filter(Boolean).join(' · ')
  if (j.status === 'ok') {
    const bands = (j.detail && j.detail.bands) || []
    const mb = j.detail && j.detail.bytes ? Math.round(j.detail.bytes / 1048576) : 0
    const parts = []
    if (j.type === 'processed') parts.push('成果 COG')
    if (bands.length) parts.push(bands.length + ' 波段')
    if (mb) parts.push(mb + ' MB')
    parts.push(time)
    return parts.filter(Boolean).join(' · ')
  }
  return [j.message || '', time].filter(Boolean).join(' · ')
}
</script>

<style scoped>
.taskcard { flex: 1; min-height: 0; display: flex; flex-direction: column; }
.tasks { flex: 1; min-height: 0; overflow: auto; }
.tlr {
  border: 1px solid var(--line); border-radius: 10px; padding: 10px 12px;
  margin-bottom: 9px; box-shadow: var(--shadow);
}
.name { font-weight: 600; display: flex; align-items: center; gap: 8px; font-size: 12.5px; }
.name .dot { width: 9px; height: 9px; border-radius: 50%; flex: none; }
.name .st { color: var(--muted); font-weight: 500; font-size: 11.5px; }
.name-text { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.act button { flex: 1; min-height: 24px; padding: 2px 4px; font-size: 11.5px; white-space: nowrap; }
.act .danger { color: #b91c1c; border-color: #eec7c7; }
.act .danger:hover { background: #fdf1f1; border-color: #dc2626; color: #b91c1c; }
</style>
