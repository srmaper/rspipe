<template>
  <div v-if="visible" class="upd" :class="{ ok: !info.has_update }">
    <div class="hd">
      <b v-if="info.has_update">发现新版本 {{ info.latest }}</b>
      <b v-else>已是最新版本</b>
      <span class="cur">当前 {{ info.current || '—' }}</span>
    </div>

    <template v-if="info.has_update">
      <!-- 下载进度 -->
      <div v-if="phase === 'downloading'" class="blk">
        <div class="bar"><i :style="{ width: pct + '%' }"></i></div>
        <div class="dl-t">
          正在下载 {{ pct }}%
          <span v-if="sizeLine"> · {{ sizeLine }}</span>
          <span v-if="speedText"> · {{ speedText }}</span>
          <span v-if="etaText"> · 剩余约 {{ etaText }}</span>
        </div>
        <div v-if="st.retries" class="dl-t warn">网络不稳，已重试 {{ st.retries }} 次（断点续传，不会从头下）</div>
      </div>

      <!-- 安装进度（安装器不回报，按时间估算 + 服务恢复检测） -->
      <div v-else-if="phase === 'installing'" class="blk">
        <div class="bar"><i :style="{ width: instPct + '%' }"></i></div>
        <div class="dl-t">{{ instStage }}</div>
        <div class="dl-t muted">安装期间服务会短暂中断，完成后页面自动刷新</div>
      </div>

      <!-- 已就绪：等用户确认安装 -->
      <div v-else-if="phase === 'ready'" class="dl-t ok">
        安装包已下载完成（{{ sizeText }}），点下方按钮覆盖安装并重启
      </div>

      <!-- 出错 -->
      <div v-else-if="phase === 'error'" class="dl-t err">{{ st.error || '操作失败' }}</div>

      <div v-if="notes && phase !== 'installing'" class="notes">{{ notes }}</div>

      <div class="acts">
        <template v-if="phase === 'idle'">
          <button class="primary" @click="startDownload">立即更新</button>
          <button @click="later">稍后</button>
          <button class="ghost" @click="skip">跳过此版本</button>
        </template>
        <template v-else-if="phase === 'downloading'">
          <button class="ghost" @click="later">后台继续，稍后安装</button>
        </template>
        <template v-else-if="phase === 'ready' && info.can_auto">
          <button class="primary" @click="installNow">立即安装并重启</button>
          <button @click="later">稍后</button>
        </template>
        <template v-else-if="phase === 'ready'">
          <button class="primary" @click="openPage">下载安装包</button>
          <button @click="later">稍后</button>
        </template>
        <template v-else-if="phase === 'error'">
          <button class="primary" @click="retry">{{ canReuse ? '重试安装' : '重新下载' }}</button>
          <button class="ghost" @click="openPage">手动下载</button>
          <button class="ghost" @click="later">稍后</button>
        </template>
        <template v-else-if="phase === 'installing'">
          <button class="ghost" disabled>安装中…</button>
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
const st = ref({ status: 'idle', stage: '', progress: 0, bytes: 0, total: 0, speed: 0, error: '' })
const visible = ref(false)
const instPct = ref(0)
const instStage = ref('正在退出程序…')

let hideTimer = null
let pollTimer = null
let instTimer = null
let waitTimer = null

