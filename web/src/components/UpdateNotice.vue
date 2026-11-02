<template>
  <div v-if="visible" class="upd" :class="{ ok: !info.has_update }">
    <div class="hd">
      <b v-if="info.has_update">发现新版本 {{ info.latest }}</b>
      <b v-else>已是最新版本</b>
      <span class="cur">当前 {{ info.current || '—' }}</span>
    </div>

    <template v-if="info.has_update">
      <div v-if="st.status === 'downloading'" class="dl">
        <div class="bar"><i :style="{ width: pct + '%' }"></i></div>
        <div class="dl-t">正在后台下载更新 {{ pct }}%<span v-if="sizeText"> · {{ sizeText }}</span></div>
      </div>
      <div v-else-if="st.status === 'ready'" class="dl-t ok">
        {{ countdown > 0 ? countdown + ' 秒后自动重启安装…' : '安装包已下载完成' }}
      </div>
      <div v-else-if="st.status === 'installing'" class="dl-t ok">正在安装，完成后页面自动恢复…</div>
      <div v-else-if="st.status === 'error'" class="dl-t err">下载失败：{{ st.error }}</div>

      <div v-if="notes" class="notes">{{ notes }}</div>

      <div class="acts">
        <template v-if="st.status === 'ready' && info.can_auto">
          <button class="primary" @click="installNow">立即重启安装</button>
          <button @click="later">推迟</button>
        </template>
        <template v-else-if="st.status === 'ready'">
          <button class="primary" @click="openPage">下载安装包</button>
          <button @click="later">稍后</button>
        </template>
        <template v-else-if="st.status === 'downloading' || st.status === 'installing'">
          <button class="ghost" @click="later">后台继续，稍后安装</button>
        </template>
        <template v-else>
          <button class="primary" @click="startDownload">重新下载</button>
          <button @click="later">稍后</button>
        </template>
      </div>
      <div class="tip">{{ tipText }}</div>
    </template>

    <div v-else class="acts"><button @click="close">知道了</button></div>
  </div>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'

const info = ref({})
const st = ref({ status: 'idle', progress: 0, error: '' })
const countdown = ref(0)
const visible = ref(false)

let hideTimer = null
let pollTimer = null
let cdTimer = null
let waitTimer = null

const notes = computed(() => {
  let text = String(info.value.notes || '').replace(/\r/g, '').trim()
  text = text.replace(/^#{1,6}\s*/gm, '').replace(/\*\*(.+?)\*\*/g, '$1')
  return text.length > 260 ? text.slice(0, 260) + '…' : text
})
const sizeText = computed(() => {
  const b = Number(info.value.asset_size || 0)
  return b > 0 ? `${(b / 1048576).toFixed(0)} MB` : ''
})
const pct = computed(() => Math.round((Number(st.value.progress) || 0) * 100))
const tipText = computed(() => {
  if (!info.value.can_auto) return '双击安装包覆盖安装即可，已下载的影像与分析结果不会丢失'
  if (st.value.status === 'downloading') return '下载完成后会自动重启安装，也可以推迟到下次启动'
  if (st.value.status === 'ready') return '安装约 10 秒，完成后页面自动刷新；数据目录不受影响'
  return '升级后浏览器会自动加载新界面，无需手动清缓存'
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
      if (data.can_auto) startDownload()
      return
    }
    if (force) {
      visible.value = true
      clearTimeout(hideTimer)
      hideTimer = setTimeout(() => { visible.value = false }, 3000)
    }
  } catch (e) {
    /* 离线或接口不可用时静默 */
  }
}

async function startDownload() {
  st.value = { status: 'downloading', progress: 0, error: '' }
  visible.value = true
  try {
    const r = await fetch('/api/update/download', { method: 'POST' })
    const d = await r.json()
    if (!d.ok) {
      st.value = { status: 'error', progress: 0, error: d.error || '下载失败' }
      return
    }
    st.value = d
    pollStatus()
  } catch (e) {
    st.value = { status: 'error', progress: 0, error: String(e.message || e) }
  }
}

function pollStatus() {
  clearInterval(pollTimer)
  pollTimer = setInterval(async () => {
    try {
      const r = await fetch('/api/update/status', { cache: 'no-store' })
      st.value = await r.json()
      if (st.value.status === 'ready') {
        clearInterval(pollTimer)
        startCountdown()
      } else if (st.value.status === 'error') {
        clearInterval(pollTimer)
      }
    } catch (e) {
      /* 服务重启中，稍后继续 */
    }
  }, 900)
}

function startCountdown() {
  if (!info.value.can_auto) return
  clearInterval(cdTimer)
  countdown.value = 5
  cdTimer = setInterval(() => {
    countdown.value -= 1
    if (countdown.value <= 0) {
      clearInterval(cdTimer)
      installNow()
    }
  }, 1000)
}

async function installNow() {
  clearInterval(cdTimer)
  countdown.value = 0
  st.value = { ...st.value, status: 'installing' }
  try {
    const r = await fetch('/api/update/install', { method: 'POST' })
    const d = await r.json()
    if (!d.ok) {
      st.value = { status: 'error', progress: 1, error: d.error || '安装失败' }
      return
    }
  } catch (e) {
    /* 服务可能已退出，继续等重启 */
  }
  waitForRestart()
}

function waitForRestart() {
  const oldVersion = info.value.current
  let tries = 0
  clearInterval(waitTimer)
  waitTimer = setInterval(async () => {
    tries += 1
    if (tries > 180) { clearInterval(waitTimer); return }
    try {
      const r = await fetch('/api/version', { cache: 'no-store' })
      const d = await r.json()
      if (d.version && d.version !== oldVersion) {
        clearInterval(waitTimer)
        location.replace('/?v=' + Date.now())
      }
    } catch (e) {
      /* 安装中 */
    }
  }, 1500)
}

function openPage() {
  const url = info.value.asset || info.value.page
  if (url) window.open(url, '_blank', 'noopener')
  visible.value = false
}

function later() {
  sessionStorage.setItem('rspipe.laterVersion', info.value.latest)
  clearInterval(cdTimer)
  countdown.value = 0
  visible.value = false
}

function close() {
  visible.value = false
}

function onManualCheck() {
  check(true)
}

onMounted(() => {
  window.addEventListener('rspipe:check-update', onManualCheck)
  hideTimer = setTimeout(() => check(false), 2500)
})
onBeforeUnmount(() => {
  window.removeEventListener('rspipe:check-update', onManualCheck)
  clearTimeout(hideTimer)
  clearInterval(pollTimer)
  clearInterval(cdTimer)
  clearInterval(waitTimer)
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
.dl { margin-bottom: 8px; }
.bar { height: 5px; background: var(--track); border-radius: 4px; overflow: hidden; }
.bar i { display: block; height: 100%; background: var(--accent); transition: width .3s; }
.dl-t { font-size: 11px; color: var(--muted); margin-top: 5px; }
.dl-t.ok { color: #15803d; }
.dl-t.err { color: var(--bad); word-break: break-all; }
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
