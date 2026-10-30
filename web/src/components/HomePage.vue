<template>
  <div class="home">
    <header class="top">
      <span class="brand"><i></i> rspipe 遥感影像平台</span>
      <button class="contact" @click="contactOpen = true">联系作者</button>
    </header>

    <main class="cards">
      <section class="card big" @click="$emit('go', 'download')">
        <div class="ico">🛰</div>
        <h2>下载遥感影像</h2>
        <p>圈选范围或搜索地名，检索场景后选择波段下载；自动生成 4326 预览栅格与 AOI 记录，支持真彩 / 假彩 / NDVI 预览</p>
        <span class="go">进入 →</span>
      </section>

      <section class="card big" @click="$emit('go', 'analysis')">
        <div class="ico">📊</div>
        <h2>遥感数据分析</h2>
        <p>基于已下载影像做指数计算与专题分析：城市扩张、绿地率、植被长势、水体提取、火烧迹地、洪涝范围、违法占地、灾前灾后对比</p>
        <span class="go">进入 →</span>
      </section>
    </main>

    <footer class="foot">数据源：earth-search（Sentinel-2）· planetary-computer（Landsat 8/9）</footer>

    <div v-if="contactOpen" class="mask" @click.self="contactOpen = false">
      <div class="dialog" role="dialog" aria-modal="true" aria-label="联系作者">
        <h3>联系作者</h3>
        <p class="tip">使用中遇到问题、有新的分析需求，欢迎微信联系</p>
        <div class="wx">
          <span class="label">微信</span>
          <b class="num">{{ WECHAT }}</b>
        </div>
        <div class="acts">
          <button class="ghost" @click="contactOpen = false">关闭</button>
          <button class="primary" @click="copyWx">{{ copied ? '已复制' : '复制微信号' }}</button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { onBeforeUnmount, onMounted, ref } from 'vue'

defineEmits(['go'])

const WECHAT = '18734242374'
const contactOpen = ref(false)
const copied = ref(false)
let copiedTimer = null

async function copyWx() {
  try {
    await navigator.clipboard.writeText(WECHAT)
  } catch {
    const el = document.createElement('textarea')
    el.value = WECHAT
    el.style.position = 'fixed'
    el.style.opacity = '0'
    document.body.appendChild(el)
    el.select()
    document.execCommand('copy')
    document.body.removeChild(el)
  }
  copied.value = true
  clearTimeout(copiedTimer)
  copiedTimer = setTimeout(() => { copied.value = false }, 1600)
}

function onKey(e) {
  if (e.key === 'Escape') contactOpen.value = false
}
onMounted(() => window.addEventListener('keydown', onKey))
onBeforeUnmount(() => {
  window.removeEventListener('keydown', onKey)
  clearTimeout(copiedTimer)
})
</script>

