<template>
  <div class="mc">
    <div ref="el" class="map"></div>
    <div class="basebar">
      <button :class="{ primary: basemap === 'imagery' }" title="卫星影像" @click="setBasemap2('imagery')">🛰</button>
      <button :class="{ primary: basemap === 'vector' }" title="矢量路网" @click="setBasemap2('vector')">🗺</button>
    </div>
    <div class="coord">{{ coordText }}</div>
  </div>
</template>

<script setup>
import { ref, onMounted, onBeforeUnmount, watch } from 'vue'
import maplibregl from 'maplibre-gl'
import cartoLayers from '../assets/carto-positron.json'

const TDT = (key) =>
  `https://t6.tianditu.gov.cn/${key}_w/wmts?SERVICE=WMTS&REQUEST=GetTile&VERSION=1.0.0`
  + `&LAYER=${key}&STYLE=default&TILEMATRIXSET=w&FORMAT=tiles`
  + `&TILEMATRIX={z}&TILEROW={y}&TILECOL={x}&tk=85c9d12d5d691d168ba5cb6ecaa749eb`

const props = defineProps({
  selectedFootprint: Object,   // 选中景的真实 footprint（蓝色高亮，切换即替换）
  previewScene: Object,
  tileTemplate: { type: String, default: '' },   // 自定义瓦片 URL 模板（分析页用）
})
const emit = defineEmits(['bbox', 'coord', 'draw-state'])
const el = ref(null)
const basemap = ref('imagery')
const coordText = ref('–')
let map = null

// ── 圈选状态机（setup 作用域：defineExpose 直接引用 → rect/poly 一套） ──
let drawMode = null        // 'rect' | 'poly' | null
let draft = []             // poly 当前环
let regAnchor = null       // rect 拖拽锚点

function setDrawMode(mode) {
  if (!map) return
  if (drawMode === mode) mode = null
  drawMode = mode
  draft = []; regAnchor = null
  map.getCanvas().style.cursor = mode ? 'crosshair' : ''
  map.dragPan[!drawMode ? 'enable' : 'disable']()
  emit('draw-state', mode || null)
}

function rectOf(a, b) {
  const west = Math.min(a[0], b[0]); const east = Math.max(a[0], b[0])
  const south = Math.min(a[1], b[1]); const north = Math.max(a[1], b[1])
  if (east - west < 1e-10 || north - south < 1e-10) return null
  return [[west, north], [east, north], [east, south], [west, south]]
}

function acceptRange(ring) {
  if (!map) return
  const xs = ring.map(p => p[0]); const ys = ring.map(p => p[1])
  const bbox = [Math.min(...xs), Math.min(...ys), Math.max(...xs), Math.max(...ys)]
  setAoiPolygon({ type: 'Polygon', coordinates: [ring.concat([ring[0]])] })
  drawMode = null; draft = []; regAnchor = null
  map.getCanvas().style.cursor = ''
  map.dragPan.enable()
  emit('draw-state', false)
  emit('bbox', bbox, ring)
}

