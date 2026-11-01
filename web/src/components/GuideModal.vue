<template>
  <teleport to="body">
    <div class="mask" @click.self="$emit('close')">
      <div class="dlg">
        <div class="hd">
          <h3>分析指南 · 分析工具说明</h3>
          <button class="x" @click="$emit('close')">✕</button>
        </div>

        <!-- 通用说明：两期变化 + 前提条件 + 常见问题 -->
        <h4 class="cat-h">通用说明</h4>
        <div class="grid general">
          <article v-for="t in GENERAL" :key="t.name" class="tcard">
            <div class="tt">
              <b>{{ t.name }}</b>
              <span class="cn">{{ t.cn }}</span>
            </div>
            <div class="f">{{ t.formula }}</div>
            <dl>
              <dt>所需栅格</dt><dd>{{ t.bands }}</dd>
              <dt>参数</dt><dd>{{ t.param }}</dd>
              <dt>结果</dt><dd>{{ t.result }}</dd>
            </dl>
          </article>

          <article class="tcard info">
            <div class="tt"><b>前提条件</b></div>
            <dl>
              <dt>分组</dt><dd>指数按用途分组（植被 / 水体与水质 / 城市与裸土 / 燃烧与灾害 / 水分与干旱 / 积雪与盐分），下拉里可直接按组找</dd>
              <dt>影像</dt><dd>必须在「下载影像」页下载完成（有 raw 波段）</dd>
              <dt>波段</dt><dd>缺波段时页面会提示，需重新下载并勾选对应波段</dd>
              <dt>两期</dt><dd>变化分析要求两景范围重叠（同地区不同日期）</dd>
            </dl>
          </article>

          <article class="tcard info">
            <div class="tt"><b>常见问题</b></div>
            <dl>
              <dt>无指数可选</dt><dd>只下载了 RGBN → 只能算 NDVI/NDWI/EVI/SAVI；NDBI/NBR/BSI/MNDWI/NDMI 等需补下 swir16 / swir22</dd>
              <dt>结果空白</dt><dd>下载的是 AOI 裁剪范围，范围外无数据属正常</dd>
              <dt>差异很小</dt><dd>同季节对比更可靠，跨季节差异主要来自物候</dd>
              <dt>定量结论</dt><dd>产量/水质/生物量需地面数据标定，指数为相对指标</dd>
            </dl>
          </article>
        </div>

        <!-- 具体分析单元：按类别分组 -->
        <section v-for="g in INDEX_GROUPS" :key="g.label" class="cat">
          <h4 class="cat-h">{{ g.label }} <span class="muted">（{{ g.items.length }}）</span></h4>
          <div class="grid">
            <article v-for="t in g.items" :key="t.name" class="tcard">
              <div class="tt">
                <b>{{ t.name }}</b>
                <span class="cn">{{ t.cn }}</span>
              </div>
              <div class="f">{{ t.formula }}</div>
              <dl>
                <dt>所需栅格</dt><dd>{{ t.bands }}</dd>
                <dt>参数</dt><dd>{{ t.param }}</dd>
                <dt>结果</dt><dd>{{ t.result }}</dd>
              </dl>
            </article>
          </div>
        </section>
      </div>
    </div>
  </teleport>
</template>

<script setup>
import { computed } from 'vue'

defineEmits(['close'])

// 通用：两期变化（不挂在具体指数分类下）
const GENERAL = [
  {
    name: '两期变化', cn: '变化检测',
    formula: 'Δ = 指数(后期) - 指数(前期)',
    bands: '两景同一指数（系统自动补算缺失的一期）',
    param: '变化阈值默认 0.1：|Δ| < 阈值视为无变化',
    result: '红=下降、白=无变化、绿=上升；统计平均变化、减少/增加/重叠面积；输出 {A}__{B}/{指数}_change.tif',
  },
]

const INDEX_ORDER = ['植被', '水体与水质', '城市与裸土', '燃烧与灾害', '水分与干旱', '积雪与盐分']

