<template>
  <div class="wrap">
    <div class="hd">
      <span class="t">{{ title }}</span>
      <button class="x" @click="$emit('close')">✕</button>
    </div>
    <div class="stage" ref="stage">
      <div ref="elA" class="pane"></div>
      <div ref="elB" class="pane paneB" :style="{ clipPath: `inset(0 0 0 ${pos}%)` }"></div>
      <div class="divider" :style="{ left: pos + '%' }" @mousedown.prevent="startDrag">
        <span class="grip">⇔</span>
      </div>
      <span class="lbl left">{{ labelA }}</span>
      <span class="lbl right">{{ labelB }}</span>
    </div>
  </div>
</template>

<script setup>
import { onBeforeUnmount, onMounted, ref } from 'vue'
import maplibregl from 'maplibre-gl'

const props = defineProps({
  title: { type: String, default: '双屏对比' },
  labelA: { type: String, default: 'A · 前期' },
  labelB: { type: String, default: 'B · 后期' },
  tileA: { type: String, required: true },   // 含 {z}/{x}/{y} 的模板
  tileB: { type: String, required: true },
  bounds: { type: Array, default: null },    // [w,s,e,n]
})
defineEmits(['close'])

const stage = ref(null)
const elA = ref(null)
const elB = ref(null)
const pos = ref(50)
let mapA = null
let mapB = null
let syncing = false
let dragging = false

function styleFor(url) {
  return {
    version: 8,
    sources: {
      base: {
        type: 'raster', tileSize: 256,
        tiles: ['https://server.arcgisonline.com/arcgis/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}.png'],
      },
      result: { type: 'raster', tileSize: 256, minzoom: 5, maxzoom: 18, tiles: [url] },
    },
    layers: [
      { id: 'base', type: 'raster', source: 'base', paint: { 'raster-opacity': 0.55 } },
      { id: 'result', type: 'raster', source: 'result', paint: { 'raster-opacity': 1 } },
    ],
  }
}

onMounted(() => {
  const b = props.bounds || [100, 20, 130, 45]
  const opts = {
    style: null,
    center: [(b[0] + b[2]) / 2, (b[1] + b[3]) / 2],
    zoom: 10,
    attributionControl: false,
    interactive: true,
  }
  mapA = new maplibregl.Map({ container: elA.value, ...opts, style: styleFor(props.tileA) })
  mapB = new maplibregl.Map({ container: elB.value, ...opts, style: styleFor(props.tileB) })
  mapA.addControl(new maplibregl.NavigationControl({ showCompass: false }), 'top-right')

  const sync = (src, dst) => () => {
    if (syncing) return
    syncing = true
    dst.jumpTo({ center: src.getCenter(), zoom: src.getZoom(), bearing: src.getBearing(), pitch: src.getPitch() })
    syncing = false
  }
  mapA.on('move', sync(mapA, mapB))
  mapB.on('move', sync(mapB, mapA))

  const fit = () => {
    const bb = [[b[0], b[1]], [b[2], b[3]]]
    mapA.fitBounds(bb, { padding: 20, maxZoom: 16 })
    mapB.fitBounds(bb, { padding: 20, maxZoom: 16 })
  }
  mapA.on('load', fit)
  mapB.on('load', () => mapB.jumpTo({ center: mapA.getCenter(), zoom: mapA.getZoom() }))

  window.addEventListener('mousemove', onDrag)
  window.addEventListener('mouseup', endDrag)
})

onBeforeUnmount(() => {
  window.removeEventListener('mousemove', onDrag)
  window.removeEventListener('mouseup', endDrag)
  if (mapA) mapA.remove()
  if (mapB) mapB.remove()
})

function startDrag() {
  dragging = true
}
function onDrag(e) {
  if (!dragging || !stage.value) return
  const r = stage.value.getBoundingClientRect()
  pos.value = Math.min(100, Math.max(0, ((e.clientX - r.left) / r.width) * 100))
}
function endDrag() {
  dragging = false
}
</script>

<style scoped>
.wrap { position: fixed; inset: 0; z-index: 120; background: rgba(10, 14, 22, .72); display: flex; flex-direction: column; }
.hd { flex: none; display: flex; align-items: center; justify-content: space-between; padding: 10px 16px; color: #e8eef7; }
.hd .t { font-size: 13.5px; font-weight: 600; }
.x { background: transparent; color: #e8eef7; border-color: rgba(255,255,255,.3); }
.stage { position: relative; flex: 1; margin: 0 16px 16px; border-radius: 12px; overflow: hidden; background: #0b1220; }
.pane { position: absolute; inset: 0; }
.paneB { z-index: 2; }
.divider {
  position: absolute; top: 0; bottom: 0; width: 2px; z-index: 5;
  background: #ffe600; transform: translateX(-1px); cursor: col-resize;
}
.grip {
  position: absolute; top: 50%; left: 50%; transform: translate(-50%, -50%);
  width: 30px; height: 30px; border-radius: 50%;
  background: #ffe600; color: #1c2534; font-size: 14px; font-weight: 700;
  display: flex; align-items: center; justify-content: center;
  box-shadow: 0 2px 10px rgba(0,0,0,.35);
}
.lbl {
  position: absolute; z-index: 4; top: 10px; padding: 3px 10px;
  background: rgba(255,255,255,.94); border-radius: 8px; font-size: 11.5px; color: #1c2534;
}
.lbl.left { left: 10px; }
.lbl.right { right: 10px; }
</style>
