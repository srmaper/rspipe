<template>
  <div class="panel" style="padding:10px;margin-top:12px">
    <label>任务队列</label>
    <div v-if="!jobs.length" class="muted">暂无任务</div>
    <div v-for="j in jobs.slice(0, 8)" :key="j.id || j.scene_id + j.type" class="row" style="margin-bottom:4px">
      <span class="badge" :class="statusCls(j.status)">{{ statusText(j.status) }}</span>
      <span class="grow" style="font-size:12px">{{ j.scene_id }}</span>
      <span class="muted">{{ Math.round((j.progress || 0) * 100) }}%</span>
    </div>
  </div>
</template>

<script setup>
defineProps({ jobs: Array })
const statusText = s => ({ queued: '排队', running: '运行', ok: '完成', error: '失败' }[s] || s)
const statusCls = s => ({ ok: 't1', error: 'c80', running: 'c50' }[s] || 'c30')
</script>