const TOOLS = [
  {
    name: 'NDVI', cn: '植被指数', group: '植被',
    formula: '(NIR - Red) / (NIR + Red)',
    bands: 'red、nir（10 m）',
    param: '分类阈值 -0.8~0.8，默认 0.3（>阈值判为植被）',
    result: '棕→黄→绿 伪彩；均值/中位数/P10-P90、有效面积、植被面积与占比；输出 {景}/ndvi.tif',
  },
  {
    name: 'EVI', cn: '增强植被指数', group: '植被',
    formula: '2.5 × (NIR - Red) / (NIR + 6Red - 7.5Blue + 1)',
    bands: 'blue、red、nir（10 m）',
    param: '阈值默认 0.3；分母趋零时结果裁到 [-1, 1]',
    result: '浓密植被不饱和，适合高覆盖区；输出 {景}/evi.tif',
  },
  {
    name: 'SAVI', cn: '土壤调整植被', group: '植被',
    formula: '(NIR - Red)(1 + L) / (NIR + Red + L)，L = 0.5',
    bands: 'red、nir（10 m）',
    param: '阈值默认 0.3',
    result: '裸土/稀疏植被区抗土壤背景；输出 {景}/savi.tif',
  },
  {
    name: 'NDRE', cn: '红边叶绿素', group: '植被',
    formula: '(NIR - RedEdge1) / (NIR + RedEdge1)',
    bands: 'nir、rededge1（仅 Sentinel-2）',
    param: '阈值默认 0.2；对植被胁迫比 NDVI 更敏感',
    result: '棕→黄→绿 伪彩；长势分级；输出 {景}/ndre.tif',
  },
  {
    name: 'NDWI', cn: '水体指数', group: '水体与水质',
    formula: '(Green - NIR) / (Green + NIR)',
    bands: 'green、nir（10 m）',
    param: '阈值默认 0.1（>阈值判为水体）',
    result: '棕→白→蓝 伪彩；水体面积与占比；输出 {景}/ndwi.tif',
  },
  {
    name: 'MNDWI', cn: '改进水体指数', group: '水体与水质',
    formula: '(Green - SWIR1) / (Green + SWIR1)',
    bands: 'green、swir16',
    param: '阈值默认 0.1；比 NDWI 更能抑制建筑与阴影',
    result: '水体提取首选；输出 {景}/mndwi.tif',
  },
  {
    name: 'AWEI', cn: '自动水体提取', group: '水体与水质',
    formula: '4(Green - SWIR1) - (0.25NIR + 2.75SWIR2)',
    bands: 'green、nir、swir16、swir22',
    param: '阈值默认 0（>0 判为水体）',
    result: '抑制阴影与暗色地表，适合复杂城区；输出 {景}/awei.tif',
  },
  {
    name: 'FAI', cn: '浮游藻类', group: '水体与水质',
    formula: 'NIR - 基线(Red→SWIR1)；仅在水体（MNDWI>0）内计算',
    bands: 'green、red、nir、swir16',
    param: '阈值默认 0.02；陆地像元一律置空',
    result: '水华/藻类富集区；输出 {景}/fai.tif',
  },
  {
    name: 'NDBI', cn: '建筑指数', group: '城市与裸土',
    formula: '(SWIR1 - NIR) / (SWIR1 + NIR)',
    bands: 'swir16、nir（20/10 m，自动重采样）',
    param: '阈值默认 0.05（>阈值判为建成区）',
    result: '绿→灰→红 伪彩；建成区面积与占比；输出 {景}/ndbi.tif',
  },
  {
    name: 'BSI', cn: '裸土指数', group: '城市与裸土',
    formula: '((SWIR1+Red) - (NIR+Blue)) / ((SWIR1+Red) + (NIR+Blue))',
    bands: 'swir16、red、nir、blue',
    param: '阈值默认 0.1（>阈值偏裸土/建成区）',
    result: '白→灰→棕 伪彩；裸土/扰动面积；输出 {景}/bsi.tif',
  },
  {
    name: 'NBR', cn: '燃烧指数', group: '燃烧与灾害',
    formula: '(NIR - SWIR2) / (NIR + SWIR2)',
    bands: 'nir、swir22',
    param: '阈值默认 0.3；配合两期差值可分级过火严重度',
    result: '黑→黄→绿 伪彩；低值区为火烧/裸地；输出 {景}/nbr.tif',
  },
  {
    name: 'NBR2', cn: '燃烧指数2', group: '燃烧与灾害',
    formula: '(SWIR1 - SWIR2) / (SWIR1 + SWIR2)',
    bands: 'swir16、swir22',
    param: '阈值默认 0.05',
    result: '对水分/燃烧后残留更敏感，常与 NBR 配合；输出 {景}/nbr2.tif',
  },
  {
    name: 'NDMI', cn: '植被水分', group: '水分与干旱',
    formula: '(NIR - SWIR1) / (NIR + SWIR1)',
    bands: 'nir、swir16',
    param: '阈值默认 0.1（>阈值水分充足）',
    result: '干旱/灌溉差异、植被含水量；输出 {景}/ndmi.tif',
  },
  {
    name: 'NDSI', cn: '积雪指数', group: '积雪与盐分',
    formula: '(Green - SWIR1) / (Green + SWIR1)',
    bands: 'green、swir16',
    param: '阈值默认 0.4（>阈值判为积雪）',
    result: '积雪范围与面积；输出 {景}/ndsi.tif',
  },
  {
    name: 'SI', cn: '盐分指数', group: '积雪与盐分',
    formula: '√(Red × SWIR1)',
    bands: 'red、swir16',
    param: '阈值默认 0.15（>阈值偏盐渍化）',
    result: '盐渍化/干旱区土壤盐分相对高低；输出 {景}/si.tif',
  },
]

