<template>
  <teleport to="body">
    <div class="tour">
      <div class="spot" :style="spotStyle"></div>
      <div class="tip" :style="tipStyle">
        <div class="hd">
          <span class="step">第 {{ index + 1 }} / {{ steps.length }} 步</span>
          <span class="title">{{ cur.title }}</span>
        </div>
        <div class="bd">{{ cur.text }}</div>
        <div class="ft">
          <button v-if="index > 0" @click="prev">上一步</button>
          <button class="primary" @click="next">{{ index === steps.length - 1 ? '完成' : '下一步' }}</button>
          <button class="skip" @click="$emit('close')">跳过</button>
        </div>
      </div>
    </div>
  </teleport>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'

const props = defineProps({
  steps: { type: Array, required: true },   // [{selector, title, text, padding?}]
})
const emit = defineEmits(['close'])

const index = ref(0)
const rect = ref({ x: 0, y: 0, w: 0, h: 0 })
const cur = computed(() => props.steps[index.value] || { title: '', text: '' })

function measure() {
  const step = props.steps[index.value]
  if (!step) return
  const el = document.querySelector(step.selector)
  if (!el) {
    // 该步目标不存在（如还没检索、没有结果卡片）→ 自动跳过
    rect.value = { x: 0, y: 0, w: 0, h: 0 }
    skipMissing()
    return
  }
  el.scrollIntoView({ block: 'nearest', inline: 'nearest' })
  const r = el.getBoundingClientRect()
  const pad = step.padding ?? 6
  rect.value = { x: r.left - pad, y: r.top - pad, w: r.width + pad * 2, h: r.height + pad * 2 }
}

const spotStyle = computed(() => ({
  left: rect.value.x + 'px',
  top: rect.value.y + 'px',
  width: rect.value.w + 'px',
  height: rect.value.h + 'px',
  display: rect.value.w ? 'block' : 'none',
}))

const tipStyle = computed(() => {
  const { x, y, w, h } = rect.value
  const vw = window.innerWidth
  const vh = window.innerHeight
  const tipW = 320
  const gap = 12
  let left = x + w + gap
  let top = y
  if (left + tipW > vw - 12) left = Math.max(12, x - tipW - gap)
  if (left + tipW > vw - 12) { left = Math.min(vw - tipW - 12, Math.max(12, x)); top = y + h + gap }
  const estH = 150
  if (top + estH > vh - 12) top = Math.max(12, vh - estH - 12)
  return { left: left + 'px', top: top + 'px', width: tipW + 'px' }
})

let skipping = false
function skipMissing() {
  if (skipping) return
  skipping = true
  setTimeout(() => {
    skipping = false
    if (index.value >= props.steps.length - 1) emit('close')
    else index.value += 1
  }, 60)
}

function next() {
  if (index.value >= props.steps.length - 1) { emit('close'); return }
  index.value += 1
}
function prev() {
  if (index.value > 0) index.value -= 1
}

watch(index, measure)
onMounted(() => {
  measure()
  window.addEventListener('resize', measure)
  window.addEventListener('scroll', measure, true)
  window.addEventListener('keydown', onKey)
})
onBeforeUnmount(() => {
  window.removeEventListener('resize', measure)
  window.removeEventListener('scroll', measure, true)
  window.removeEventListener('keydown', onKey)
})
function onKey(e) {
  if (e.key === 'Escape') emit('close')
  if (e.key === 'ArrowRight' || e.key === 'Enter') next()
  if (e.key === 'ArrowLeft') prev()
}
</script>

<style scoped>
.tour { position: fixed; inset: 0; z-index: 300; }
.spot {
  position: fixed;
  border: 2px solid #38bdf8;
  border-radius: 10px;
  box-shadow: 0 0 0 9999px rgba(6, 10, 18, .62), 0 0 0 4px rgba(56, 189, 248, .25);
  transition: all .22s ease;
  pointer-events: none;
}
.tip {
  position: fixed;
  background: var(--panel);
  border: 1px solid var(--line);
  border-radius: 12px;
  padding: 12px 14px;
  box-shadow: 0 18px 50px rgba(2, 8, 20, .45);
  transition: all .22s ease;
}
.hd { display: flex; align-items: baseline; gap: 8px; margin-bottom: 6px; }
.step { font-size: 11px; color: #0284c7; background: #e0f2fe; border-radius: 8px; padding: 1px 7px; }
.title { font-size: 13.5px; font-weight: 700; }
.bd { font-size: 12.5px; color: var(--ink); line-height: 1.85; }
.ft { display: flex; gap: 8px; margin-top: 12px; justify-content: flex-end; }
.skip { border: none; background: transparent; color: var(--muted); }
</style>