onMounted(() => {
  map = new maplibregl.Map({
    container: el.value,
    style: {
      version: 8,
      sources: {
        imagery: {
          type: 'raster',
          tiles: ['https://server.arcgisonline.com/arcgis/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}.png'],
          tileSize: 256,
          attribution: '© Esri World_Imagery',
        },
        anno: { type: 'raster', tiles: [TDT('cia')], tileSize: 256, attribution: '© 天地图 cia' },
        label: { type: 'raster', tiles: [TDT('cva')], tileSize: 256, attribution: '© 天地图 cva' },
        vbg: {
          type: 'vector',
          tiles: ['https://tiles.basemaps.cartocdn.com/vectortiles/carto.streets/v1/{z}/{x}/{y}.mvt'],
          maxzoom: 14,
        },
      },
      layers: [
        { id: 'bg', type: 'background', paint: { 'background-color': '#eef1f7' } },
        { id: 'imagery', type: 'raster', source: 'imagery', layout: { visibility: 'none' } },
        { id: 'anno', type: 'raster', source: 'anno', layout: { visibility: 'none' }, paint: { 'raster-opacity': 0.75 } },
        { id: 'label', type: 'raster', source: 'label', layout: { visibility: 'none' }, paint: { 'raster-opacity': 0.75 } },
      ],
      sprites: undefined,
      glyphs: 'https://tiles.basemaps.cartocdn.com/fonts/{fontstack}/{range}.pbf',
      id: 'rsv1',
    },
    attributionControl: false,
    center: [104.0, 35.5],   // 中国全境
    zoom: 3.5,
  })
  map.addControl(new maplibregl.NavigationControl(), 'top-right')
  map.addControl(new maplibregl.ScaleControl({ maxWidth: 120 }), 'bottom-left')
  // 注入 carto positron 矢量层必须在 style ready 之后
  map.on('load', () => {
    injectCarto(map)
    setBasemap('imagery')  // 默认影像模式（矢量层已就位可即时切换）
    if (props.selectedFootprint) setSelectedFootprint(props.selectedFootprint)
  })
  // ── 圈选事件绑定（对齐 tiles.html）：状态机在 setup 作用域，这里只挂 handler ──
  map.on('mousedown', e => {
    if (drawMode === 'rect') regAnchor = [e.lngLat.lng, e.lngLat.lat]
  })
  map.on('mousemove', e => {
    coordText.value = `${e.lngLat.lng.toFixed(4)}°, ${e.lngLat.lat.toFixed(4)}°`
    emit('coord', e.lngLat)
    if (drawMode === 'rect' && regAnchor) {
      const r = rectOf(regAnchor, [e.lngLat.lng, e.lngLat.lat])
      if (r) setAoiPolygon({ type: 'Polygon', coordinates: [r.concat([r[0]])] })
    } else if (drawMode === 'poly' && draft.length) {
      const prev = draft.concat([[e.lngLat.lng, e.lngLat.lat]])
      setAoiPolygon({ type: 'Polygon', coordinates: [prev.concat([prev[0]])] })
    }
  })
  map.on('mouseup', e => {
    if (drawMode !== 'rect' || !regAnchor) return
    const r = rectOf(regAnchor, [e.lngLat.lng, e.lngLat.lat])
    regAnchor = null
    if (!r) return // 原地点击不算拖拽
    acceptRange(r)
  })
  map.on('click', e => {
    if (drawMode === 'poly') draft.push([e.lngLat.lng, e.lngLat.lat])
  })
  map.on('dblclick', e => {
    if (drawMode !== 'poly') return
    e.preventDefault()
    draft.pop()
    if (draft.length >= 3) acceptRange(draft.slice())
    else { draft = []; setDrawMode(null); setAoiPolygon(null) }
  })
  map.on('contextmenu', e => {
    if (drawMode !== 'poly') return
    e.preventDefault()
    if (draft.length) draft.pop()
  })
  window.addEventListener('keydown', ev => {
    if (!drawMode) return
    if (ev.key === 'Escape') { setDrawMode(null); setAoiPolygon(null); draft = [] }
  })
})

onBeforeUnmount(() => map && map.remove())


function setAoiVisible(visible) {
  if (!map) return
  try {
    if (map.getLayer('aoi-line')) {
      map.setLayoutProperty('aoi-line', 'visibility', visible ? 'visible' : 'none')
      if (visible) map.moveLayer('aoi-line')   // AOI 时刻在最顶
    }
  } catch (e) { /* 图层尚未创建 */ }
}

