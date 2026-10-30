<template>
  <teleport to="body">
    <!-- 确认框 -->
    <div v-if="confirmState" class="mask" @click.self="cancel">
      <div class="box confirm">
        <div class="title">{{ confirmState.title }}</div>
        <div class="msg">{{ confirmState.message }}</div>
        <div class="actions">
          <button @click="cancel">{{ confirmState.cancelText || '取消' }}</button>
          <button class="primary" @click="ok">{{ confirmState.okText || '确定' }}</button>
        </div>
      </div>
    </div>

    <!-- 轻提示 -->
    <div v-if="toastState" class="toast" :class="toastState.kind">{{ toastState.text }}</div>
  </teleport>
</template>

<script setup>
import { onBeforeUnmount, ref } from 'vue'

const confirmState = ref(null)
const toastState = ref(null)
let toastTimer = null
let resolver = null

function confirm(opts) {
  confirmState.value = {
    title: opts.title || '确认',
    message: opts.message || '',
    okText: opts.okText,
    cancelText: opts.cancelText,
  }
  return new Promise((resolve) => { resolver = resolve })
}
function ok() {
  confirmState.value = null
  resolver?.(true)
  resolver = null
}
function cancel() {
  confirmState.value = null
  resolver?.(false)
  resolver = null
}

function toast(text, kind = 'info', ms = 2600) {
  toastState.value = { text, kind }
  clearTimeout(toastTimer)
  toastTimer = setTimeout(() => (toastState.value = null), ms)
}

onBeforeUnmount(() => clearTimeout(toastTimer))

defineExpose({ confirm, toast })
</script>

<style scoped>
.mask {
  position: fixed; inset: 0; z-index: 200;
  background: rgba(8, 12, 20, .55);
  display: flex; align-items: center; justify-content: center;
}
.box.confirm {
  width: 380px; max-width: calc(100vw - 48px);
  background: var(--panel); border: 1px solid var(--line);
  border-radius: 12px; padding: 18px 20px 14px;
  box-shadow: 0 20px 60px rgba(16, 24, 40, .3);
}
.title { font-size: 14px; font-weight: 700; margin-bottom: 8px; }
.msg { font-size: 12.5px; color: var(--ink); line-height: 1.8; word-break: break-word; }
.actions { display: flex; justify-content: flex-end; gap: 8px; margin-top: 16px; }

.toast {
  position: fixed; z-index: 210;
  left: 50%; top: 50%; transform: translate(-50%, -50%);
  max-width: 70vw; padding: 12px 22px;
  background: rgba(18, 22, 29, .92); color: #fff;
  border-radius: 10px; font-size: 13px; line-height: 1.6;
  text-align: center; box-shadow: 0 12px 40px rgba(0, 0, 0, .28);
  pointer-events: none;
}
.toast.warn { background: rgba(120, 53, 15, .95); }
.toast.error { background: rgba(127, 29, 29, .95); }
</style>