const INDEX_GROUPS = computed(() =>
  INDEX_ORDER.map((g) => ({ label: g, items: TOOLS.filter((t) => t.group === g) })).filter(
    (g) => g.items.length,
  ),
)
</script>

<style scoped>
.mask { position: fixed; inset: 0; background: rgba(0,0,0,.45); z-index: 100; display: flex; align-items: center; justify-content: center; }
.dlg {
  width: 1080px; max-width: calc(100vw - 48px); max-height: 80vh; overflow: auto;
  background: var(--panel); border: 1px solid var(--line); border-radius: 12px;
  padding: 16px 18px; box-shadow: 0 18px 60px rgba(16,24,40,.3);
}
.hd { display: flex; align-items: center; justify-content: space-between; margin-bottom: 12px; }
.hd h3 { margin: 0; font-size: 15px; }
.x { flex: none; min-height: 26px; padding: 2px 9px; }
.grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(300px, 1fr)); gap: 12px; }
.general { margin-bottom: 4px; }
.cat-h {
  margin: 16px 0 8px; font-size: 12.5px; font-weight: 600; color: var(--muted);
  border-bottom: 1px dashed var(--line); padding-bottom: 4px;
}
.cat:first-of-type .cat-h { margin-top: 6px; }
.tcard { background: var(--panel); border: 1px solid var(--line); border-radius: 10px; padding: 12px; box-shadow: var(--shadow); }
.tcard.info { background: var(--track); }
.tt { display: flex; align-items: baseline; gap: 8px; margin-bottom: 6px; }
.tt b { font-size: 13px; }
.cn { color: var(--muted); font-size: 11.5px; }
.f { font-family: Consolas, monospace; font-size: 11.5px; color: var(--accent); background: #f5f9ff; border: 1px solid #dbeafe; border-radius: 6px; padding: 4px 6px; margin-bottom: 8px; word-break: break-all; }
dl { margin: 0; font-size: 11.5px; }
dt { color: var(--muted); margin-top: 6px; }
dd { margin: 1px 0 0; color: var(--ink); line-height: 1.65; }
</style>