function fitBbox(b) {
  if (!map || !b) return
  map.fitBounds([[b[0], b[1]], [b[2], b[3]]], { padding: 40, maxZoom: 18 })
}
function setAoiPolygon(geom) {
  if (!map) return
  const data = geom ? { type: 'Feature', geometry: geom, properties: {} } : { type: 'FeatureCollection', features: [] }
  if (!map.getSource('aoi')) {
    map.addSource('aoi', { type: 'geojson', data })
    map.addLayer({
      id: 'aoi-line', type: 'line', source: 'aoi',
        paint: { 'line-color': '#ffe600', 'line-width': 4, 'line-opacity': 1 },
    })
  } else {
    map.getSource('aoi').setData(data)
  }
}
// ── 选中景的真实 footprint（蓝色）：切换选择时先销毁旧图层，避免叠加 ──
function clearSelectedFootprint() {
  if (!map) return
  try { if (map.getLayer('selfp-fill')) map.removeLayer('selfp-fill') } catch (e) {}
  try { if (map.getLayer('selfp-line')) map.removeLayer('selfp-line') } catch (e) {}
  try { if (map.getSource('selfp')) map.removeSource('selfp') } catch (e) {}
}

function setSelectedFootprint(geom) {
  if (!map) return
  clearSelectedFootprint()
  if (!geom || !geom.type) return
  map.addSource('selfp', {
    type: 'geojson',
    data: { type: 'Feature', geometry: geom, properties: {} },
  })
  map.addLayer({
    id: 'selfp-fill', type: 'fill', source: 'selfp',
    paint: { 'fill-color': '#2563eb', 'fill-opacity': 0.12 },
  })
  map.addLayer({
    id: 'selfp-line', type: 'line', source: 'selfp',
    paint: { 'line-color': '#2563eb', 'line-width': 3, 'line-opacity': 1 },
  })
  restackBusinessLayers(map)
}

let previewId = null
function setPreviewTiles(sceneId, channels, provider, collection, bounds) {
  if (!map) return
  clearPreview()
  previewId = sceneId
  const q = new URLSearchParams({ channels })
  if (provider) q.set('provider', provider)
  if (collection) q.set('collection', collection)
  const url = props.tileTemplate || `/api/tiles/${sceneId}/{z}/{x}/{y}.png?${q.toString()}`
  const src = {
    type: 'raster',
    tiles: [url],
    tileSize: 256,
    minzoom: 5,
    maxzoom: 18,
  }
  if (bounds && bounds.length === 4) src.bounds = bounds  // 限定请求范围=景范围
  map.addSource('preview', src)
  map.addLayer({ id: 'preview', type: 'raster', source: 'preview', paint: { 'raster-opacity': 1 } })
  restackBusinessLayers(map)   // 下载 AOI 始终压在最顶
  // 瓦片异步装载后个别实现会按加载顺序重建绘制队列，idle 时再归层一次防漂移
  map.once('idle', () => restackBusinessLayers(map))
}
function clearPreview() {
  if (!map) return
  if (map.getLayer('preview')) map.removeLayer('preview')
  if (map.getSource('preview')) map.removeSource('preview')
  previewId = null
}

watch(() => props.selectedFootprint, g => setSelectedFootprint(g))
watch(
  () => props.previewScene,
  s => {
    if (!s) { clearPreview(); return }
    if (s.ready) setPreviewTiles(s.scene_id, 'rgb', s.provider, s.collection, s.bounds)
  },
)

const VB_IDS = []

function injectCarto(map) {
  for (const ly of cartoLayers.layers) {
    if (ly.type !== 'fill' && ly.type !== 'line') continue
    if (ly['source-layer'] === 'boundary') continue
    const d = JSON.parse(JSON.stringify(ly))
    d.id = 'vbg-' + d.id
    d.source = 'vbg'
    d.layout = { ...(d.layout || {}), visibility: 'none' }
    d.paint = { ...(d.paint || {}) }
    if (d.type === 'fill') d.paint['fill-opacity'] = 0.7
    if (d.type === 'line') d.paint['line-opacity'] = 0.7
    try { map.addLayer(d); VB_IDS.push(d.id) } catch (e) { console.warn('vbg skip', d.id, e) }
  }
  // 注记层必须压在矢量路网之上
  try { map.moveLayer('label') } catch (e) {}
  // AOI/足迹/预览等业务图层重新回到最顶
  restackBusinessLayers(map)
}

