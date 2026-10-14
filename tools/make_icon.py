"""產生介面圖標（開發者用）：static/icon.png（網頁用）與 static/icon.ico（桌面捷徑用）。

Lumina 的圖示「L 字光束」：近黑色圓角方塊、白色粗 L、右上一顆橘色小點像光源（v2.0.0 起；之前是負空間 A）。
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
    d.rectangle([250, 210, 400, 810], fill="white")                     # L 的直筆
    d.rectangle([250, 660, 760, 810], fill="white")                     # L 的橫筆
    d.ellipse([650, 200, 790, 340], fill=ACCENT)                         # 橘色光點
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
