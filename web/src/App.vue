<template>
  <HomePage v-if="view === 'home'" @go="go" />
  <DownloadPage v-else-if="view === 'download'" @home="go('home')" />
  <AnalysisPage v-else @home="go('home')" />
  <UpdateNotice />
</template>

<script setup>
import { onBeforeUnmount, onMounted, ref } from 'vue'
import HomePage from './components/HomePage.vue'
import DownloadPage from './components/DownloadPage.vue'
import AnalysisPage from './components/AnalysisPage.vue'
import UpdateNotice from './components/UpdateNotice.vue'

const VIEWS = ['download', 'analysis']
const view = ref('home')

function syncFromHash() {
  const h = (location.hash || '').replace(/^#\/?/, '')
  view.value = VIEWS.includes(h) ? h : 'home'
}
function go(v) {
  location.hash = '#/' + v
  view.value = v
}
onMounted(() => {
  syncFromHash()
  window.addEventListener('hashchange', syncFromHash)
})
onBeforeUnmount(() => window.removeEventListener('hashchange', syncFromHash))
</script>
