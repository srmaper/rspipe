<template>
  <div v-if="visible" class="upd" :class="{ ok: !info.has_update }">
    <div class="hd">
      <b v-if="info.has_update">发现新版本 {{ info.latest }}</b>
      <b v-else>已是最新版本</b>
      <span class="cur">当前 {{ info.current || '—' }}</span>
    </div>
    <div v-if="info.has_update && notes" class="notes">{{ notes }}</div>
    <div class="acts">
      <template v-if="info.has_update">
        <button class="primary" @click="download">下载安装包</button>
        <button @click="later">稍后</button>
        <button class="ghost" @click="skip">跳过此版本</button>
      </template>
      <button v-else @click="close">知道了</button>
    </div>
    <div v-if="info.has_update" class="tip">
      <span v-if="sizeText">约 {{ sizeText }} · </span>下载后双击安装包即可覆盖安装，已下载的影像与分析结果不会丢失
    </div>
  </div>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'

const info = ref({})
const visible = ref(false)
let hideTimer = null

const notes = computed(() => {
  let text = String(info.value.notes || '').replace(/\r/g, '').trim()
  text = text.replace(/^#{1,6}\s*/gm, '').replace(/\*\*(.+?)\*\*/g, '$1')
  return text.length > 260 ? text.slice(0, 260) + '…' : text
})
const sizeText = computed(() => {
  const b = Number(info.value.asset_size || 0)
  return b > 0 ? `${(b / 1048576).toFixed(0)} MB` : ''
})

function shouldShow(data) {
  if (!data || !data.has_update) return false
  const skip = localStorage.getItem('rspipe.skipVersion')
  if (skip && skip === data.latest) return false
  const later = sessionStorage.getItem('rspipe.laterVersion')
  if (later && later === data.latest) return false
  return true
}

async function check(force = false) {
  try {
    const r = await fetch('/api/update/check' + (force ? '?force=true' : ''))
    if (!r.ok) return
    const data = await r.json()
    if (!data || data.ok === false) return
    info.value = data
    if (data.has_update && shouldShow(data)) {
      visible.value = true
      return
    }
    if (force) {
      // 手动检查且已是最新：提示 3 秒
      visible.value = true
      clearTimeout(hideTimer)
      hideTimer = setTimeout(() => { visible.value = false }, 3000)
    }
  } catch (e) {
    /* 离线或接口不可用时静默 */
  }
}

function download() {
  const url = info.value.asset || info.value.page
  if (url) window.open(url, '_blank', 'noopener')
  visible.value = false
}

function later() {
  sessionStorage.setItem('rspipe.laterVersion', info.value.latest)
  visible.value = false
}

function skip() {
  localStorage.setItem('rspipe.skipVersion', info.value.latest)
  visible.value = false
}

function close() {
  visible.value = false
}

function onManualCheck() {
  check(true)
}

let timer = null
onMounted(() => {
  window.addEventListener('rspipe:check-update', onManualCheck)
  timer = setTimeout(() => check(false), 2500)
})
onBeforeUnmount(() => {
  window.removeEventListener('rspipe:check-update', onManualCheck)
  clearTimeout(timer)
  clearTimeout(hideTimer)
})
</script>

<style scoped>
.upd {
  position: fixed; right: 16px; bottom: 16px; z-index: 90;
  width: 340px; padding: 12px 14px;
  background: var(--panel); border: 1px solid var(--line); border-radius: 12px;
  box-shadow: 0 16px 44px rgba(16, 24, 40, .22);
}
.upd.ok { width: 260px; }
.hd { display: flex; align-items: baseline; gap: 8px; margin-bottom: 6px; }
.hd b { font-size: 13px; }
.cur { margin-left: auto; font-size: 11px; color: var(--muted); }
.notes {
  font-size: 11.5px; color: var(--ink); line-height: 1.6; white-space: pre-line;
  max-height: 96px; overflow: auto; margin-bottom: 8px;
  background: var(--track); border-radius: 8px; padding: 6px 8px;
}
.acts { display: flex; gap: 6px; }
.acts button { flex: 1; min-height: 28px; font-size: 11.5px; padding: 2px 8px; }
.acts .ghost { flex: 0 0 auto; color: var(--muted); }
.tip { margin-top: 8px; font-size: 10.5px; color: var(--muted); line-height: 1.5; }
</style>
