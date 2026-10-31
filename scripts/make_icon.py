"""生成 rspipe 应用图标：地球 + 轨道 + 卫星（B3 方案）。

输出：
    packaging/rspipe.ico            exe / 安装包图标（16–256 多尺寸）
    web/public/favicon.ico          浏览器标签页
    web/public/icon-192.png         安卓 / PWA
    web/public/icon-512.png
    web/public/apple-touch-icon.png iOS
    docs/icon.png                   README 展示

用法：python scripts/make_icon.py
"""

import math
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
S = 2048

BG_TOP = (13, 33, 55)
BG_BOT = (6, 20, 35)
BLUE = (37, 99, 235)
BLUE_D = (29, 78, 216)
CYAN = (56, 189, 248)
WHITE = (236, 244, 252)

CX, CY, R = S * 0.47, S * 0.615, S * 0.25
ANG = math.radians(-20)
RX, RY = S * 0.445, S * 0.180


def lerp(a, b, t):
    return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))


def rot(pts, ang, cx, cy):
    c, s = math.cos(ang), math.sin(ang)
    return [(cx + (x - cx) * c - (y - cy) * s, cy + (x - cx) * s + (y - cy) * c) for x, y in pts]


def rounded_bg():
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    for y in range(S):
        d.line([(0, y), (S, y)], fill=lerp(BG_TOP, BG_BOT, y / S) + (255,))
    mask = Image.new("L", (S, S), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, S - 1, S - 1], radius=int(S * 0.22), fill=255)
    img.putalpha(mask)
    return img, mask


def glow(img, cx, cy, r, color, alpha=42):
    lay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(lay)
    steps = 30
    for i in range(steps, 0, -1):
        rr = r * i / steps
        d.ellipse([cx - rr, cy - rr, cx + rr, cy + rr], fill=color + (int(alpha * (1 - i / steps) ** 1.7),))
    img.alpha_composite(lay)


def globe(img, cx, cy, r):
    lay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(lay)
    for y in range(int(cy - r), int(cy + r) + 1):
        d.line([(0, y), (S, y)], fill=lerp((30, 64, 175), (14, 165, 233), (y - (cy - r)) / (2 * r)) + (255,))
    m = Image.new("L", img.size, 0)
    ImageDraw.Draw(m).ellipse([cx - r, cy - r, cx + r, cy + r], fill=255)
    lay.putalpha(Image.composite(m, Image.new("L", img.size, 0), lay.getchannel("A")))
    img.alpha_composite(lay)

    d = ImageDraw.Draw(img)
    w = max(2, int(S * 0.0032))
    line = (255, 255, 255, 80)
    d.ellipse([cx - r, cy - r, cx + r, cy + r], outline=(255, 255, 255, 145), width=w)
    for k in (0.45, 0.0, -0.45):
        yy = cy + r * k
        half = math.sqrt(max(0.0, r * r - (r * k) ** 2))
        d.line([(cx - half, yy), (cx + half, yy)], fill=line, width=w)
    for k in (0.0, 0.52, 0.86):
        rx = r * k if k else r
        d.ellipse([cx - rx, cy - r, cx + rx, cy + r], outline=line, width=w)


def satellite(img, cx, cy, span, ang):
    bw, bh = span * 0.30, span * 0.20
    pw, ph = span * 0.33, span * 0.16
    gap = span * 0.030
    cell = (147, 197, 253, 170)
    cw = max(2, int(S * 0.0018))

    def rrect(box, radius, fill=None, outline=None, width=1):
        lay = Image.new("RGBA", img.size, (0, 0, 0, 0))
        ImageDraw.Draw(lay).rounded_rectangle(box, radius=radius, fill=fill, outline=outline, width=width)
        lay = lay.rotate(-math.degrees(ang), resample=Image.BICUBIC, center=(cx, cy))
        img.alpha_composite(lay)

    for sign in (-1, 1):
        x0 = cx + bw / 2 + gap if sign > 0 else cx - bw / 2 - gap - pw
        box = [x0, cy - ph / 2, x0 + pw, cy + ph / 2]
        rrect(box, int(ph * 0.14), fill=BLUE_D + (255,))
        lay = Image.new("RGBA", img.size, (0, 0, 0, 0))
        ld = ImageDraw.Draw(lay)
        for i in range(1, 3):
            x = x0 + pw * i / 3
            ld.line([(x, cy - ph / 2), (x, cy + ph / 2)], fill=cell, width=cw)
        ld.line([(x0, cy), (x0 + pw, cy)], fill=cell, width=cw)
        lay = lay.rotate(-math.degrees(ang), resample=Image.BICUBIC, center=(cx, cy))
        img.alpha_composite(lay)
        rrect(box, int(ph * 0.14), outline=(191, 219, 254, 200), width=max(2, int(S * 0.0022)))

    box = [cx - bw / 2, cy - bh / 2, cx + bw / 2, cy + bh / 2]
    rrect(box, int(bh * 0.3), fill=WHITE + (255,))
    rrect([box[0] + bw * 0.14, box[1] + bh * 0.3, box[2] - bw * 0.14, box[3] - bh * 0.3],
          int(bh * 0.1), fill=BLUE + (255,))


def render():
    img, mask = rounded_bg()
    glow(img, CX, CY, S * 0.44, CYAN, 42)

    w = int(S * 0.013)
    back = rot([(CX + RX * math.cos(t), CY + RY * math.sin(t))
                for t in [math.pi * i / 360 for i in range(361)]], ANG, CX, CY)
    front = rot([(CX + RX * math.cos(t), CY + RY * math.sin(t))
                 for t in [math.pi + math.pi * i / 360 for i in range(361)]], ANG, CX, CY)

    lay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    ImageDraw.Draw(lay).line(back, fill=CYAN + (145,), width=w, joint="curve")
    img.alpha_composite(lay)

    globe(img, CX, CY, R)

    lay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    ImageDraw.Draw(lay).line(front, fill=CYAN + (245,), width=w, joint="curve")
    img.alpha_composite(lay)

    t = math.radians(312)
    sx, sy = rot([(CX + RX * math.cos(t), CY + RY * math.sin(t))], ANG, CX, CY)[0]
    satellite(img, sx, sy, S * 0.29, ANG + math.radians(14))

    img.putalpha(Image.composite(img.getchannel("A"), Image.new("L", img.size, 0), mask))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([int(S * 0.012)] * 2 + [S - int(S * 0.012)] * 2,
                        radius=int(S * 0.21), outline=(255, 255, 255, 24), width=int(S * 0.005))
    return img


def main():
    (ROOT / "web" / "public").mkdir(parents=True, exist_ok=True)
    master = render()
    master.resize((512, 512), Image.LANCZOS).save(ROOT / "docs" / "icon.png")

    ico = master.resize((256, 256), Image.LANCZOS)
    ico.save(ROOT / "packaging" / "rspipe.ico",
             sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    ico.save(ROOT / "web" / "public" / "favicon.ico", sizes=[(16, 16), (32, 32), (48, 48)])

    for name, size in (("icon-192.png", 192), ("icon-512.png", 512), ("apple-touch-icon.png", 180)):
        master.resize((size, size), Image.LANCZOS).save(ROOT / "web" / "public" / name)

    print("ok:", [p.name for p in (ROOT / "web" / "public").iterdir()])


if __name__ == "__main__":
    main()
