<template>
  <div class="card">
    <h3>下载范围（AOI）</h3>
    <div class="row" style="margin-top:0">
      <button :class="{ primary: active === 'rect' }" @click="$emit('set', 'rect')">框选矩形</button>
      <button :class="{ primary: active === 'poly' }" @click="$emit('set', 'poly')">绘制多边形</button>
    </div>
    <div class="row">
      <button :class="{ primary: active === 'search' }" @click="$emit('set', 'search')">📍 位置搜索</button>
      <button class="danger" :disabled="!busy" @click="$emit('clear')">清除</button>
    </div>
    <div class="rbox" :class="{ bad: !coords }">
      <template v-if="coords">
        范围 <b>{{ aoiName || '手动圈选' }}</b><br>
        bbox {{ coords.map(v => v.toFixed(2)).join(', ') }}
      </template>
      <template v-else>范围：框选矩形 / 绘制多边形，或打开位置搜索输入地名</template>
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue'

const props = defineProps({
  bbox: Array,
  aoiName: String,
  active: String,   // 'rect' | 'poly' | 'search' | null —— 三按钮互斥单选
})
defineEmits(['set', 'clear'])
const busy = computed(() => !!props.bbox || !!props.aoiName)
const coords = computed(() => props.bbox || null)
</script>

<style scoped>
.rbox {
  margin-top: 10px; padding: 9px 11px; border-radius: 9px;
  background: #f0f6ff; border: 1px solid #cfe0fb; font-size: 11px; line-height: 1.8;
}
.rbox.bad { background: var(--track); border-color: var(--track); color: var(--muted); }
.danger { color: #b91c1c; border-color: #eec7c7; }
.danger:hover:not(:disabled) { background: #fdf1f1; border-color: #dc2626; color: #b91c1c; }
</style>
