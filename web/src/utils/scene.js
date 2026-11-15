/**
 * 景号缩写：完整 STAC id 太长（Sentinel-1 有 60+ 字符），界面统一用短名，
 * 完整 id 放 title 悬停查看。
 *
 *   S1D_IW_GRDH_1SDV_20261025T105802_..._rtc  →  S1D IW 2026-10-25 10:58
 *   S2C_52TES_20261006_0_L2A                  →  S2C 2026-10-06 52TES
 *   LC09_L2SP_119039_20240826_02_T1           →  L9 2024-08-26 119/039
 */
export function shortScene(id, opts = {}) {
  const withDate = opts.date !== false
  const s = String(id || '')
  if (!s) return ''

  let m = s.match(/^(S1[A-D])_([A-Z]{2})_[A-Z0-9]+_[A-Z0-9]+_(\d{8})T(\d{4})/i)
  if (m) {
    const [, sat, mode, d, t] = m
    return `${sat} ${mode} ${fmtDate(d)} ${t.slice(0, 2)}:${t.slice(2)}`
  }
  m = s.match(/^(S2[A-D])_([0-9A-Z]{5})_(\d{8})_/i)
  if (m) return `${m[1]} ${fmtDate(m[3])} ${m[2]}`
  m = s.match(/^L([CEOT])(\d{2})_[A-Z0-9]+_(\d{3})(\d{3})_(\d{8})_/i)
  if (m) return `L${Number(m[2])} ${fmtDate(m[5])} ${m[3]}/${m[4]}`
  return s
}

function fmtDate(yyyymmdd) {
  return `${yyyymmdd.slice(0, 4)}-${yyyymmdd.slice(4, 6)}-${yyyymmdd.slice(6, 8)}`
}
