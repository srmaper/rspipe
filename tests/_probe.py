"""只读：列出深圳原始影像的精确下载清单（不下载）。"""


from pystac_client import Client

from rspipe.ingest.cogreader import CogReader
from rspipe.ingest.signing import needs_signing, sign_href

SZ = (113.760, 22.410, 114.620, 22.870)
TARGETS = {
    "earth-search": ("sentinel-2-l2a", "S2A_49QHF_20261007_0_L2A"),
    "planetary-computer": ("landsat-c2-l2", "LC08_L2SP_121044_20261009_02_T1"),
}

# 「光谱波段 + QA」白名单；排除 metadata/preview/aux
SPECTRAL = {
    "coastal", "blue", "green", "red", "rededge1", "rededge2", "rededge3",
    "nir", "nir08", "nir09", "swir16", "swir22",
    "B01", "B02", "B03", "B04", "B05", "B06", "B07", "B08", "B8A", "B09", "B11", "B12",
    "SR_B1", "SR_B2", "SR_B3", "SR_B4", "SR_B5", "SR_B6", "SR_B7",
}
QA = {"scl", "qa_pixel", "qa"}

for prov, (coll, sid) in TARGETS.items():
    url = (
        "https://earth-search.aws.element84.com/v1"
        if prov == "earth-search"
        else "https://planetarycomputer.microsoft.com/api/stac/v1"
    )
    c = Client.open(url)
    s = c.search(collections=[coll], ids=[sid], max_items=1)
    items = list(s.items())
    if not items:
        print(f"\n!! {sid} 未找到")
        continue
    it = items[0]
    print(f"\n{'='*92}")
    print(f"{prov} / {coll} / {sid}")
    print(f"{'='*92}")

    total_raw = 0
    total_aoi = 0
    rows = []
    for key, a in sorted(it.assets.items()):
        if key not in SPECTRAL and key not in QA:
            continue
        href = sign_href(a.href) if needs_signing(a.href) else a.href
        try:
            with CogReader(href) as r:
                info = r.info
                col, row, w, h = r.window_for_bbox(SZ)
                bpp = info.bits_per_sample // 8
                raw = info.width * info.height * bpp
                aoi = w * h * bpp
                rows.append((key, info.width, info.height, w, h, str(info.dtype), raw, aoi, href))
                total_raw += raw
                total_aoi += aoi
        except Exception as exc:
            rows.append((key, 0, 0, 0, 0, "?", 0, 0, f"ERR {str(exc)[:40]}"))

    print(f"{'asset':10s} {'源尺寸':>14s} {'AOI 窗口':>14s} {'dtype':>8s} {'原始MB':>8s} {'AOI MB':>8s}")
    print("-" * 92)
    for key, sw, sh, w, h, dt, raw, aoi, _ in rows:
        print(
            f"{key:10s} {sw:6d}x{sh:<7d} {w:6d}x{h:<7d} {dt:>8s} {raw/1e6:8.1f} {aoi/1e6:8.1f}"
        )
    print("-" * 92)
    print(f"{'合计':10s} {'':>14s} {'':>14s} {'':>8s} {total_raw/1e6:8.1f} {total_aoi/1e6:8.1f}")
    print(f"文件数: {len(rows)}   AOI 裁剪后体积 ≈ {total_aoi/1e6:.0f} MB（未压缩）")