<style scoped>
.home {
  min-height: 100vh;
  display: flex;
  flex-direction: column;
  background: #05090f url('../assets/home-bg.jpg') center / cover no-repeat fixed;
  color: #eaf2fb;
  position: relative;
  overflow: hidden;
}
.home::after {
  content: "";
  position: absolute;
  inset: 0;
  background: radial-gradient(120% 80% at 50% 110%, rgba(56, 189, 248, .18), transparent 60%);
  pointer-events: none;
}
.top {
  position: relative;
  z-index: 2;
  display: flex;
  align-items: center;
  gap: 14px;
  padding: 16px 22px;
  background: linear-gradient(180deg, rgba(3, 8, 18, .72), rgba(3, 8, 18, 0));
  backdrop-filter: blur(6px);
}
.brand { font-size: 15px; font-weight: 700; display: flex; align-items: center; gap: 9px; color: #fff; letter-spacing: .3px; }
.brand i {
  width: 22px; height: 22px; border-radius: 7px;
  background: linear-gradient(135deg, #38bdf8, #22d3ee);
  box-shadow: 0 0 14px rgba(56, 189, 248, .65);
  display: inline-block;
}
.tag { color: rgba(226, 240, 255, .62); font-size: 12px; }
.contact {
  margin-left: auto;
  padding: 5px 12px;
  font-size: 12px;
  color: #cfe9ff;
  background: rgba(255, 255, 255, .07);
  border: 1px solid rgba(255, 255, 255, .18);
  border-radius: 999px;
  cursor: pointer;
  backdrop-filter: blur(8px);
  transition: all .18s ease;
}
.contact:hover { color: #fff; border-color: rgba(56, 189, 248, .8); background: rgba(56, 189, 248, .18); }

.mask {
  position: fixed;
  inset: 0;
  z-index: 40;
  display: flex;
  align-items: center;
  justify-content: center;
  background: rgba(2, 6, 14, .58);
  backdrop-filter: blur(3px);
}
.dialog {
  width: 340px;
  padding: 22px 22px 18px;
  border-radius: 16px;
  border: 1px solid rgba(255, 255, 255, .18);
  background: linear-gradient(170deg, #ffffff, #f2f8ff);
  box-shadow: 0 26px 60px rgba(2, 8, 20, .45), inset 0 1px 0 #fff;
  color: #0f172a;
}
.dialog h3 { margin: 0 0 6px; font-size: 16px; color: #0f172a; letter-spacing: .4px; }
.tip { margin: 0 0 16px; font-size: 12px; line-height: 1.7; color: rgba(15, 23, 42, .58); }
.wx {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 12px 14px;
  border-radius: 11px;
  background: #eef7ff;
  border: 1px solid #bfe3fb;
}
.wx .label { font-size: 12px; color: #0284c7; }
.wx .num { font-size: 18px; letter-spacing: 1.4px; color: #0f172a; font-variant-numeric: tabular-nums; }
.acts { display: flex; justify-content: flex-end; gap: 10px; margin-top: 18px; }
.acts button {
  padding: 7px 16px;
  font-size: 12.5px;
  border-radius: 9px;
  cursor: pointer;
  border: 1px solid transparent;
  transition: all .18s ease;
}
.ghost { background: #f1f5f9; border-color: #e2e8f0; color: #334155; }
.ghost:hover { background: #e6edf5; color: #0f172a; }
.primary { background: linear-gradient(135deg, #38bdf8, #22d3ee); color: #04121f; font-weight: 700; }
.primary:hover { filter: brightness(1.08); }

.cards {
  position: relative;
  z-index: 2;
  flex: 1;
  display: flex;
  gap: 26px;
  align-items: center;
  justify-content: center;
  padding: 32px;
  flex-wrap: wrap;
}
.card.big {
  width: 430px;
  min-height: 268px;
  padding: 28px;
  cursor: pointer;
  display: flex;
  flex-direction: column;
  gap: 12px;
  border-radius: 18px;
  border: 1px solid rgba(255, 255, 255, .16);
  background: linear-gradient(160deg, rgba(255, 255, 255, .10), rgba(255, 255, 255, .03));
  backdrop-filter: blur(16px) saturate(120%);
  box-shadow: 0 20px 50px rgba(2, 8, 20, .55), inset 0 1px 0 rgba(255, 255, 255, .18);
  transition: transform .22s ease, box-shadow .22s ease, border-color .22s ease;
}
.card.big:hover {
  transform: translateY(-6px);
  border-color: rgba(56, 189, 248, .75);
  box-shadow: 0 26px 60px rgba(3, 10, 25, .7), 0 0 0 1px rgba(56, 189, 248, .35), inset 0 1px 0 rgba(255, 255, 255, .22);
}
.ico {
  font-size: 36px;
  filter: drop-shadow(0 0 12px rgba(56, 189, 248, .55));
}
h2 { margin: 0; font-size: 21px; color: #fff; letter-spacing: .5px; }
p { margin: 0; color: rgba(226, 240, 255, .72); font-size: 12.5px; line-height: 1.95; }
.go {
  margin-top: auto;
  font-size: 13px;
  font-weight: 600;
  color: #7dd3fc;
  display: inline-flex;
  align-items: center;
  gap: 6px;
}
.card.big:hover .go { color: #e0f2fe; }
.foot {
  position: relative;
  z-index: 2;
  text-align: center;
  color: rgba(226, 240, 255, .72);
  font-size: 11.5px;
  padding: 26px 16px 18px;
  background: linear-gradient(0deg, rgba(3, 8, 18, .88), rgba(3, 8, 18, 0));
  letter-spacing: .3px;
}
</style>
