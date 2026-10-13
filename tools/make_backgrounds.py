"""產生內建背景圖（開發者用）：static/backgrounds/<名稱>-dark.jpg、<名稱>-light.jpg。

設計原則（使用者指定）：簡潔、不是單色、看起來舒服、低飽和；每個元素都有來由（物理／反重力），不放無來由的裝飾。
用法：python tools/make_backgrounds.py（需要 numpy、Pillow）
"""
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "static" / "backgrounds"
W, H = 2560, 1600
S = W / 480                       # 草稿是 480 寬，線條粗細依比例放大
y, x = np.mgrid[0:H, 0:W].astype(np.float32)
u, v = x / W, y / H


def grain(a, amt=3.0, seed=7):
    rng = np.random.default_rng(seed)
    return np.clip(a + rng.normal(0, amt, a.shape[:2])[..., None], 0, 255)


def mix(colors, weights):
    ws = np.stack(weights, -1)
    ws = ws / ws.sum(-1, keepdims=True)
    return np.tensordot(ws, np.array(colors, np.float32), axes=([-1], [0]))


def grad(c1, c2, c3=None):
    t = 0.65 * v + 0.35 * u
    c1, c2 = np.array(c1, np.float32), np.array(c2, np.float32)
    if c3 is None:
        return c1 * (1 - t[..., None]) + c2 * t[..., None]
    c3 = np.array(c3, np.float32)
    a = np.clip(t * 2, 0, 1)[..., None]; b = np.clip(t * 2 - 1, 0, 1)[..., None]
    return c1 * (1 - a) + c2 * a * (1 - b) + c3 * b


def blob(cx, cy, r):
    return np.exp(-(((u - cx) ** 2) / r ** 2 + ((v - cy) ** 2) / (r * 0.7) ** 2))


def mist_base(dark):
    cols = ([(38, 44, 58), (52, 62, 66), (66, 58, 60), (44, 48, 70)] if dark
            else [(214, 220, 228), (210, 222, 214), (232, 222, 210), (222, 216, 230)])
    return mix(cols, [blob(.15, .2, .5) + .05, blob(.85, .3, .45), blob(.6, .95, .55), blob(.2, .9, .4)])


def lines(draw_fn, dark, strength, blur=1.2):
    layer = Image.new("L", (W, H), 0)
    draw_fn(ImageDraw.Draw(layer))
    m = np.asarray(layer.filter(ImageFilter.GaussianBlur(blur)), np.float32)[..., None] / 255
    return m * (strength if dark else -strength)


# 2 波的干涉：雙點波源的干涉條紋（量子的波動性），只在一側淡淡出現
def wave(dark):
    a = mist_base(dark)
    r1 = np.hypot(x - W * .18, y - H * 1.1); r2 = np.hypot(x - W * .42, y - H * 1.1)
    pattern = np.cos(r1 / (9 * S)) + np.cos(r2 / (9 * S))
    fade = np.clip(1.2 - v, 0, 1) * np.clip(u * 1.4, 0, 1)
    return a + pattern[..., None] * fade[..., None] * (5 if dark else -5)


# 3 等高線：位能地形的等位線（勢能井）
def contour(dark):
    cols = ([(34, 40, 52), (46, 54, 58), (56, 50, 58)] if dark else [(222, 226, 232), (226, 230, 222), (234, 226, 222)])
    a = mix(cols, [1 - u + .2, u * (1 - v) + .2, v + .1])
    phi = -1.0 / (np.hypot(u - .72, (v - .55) * 1.4) + .12) - .6 / (np.hypot(u - .3, (v - .35) * 1.4) + .2)
    band = np.abs(np.sin(phi * 6))
    line = np.clip(1 - band / 0.035, 0, 1)                         # 細而平滑的等位線
    return a + line[..., None] * (13 if dark else -15)


# 5 布洛赫球：qubit 狀態的幾何表示，右側一個很大、很淡的球
def bloch(dark):
    a = grad((32, 38, 50), (44, 48, 62)) if dark else grad((226, 230, 236), (216, 222, 232))
    cx, cy, r = W * .78, H * .55, H * .62
    w = int(2 * S)

    def f(d):
        d.ellipse([cx - r, cy - r, cx + r, cy + r], outline=255, width=w)
        for k in (.25, .55):
            d.ellipse([cx - r, cy - r * k, cx + r, cy + r * k], outline=150, width=max(1, w // 2))
        d.ellipse([cx - r * .4, cy - r, cx + r * .4, cy + r], outline=110, width=max(1, w // 2))
        d.line([cx, cy, cx + r * .45, cy - r * .62], fill=200, width=w)
    return a + lines(f, dark, 15, blur=S * 0.8)


# 7 暮色：板岩藍 → 霧紫 → 暖灰的純色調漸層
def dusk(dark):
    return grad((30, 36, 50), (46, 42, 60), (58, 48, 52)) if dark else grad((220, 226, 236), (228, 222, 234), (238, 228, 222))


# 9 電偶極場線：左下角淡淡的電場線（像講義插圖），其餘留白
def field(dark):
    a = grad((30, 36, 46), (40, 46, 56)) if dark else grad((226, 232, 236), (222, 228, 234))
    q1, q2 = np.array([W * .16, H * .78]), np.array([W * .34, H * .78])

    def f(d):
        for k in range(14):
            ang = 2 * np.pi * k / 14
            p = q1 + 4 * S * np.array([np.cos(ang), np.sin(ang)])
            pts = [tuple(p)]
            for _ in range(2600):
                r1, r2 = p - q1, p - q2
                E = r1 / np.linalg.norm(r1) ** 3 - r2 / np.linalg.norm(r2) ** 3
                p = p + 2.0 * S * E / (np.linalg.norm(E) + 1e-12)
                pts.append(tuple(p))
                if np.linalg.norm(p - q2) < 4 * S or not (-300 < p[0] < W + 300 and -300 < p[1] < H + 300):
                    break
            d.line(pts, fill=255, width=max(1, int(1.2 * S)))
    fade = np.clip(1.25 - np.hypot(u - .25, v - .78) * 1.6, 0, 1)[..., None]
    return a + lines(f, dark, 17, blur=S * 0.7) * fade


DESIGNS = {"wave": wave, "contour": contour, "bloch": bloch, "dusk": dusk, "field": field}


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    for name, fn in DESIGNS.items():
        for dark in (True, False):
            a = grain(fn(dark), seed=sum(map(ord, name)))      # 固定種子：每次產生的結果一樣
            img = Image.fromarray(a.astype(np.uint8)).filter(ImageFilter.GaussianBlur(0.6))
            path = OUT / f"{name}-{'dark' if dark else 'light'}.jpg"
            img.save(path, quality=88, optimize=True, progressive=True)
            print(f"{path.name}  {path.stat().st_size // 1024} KB")


if __name__ == "__main__":
    main()
