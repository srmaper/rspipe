<template>
  <div class="home">
    <header class="top">
      <span class="brand"><i></i> rspipe 遥感影像平台</span>
      <span class="tag">Landsat / Sentinel-2 · 检索 · 预览 · 下载 · 应用</span>
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
  </div>
</template>

<script setup>
defineEmits(['go'])
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
