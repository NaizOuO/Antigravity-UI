"""產生介面圖標（開發者用）：static/icon.png（網頁用）與 static/icon.ico（桌面捷徑用）。

設計「負空間 A」：近黑色圓角方塊；白色實心三角形，底部中間挖空形成 A；右上一顆橘色小點。
用法：python tools/make_icon.py（需要 Pillow）
"""

from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
S = 1024                        # 先畫大圖再縮小，邊緣才平滑
BG = (18, 18, 18)               # #121212
ACCENT = (255, 122, 69)         # #FF7A45


def draw():
    im = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.rounded_rectangle([0, 0, S - 1, S - 1], radius=230, fill=BG)
    d.polygon([(512, 210), (800, 790), (224, 790)], fill="white")        # A 的外框
    d.polygon([(512, 520), (610, 790), (414, 790)], fill=BG)             # 中間挖空
    d.ellipse([708, 188, 832, 312], fill=ACCENT)                         # 橘色小點
    return im


def main():
    big = draw()
    out = ROOT / "static"
    big.resize((512, 512), Image.LANCZOS).save(out / "icon.png")
    sizes = [(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]
    big.resize((256, 256), Image.LANCZOS).save(out / "icon.ico", sizes=sizes)
    print("已產生", out / "icon.png", "與", out / "icon.ico")


if __name__ == "__main__":
    main()
