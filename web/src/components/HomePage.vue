<template>
  <div class="home">
    <header class="top">
      <span class="brand"><i></i> rspipe 遥感影像平台</span>
      <span class="tag">Landsat / Sentinel-2 · 检索 · 预览 · 下载 · 应用</span>
    </header>

    <main class="cards">
      <section class="card big" @click="$emit('go', 'download')">
        <div class="ico">🛰</div>
        <h2>下载影像</h2>
        <p>圈选范围或搜索地名 → 检索场景（Tier / AOI 实测云量排序）→ 选择波段下载<br>
           自动生成 4326 预览栅格与 AOI 记录，支持真彩 / 假彩 / NDVI 预览</p>
        <span class="go">进入 →</span>
      </section>

      <section class="card big" @click="$emit('go', 'analysis')">
        <div class="ico">📊</div>
        <h2>数据分析</h2>
        <p>基于已下载影像做指数计算与专题分析：城市扩张、绿地率、植被长势、<br>
           水体提取、火烧迹地、洪涝范围、违法占地、灾前灾后对比</p>
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
.home { min-height: 100vh; display: flex; flex-direction: column; background: linear-gradient(180deg, #eef1f6, #e7ebf2); }
.top { display: flex; align-items: center; gap: 14px; padding: 14px 20px; background: var(--panel); border-bottom: 1px solid var(--line); }
.brand { font-size: 15px; font-weight: 700; display: flex; align-items: center; gap: 9px; }
.brand i { width: 22px; height: 22px; border-radius: 7px; background: linear-gradient(135deg, var(--accent), #22d3ee); display: inline-block; }
.tag { color: var(--muted); font-size: 12px; }
.cards { flex: 1; display: flex; gap: 24px; align-items: center; justify-content: center; padding: 32px; }
.card.big { width: 420px; min-height: 260px; padding: 26px; cursor: pointer; display: flex; flex-direction: column; gap: 10px; transition: all .18s; }
.card.big:hover { border-color: var(--accent); transform: translateY(-3px); box-shadow: 0 12px 32px rgba(37,99,235,.16); }
.ico { font-size: 34px; }
h2 { margin: 0; font-size: 20px; }
p { margin: 0; color: var(--muted); font-size: 12.5px; line-height: 1.9; }
.go { margin-top: auto; color: var(--accent); font-weight: 600; font-size: 13px; }
.foot { text-align: center; color: var(--muted); font-size: 11.5px; padding: 14px; }
</style>
