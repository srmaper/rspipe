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
        <div class="hint">{{ j.type === 'raw' ? 'raw 全波段' : 'processed 成果' }} · {{ j.message }}</div>
        <div v-if="j.status === 'ok' || j.status === 'error'" class="row act" style="margin-top:6px">
          <button @click="$emit('preview', j)">预览</button>
          <button @click="$emit('meta', j)">元数据</button>
          <button @click="$emit('download', j)">下载</button>
          <button class="danger" @click="$emit('delete', j)">删除</button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
defineProps({ jobs: Array })
defineEmits(['preview', 'meta', 'download', 'delete'])
const statusText = s => ({ queued: '排队', running: '运行', ok: '完成', error: '失败' }[s] || s)
const dotColor = s => ({ ok: '#16a34a', running: '#2563eb', error: '#dc2626', queued: '#aab2bd' }[s] || '#aab2bd')
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