const phase = computed(() => {
  const s = st.value.status
  if (s === 'downloading' || s === 'installing' || s === 'error') return s
  if (s === 'ready' || (s === 'idle' && info.value.downloaded)) return 'ready'
  return 'idle'
})
const canReuse = computed(() => phase.value === 'error' && st.value.status === 'error' && !!st.value.path)
const pct = computed(() => Math.round((Number(st.value.progress) || 0) * 100))
const sizeText = computed(() => {
  const b = Number(st.value.total || info.value.asset_size || 0)
  return b > 0 ? `${(b / 1048576).toFixed(0)} MB` : '—'
})
const sizeLine = computed(() => {
  const done = Number(st.value.bytes || 0)
  const total = Number(st.value.total || 0)
  if (!total) return ''
  return `${(done / 1048576).toFixed(1)} / ${(total / 1048576).toFixed(1)} MB`
})
const speedText = computed(() => {
  const v = Number(st.value.speed || 0)
  if (v <= 0) return ''
  return v > 1048576 ? `${(v / 1048576).toFixed(1)} MB/s` : `${Math.round(v / 1024)} KB/s`
})
const etaText = computed(() => {
  const v = Number(st.value.speed || 0)
  const total = Number(st.value.total || 0)
  const done = Number(st.value.bytes || 0)
  if (v <= 0 || total <= done) return ''
  const s = Math.round((total - done) / v)
  return s > 90 ? `${Math.round(s / 60)} 分钟` : `${s} 秒`
})
const notes = computed(() => {
  let text = String(info.value.notes || '').replace(/\r/g, '').trim()
  text = text.replace(/^#{1,6}\s*/gm, '').replace(/\*\*(.+?)\*\*/g, '$1')
  return text.length > 240 ? text.slice(0, 240) + '…' : text
})
const tipText = computed(() => {
  if (!info.value.can_auto) return '双击安装包覆盖安装即可，已下载的影像与分析结果不会丢失'
  if (phase.value === 'idle') return '更新不会自动开始，确认后才会下载；数据目录不受影响'
  if (phase.value === 'downloading') return '可以关闭提示后台下载，下完再安装'
  if (phase.value === 'ready') return '安装约 10–30 秒，完成后页面自动刷新；已下载影像与分析结果不会丢'
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
      await syncStatus()
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

async function syncStatus() {
  try {
    const r = await fetch('/api/update/status', { cache: 'no-store' })
    const s = await r.json()
    st.value = s
    if (s.status === 'downloading') pollStatus()
  } catch (e) {
    /* ignore */
  }
}

async function startDownload() {
  if (st.value.status === 'downloading') return
  st.value = { ...st.value, status: 'downloading', progress: 0, bytes: 0, speed: 0, error: '' }
  try {
    const r = await fetch('/api/update/download', { method: 'POST' })
    const d = await r.json()
    if (!d.ok) {
      st.value = { ...st.value, status: 'error', error: d.error || '下载失败' }
      return
    }
    st.value = d
    if (d.status === 'ready') return
    pollStatus()
  } catch (e) {
    st.value = { ...st.value, status: 'error', error: String(e.message || e) }
  }
}

function pollStatus() {
  clearInterval(pollTimer)
  pollTimer = setInterval(async () => {
    try {
      const r = await fetch('/api/update/status', { cache: 'no-store' })
      st.value = await r.json()
      if (st.value.status === 'ready' || st.value.status === 'error') clearInterval(pollTimer)
    } catch (e) {
      /* 服务重启中 */
    }
  }, 700)
}

async function installNow() {
  clearInterval(instTimer)
  instPct.value = 3
  instStage.value = '正在退出程序…'
  st.value = { ...st.value, status: 'installing' }
  const t0 = Date.now()
  instTimer = setInterval(() => {
    const el = (Date.now() - t0) / 1000
    instPct.value = Math.min(90, 3 + el * 3.2)
    if (el < 3) instStage.value = '正在退出程序…'
    else if (el < 20) instStage.value = `正在安装…（约 ${Math.max(1, Math.round(20 - el))} 秒）`
    else instStage.value = '仍在安装，请稍候…'
  }, 300)
  try {
    const r = await fetch('/api/update/install', { method: 'POST' })
    const d = await r.json()
    if (!d.ok) {
      clearInterval(instTimer)
      st.value = { ...st.value, status: 'error', error: d.error || '安装失败' }
      return
    }
  } catch (e) {
    /* 服务可能已退出 */
  }
  waitForRestart()
}

function waitForRestart() {
  const oldVersion = info.value.current
  let tries = 0
  clearInterval(waitTimer)
  waitTimer = setInterval(async () => {
    tries += 1
    if (tries > 200) {
      clearInterval(waitTimer)
      clearInterval(instTimer)
      st.value = { ...st.value, status: 'error', error: '安装后服务未恢复，请手动打开 rspipe' }
      return
    }
    try {
      const r = await fetch('/api/version', { cache: 'no-store' })
      const d = await r.json()
      if (d.version && d.version !== oldVersion) {
        clearInterval(waitTimer)
        clearInterval(instTimer)
        instPct.value = 100
        instStage.value = '安装完成，正在刷新…'
        location.replace('/?v=' + Date.now())
      }
    } catch (e) {
      /* 安装中，服务未起来 */
    }
  }, 1500)
}

async function retry() {
  if (canReuse.value) {
    await installNow()
    return
  }
  await startDownload()
}

function openPage() {
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

onMounted(() => {
  window.addEventListener('rspipe:check-update', onManualCheck)
  hideTimer = setTimeout(() => check(false), 2500)
})
onBeforeUnmount(() => {
  window.removeEventListener('rspipe:check-update', onManualCheck)
  clearTimeout(hideTimer)
  clearInterval(pollTimer)
  clearInterval(instTimer)
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
.blk { margin-bottom: 8px; }
.bar { height: 5px; background: var(--track); border-radius: 4px; overflow: hidden; }
.bar i { display: block; height: 100%; background: var(--accent); transition: width .3s; }
.dl-t { font-size: 11px; color: var(--muted); margin-top: 5px; }
.dl-t.ok { color: #15803d; }
.dl-t.err { color: var(--bad); word-break: break-all; }
.dl-t.warn { color: #b45309; }
.dl-t.muted { opacity: .85; }
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
