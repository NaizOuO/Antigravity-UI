"""run_capture 的網頁版：用無頭 Edge（Playwright）開啟工作資料夾裡的 .html，照腳本按鍵、截圖。

由 lumina_mcp.py 用工作資料夾的 .venv python 執行（那裡要先 venv_install playwright）。
網頁透過本機的小伺服器開啟（不是 file://），所以 ES module、fetch 讀本地檔案都能用。
看不到畫面、不會出聲、不佔使用者的螢幕。

參數：python lumina_web.py <工作資料夾> <html 相對路徑> <輸出資料夾> <actions JSON>
最後一行輸出 JSON：{"shots": [...], "errors": [...], "console": [...]}
"""
import functools
import http.server
import json
import sys
import threading
import time
from pathlib import Path

work, page_rel, out_dir, actions = Path(sys.argv[1]), sys.argv[2], Path(sys.argv[3]), json.loads(sys.argv[4])
KEYS = {"left": "ArrowLeft", "right": "ArrowRight", "up": "ArrowUp", "down": "ArrowDown", "space": " ",
        "enter": "Enter", "esc": "Escape", "tab": "Tab", "shift": "Shift", "ctrl": "Control", "alt": "Alt"}


def key(name):
    name = name.strip().lower()
    return KEYS.get(name, name if len(name) == 1 else name)


class Quiet(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *a):
        pass


srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(Quiet, directory=str(work)))
threading.Thread(target=srv.serve_forever, daemon=True).start()
url = f"http://127.0.0.1:{srv.server_address[1]}/{page_rel.replace(chr(92), '/')}"

from playwright.sync_api import sync_playwright  # noqa: E402

shots, errors, console = [], [], []
with sync_playwright() as pw:
    browser = pw.chromium.launch(channel="msedge", headless=True,
                                 args=["--use-angle=d3d11", "--enable-unsafe-swiftshader", "--autoplay-policy=no-user-gesture-required",
                                       "--mute-audio"])
    page = browser.new_page(viewport={"width": 1280, "height": 720})
    page.on("pageerror", lambda e: errors.append(str(e)[:300]))
    page.on("console", lambda m: console.append(f"{m.type}: {m.text}"[:200]) if m.type in ("error", "warning") else None)
    page.goto(url)
    try:                                            # 遊戲載入完（貼圖、模型）會設 window.__LUMINA_READY__ = true；沒設就等 1.5 秒
        page.wait_for_function("window.__LUMINA_READY__ === true", timeout=15000)
    except Exception:
        pass
    page.wait_for_timeout(1500)
    page.mouse.click(640, 360)                      # 很多遊戲要先點一下才收得到鍵盤、才能開聲音
    for raw in actions:
        parts = str(raw).split()
        if not parts:
            continue
        cmd = parts[0].lower()
        if cmd == "wait":
            page.wait_for_timeout(int(float(parts[1]) * 1000))
        elif cmd == "shot":
            out = out_dir / f"web_{int(time.time() * 1000)}.png"
            page.screenshot(path=str(out))
            shots.append(f"[{' '.join(parts[1:]) or '截圖'}] {out}（1280×720）")
        elif cmd in ("hold", "press"):
            ks = [key(k) for k in parts[1].split("+")]
            secs = float(parts[2]) if cmd == "hold" else 0.08
            for k in ks:
                page.keyboard.down(k)
            page.wait_for_timeout(int(secs * 1000))
            for k in reversed(ks):
                page.keyboard.up(k)
        elif cmd == "rec":                          # rec 按鍵 秒數 [張數]：按住（或 none 不按）的同時連拍，看動作過程
            ks = [] if parts[1].lower() == "none" else [key(k) for k in parts[1].split("+")]
            secs, n = float(parts[2]), int(parts[3]) if len(parts) > 3 else 6
            for k in ks:
                page.keyboard.down(k)
            t0 = time.time()
            for i in range(n):
                target = t0 + secs * i / max(1, n - 1)
                page.wait_for_timeout(max(0, int((target - time.time()) * 1000)))
                out = out_dir / f"web_{int(time.time() * 1000)}.png"
                page.screenshot(path=str(out), scale="css")
                shots.append(f"[連拍 {parts[1]} {i + 1}/{n}，{time.time() - t0:.2f}s] {out}")
            for k in reversed(ks):
                page.keyboard.up(k)
        elif cmd == "click":                        # click x y（以 1280×720 的畫面座標）
            page.mouse.click(float(parts[1]), float(parts[2]))
        elif cmd == "drag":                         # drag dx dy：按住滑鼠拖曳（轉視角）
            page.mouse.move(640, 360)
            page.mouse.down()
            page.mouse.move(640 + float(parts[1]), 360 + float(parts[2]), steps=10)
            page.mouse.up()
    browser.close()
srv.shutdown()
print(json.dumps({"shots": shots, "errors": errors[:10], "console": console[-15:]}, ensure_ascii=False))