function restackBusinessLayers(m) {
  const top = ['preview', 'selfp-fill', 'selfp-line', 'aoi-line']
  for (const id of top) {
    try { if (m.getLayer(id)) m.moveLayer(id) } catch (e) {}
  }
}

function setBasemap2(kind) {
  basemap.value = kind
  setBasemap(kind)
}

function setBasemap(kind) {
  if (!map) return
  // 注意：不要用 isStyleLoaded() 当门槛——load 事件后它因瓦片仍在流会回 false，
  // 导致首次 setBasemap 空转（tiles.html 注释里踩过的同一坑）。setLayoutProperty 自带抛错兜底。
  const ids = ['imagery', 'anno', 'label', ...VB_IDS]
  ids.forEach(id => { try { map.setLayoutProperty(id, 'visibility', 'none') } catch (e) {} })
  if (kind === 'imagery') {
    map.setLayoutProperty('imagery', 'visibility', 'visible')  // ArcGIS 影像
    map.setLayoutProperty('anno', 'visibility', 'visible')     // 天地图影像注记
    restackBusinessLayers(map)
  } else {
    // vector：carto 矢量路网 + 天地图矢量注记（注记层保持在最顶）
    VB_IDS.forEach(id => map.setLayoutProperty(id, 'visibility', 'visible'))
    map.setLayoutProperty('label', 'visibility', 'visible')
    restackBusinessLayers(map)
  }
}

function viewportBbox() {
  if (!map) return null
  const b = map.getBounds()
  return [b.getWest(), b.getSouth(), b.getEast(), b.getNorth()]
}

defineExpose({
  fitBbox, setAoiPolygon, setAoiVisible, setPreviewTiles, clearPreview, setBasemap, setDrawMode, viewportBbox,
})
</script>

<style scoped>
.mc { position: absolute; inset: 0; }
.map { position: absolute; inset: 0; }
.basebar {
  position: absolute; z-index: 31; right: 12px; bottom: 34px;
  display: flex; flex-direction: column; gap: 4px;
  background: rgba(255,255,255,.96); border: 1px solid var(--line);
  border-radius: 10px; padding: 4px; box-shadow: 0 4px 18px rgba(20,30,45,.14);
}
.basebar button { width: 32px; height: 32px; min-height: 32px; padding: 0; font-size: 16px; }
.coord {
  position: absolute; z-index: 30; left: 50%; bottom: 12px; transform: translateX(-50%);
  background: rgba(255,255,255,.94); border: 1px solid var(--line); border-radius: 8px;
  padding: 4px 12px; font-size: 11px; font-variant-numeric: tabular-nums;
  box-shadow: 0 2px 10px rgba(20,30,45,.10); pointer-events: none;
}

/* 比例尺：白色圆角卡片 + 上方文字 + 下方带端帽的线段 */
:deep(.maplibregl-ctrl-scale) {
  background-color: rgba(255, 255, 255, .96) !important;
  border: 1px solid var(--line) !important;
  border-radius: 8px !important;
  padding: 6px 14px 18px !important;
  color: #1c2534 !important;
  font-size: 11.5px !important;
  font-weight: 600;
  line-height: 1.25;
  text-align: center;
  min-width: 76px;
  box-shadow: 0 2px 10px rgba(20, 30, 45, .12);
  background-image:
    linear-gradient(#1c2534, #1c2534),
    linear-gradient(#1c2534, #1c2534),
    linear-gradient(#1c2534, #1c2534);
  background-repeat: no-repeat;
  background-size: calc(100% - 28px) 2px, 2px 9px, 2px 9px;
  background-position: left 14px bottom 7px, left 14px bottom 7px, right 14px bottom 7px;
  /* 整体缩小到 75%（文字/线段/卡片同步缩放） */
  transform: scale(.75);
  transform-origin: left bottom;
  margin: 0 0 -2px -1px;
}
:deep(.maplibregl-ctrl-bottom-left) { margin-bottom: 6px; }
</style>
