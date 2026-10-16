"""「記帳網頁」題的評分程式（模型看不到）：用無頭 Edge 實際操作。用 bench/.venv 的 python 執行（裡面有 playwright）。

介面（寫在題目裡）：index.html 打開就能用；元素用 data-testid 標記：
    amount（金額輸入）、type（<select>，選項 value 是 income / expense）、category（<select> 或 <input>）、
    date（<input type="date">）、note（備註）、add（新增按鈕）、record（每一筆紀錄）、edit／delete（紀錄裡的按鈕）、
    month（<input type="month">，選要看的月份）、month-income／month-expense／month-balance（該月的數字）、
    chart（統計圖的容器，裡面是 canvas 或 svg）、export（匯出 CSV 的按鈕）
用法：python budget_web.py <題目資料夾>
"""
import functools
import http.server
import json
import re
import sys
import tempfile
import threading
from pathlib import Path

from playwright.sync_api import sync_playwright

GAME = Path(sys.argv[1]).resolve()
SHOTS = GAME / "_bench_screenshots"
checks, notes = {}, {}


def report():
    print("BENCH_RESULT " + json.dumps({"passed": sum(checks.values()), "total": len(checks), "checks": checks,
                                        "notes": notes}, ensure_ascii=False))


def check(name, fn):
    try:
        ok, note = fn()
    except Exception as e:
        ok, note = False, f"{type(e).__name__}: {e}"[:300]
    checks[name] = bool(ok)
    if note and not ok:
        notes[name] = str(note)[:300]


if not (GAME / "index.html").exists():
    checks["有 index.html"] = False
    report()
    sys.exit()
checks["有 index.html"] = True


class Quiet(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *a):
        pass


srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(Quiet, directory=str(GAME)))
threading.Thread(target=srv.serve_forever, daemon=True).start()
URL = f"http://127.0.0.1:{srv.server_address[1]}/index.html"
SHOTS.mkdir(exist_ok=True)


def num(page, tid):
    t = page.get_by_test_id(tid).inner_text()
    m = re.findall(r"-?[\d,]+(?:\.\d+)?", t.replace(" ", ""))
    return float(m[-1].replace(",", "")) if m else None


def add(page, amount, kind, date, note="", category=None):
    page.get_by_test_id("amount").fill(str(amount))
    page.get_by_test_id("type").select_option(kind)
    cat = page.get_by_test_id("category")
    if cat.evaluate("e => e.tagName") == "SELECT":
        opts = [o for o in cat.evaluate("e => [...e.options].map(o => o.value)") if o]
        if opts:
            cat.select_option(category if category in opts else opts[0])
    else:
        cat.fill(category or "餐飲")
    page.get_by_test_id("date").fill(date)
    page.get_by_test_id("note").fill(note)
    page.get_by_test_id("add").click()
    page.wait_for_timeout(300)


def count(page):
    return page.get_by_test_id("record").count()


with sync_playwright() as pw:
    browser = pw.chromium.launch(channel="msedge", headless=True)
    ctx = browser.new_context(viewport={"width": 1280, "height": 800}, accept_downloads=True)
    page = ctx.new_page()
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.on("dialog", lambda d: d.accept())
    page.goto(URL)
    page.wait_for_timeout(800)
    page.screenshot(path=str(SHOTS / "01_開啟.png"), full_page=True)
    # 有些網頁一開始會放示範資料（合理的設計）：先用網頁自己的刪除鍵清空所有月份，再開始測
    try:
        for m in ["", "2026-09", "2026-10"] + [f"2026-{k:02d}" for k in range(1, 13)]:
            if m:
                page.get_by_test_id("month").fill(m)
                page.get_by_test_id("month").dispatch_event("change")
                page.wait_for_timeout(150)
            for _ in range(60):
                rows = page.get_by_test_id("record")
                if not rows.count():
                    break
                rows.first.get_by_test_id("delete").click()
                page.wait_for_timeout(120)
    except Exception:
        pass
    check("打開沒有 JavaScript 錯誤", lambda: (not errors, errors[:2]))

    def set_month(m):
        page.get_by_test_id("month").fill(m)
        page.get_by_test_id("month").dispatch_event("change")
        page.wait_for_timeout(300)

    def c_add():
        set_month("2026-10")
        add(page, 1000, "income", "2026-10-03", "薪水", "薪水")
        add(page, 250, "expense", "2026-10-05", "午餐")
        add(page, 400, "expense", "2026-10-08", "交通")
        vals = (num(page, "month-income"), num(page, "month-expense"), num(page, "month-balance"))
        return count(page) == 3 and vals == (1000, 650, 350), f"紀錄 {count(page)} 筆；收入／支出／結餘 = {vals}（應該 1000／650／350）"
    check("新增收入、支出後當月數字正確", c_add)
    page.screenshot(path=str(SHOTS / "02_新增三筆.png"), full_page=True)

    def c_invalid():
        n = count(page)
        for bad in ["", "-50", "abc", "0"]:
            try:
                page.get_by_test_id("amount").fill(bad)
            except Exception:
                pass
            page.get_by_test_id("add").click()
            page.wait_for_timeout(200)
        return count(page) == n, f"亂填之後紀錄從 {n} 筆變成 {count(page)} 筆"
    check("亂填（空白、負數、文字、0）不會新增", c_invalid)

    def c_xss():
        page.evaluate("window.__xss = 0")
        add(page, 10, "expense", "2026-10-09", "<img src=x onerror=\"window.__xss=1\">")
        page.wait_for_timeout(300)
        hit = page.evaluate("window.__xss")
        rows = page.get_by_test_id("record")            # 刪掉這筆測試用的紀錄（依金額 10 找，不假設排在最後）
        texts = [rows.nth(i).inner_text() for i in range(rows.count())]
        hit_rows = ([i for i, t in enumerate(texts) if "onerror" in t or "<img" in t] or
                    [i for i, t in enumerate(texts) if re.search(r"(?<![\d.\-/])10(?![\d\-/])", t)])
        if hit_rows:
            rows.nth(hit_rows[0]).get_by_test_id("delete").click()
        page.wait_for_timeout(300)
        return hit != 1, "備註裡的 HTML 被執行了"
    check("備註輸入 HTML 不會被執行（安全）", c_xss)

    def c_persist():
        page.reload()
        page.wait_for_timeout(800)
        set_month("2026-10")
        return count(page) == 3 and num(page, "month-expense") == 650, f"重新整理後 {count(page)} 筆、支出 {num(page, 'month-expense')}"
    check("重新整理後資料還在", c_persist)

    def c_edit():
        rows = page.get_by_test_id("record")
        target = next(i for i in range(rows.count()) if "250" in rows.nth(i).inner_text())
        rows.nth(target).get_by_test_id("edit").click()
        page.wait_for_timeout(300)
        page.get_by_test_id("amount").fill("300")
        # 編輯可能用同一個表單（按 add 儲存）或另外的儲存鍵
        save = page.locator("[data-testid=save]")
        (save.first if save.count() else page.get_by_test_id("add")).click()
        page.wait_for_timeout(300)
        return count(page) == 3 and num(page, "month-expense") == 700, f"編輯後 {count(page)} 筆、支出 {num(page, 'month-expense')}（應該 3 筆、700）"
    check("編輯一筆後數字跟著更新", c_edit)

    def c_delete():
        rows = page.get_by_test_id("record")
        target = next(i for i in range(rows.count()) if "400" in rows.nth(i).inner_text())
        rows.nth(target).get_by_test_id("delete").click()
        page.wait_for_timeout(400)
        return count(page) == 2 and num(page, "month-expense") == 300, f"刪除後 {count(page)} 筆、支出 {num(page, 'month-expense')}"
    check("刪除一筆後數字跟著更新", c_delete)

    def c_month():
        add(page, 999, "expense", "2026-09-20", "上個月")
        set_month("2026-10")                    # 新增別的月份時，有些網頁會自動跳到那個月（合理），所以先切回來
        oct_exp = num(page, "month-expense")
        set_month("2026-09")
        sep_exp = num(page, "month-expense")
        set_month("2026-10")
        return oct_exp == 300 and sep_exp == 999, f"十月支出 {oct_exp}（應 300）、九月支出 {sep_exp}（應 999）"
    check("切換月份只算那個月", c_month)

    check("有統計圖", lambda: (page.get_by_test_id("chart").locator("canvas, svg").count() > 0
                                and page.get_by_test_id("chart").bounding_box()["height"] > 50, "找不到 canvas／svg 或高度太小"))
    page.screenshot(path=str(SHOTS / "03_統計圖.png"), full_page=True)

    def c_export():
        with page.expect_download(timeout=5000) as d:
            page.get_by_test_id("export").click()
        path = Path(tempfile.mkdtemp()) / "out.csv"
        d.value.save_as(str(path))
        text = path.read_bytes().decode("utf-8-sig", "replace")
        lines = [l for l in text.splitlines() if l.strip()]
        return len(lines) >= 4 and "999" in text and "1000" in text, f"匯出 {len(lines)} 行：{text[:150]}"
    check("匯出 CSV 有所有紀錄", c_export)

    def c_mobile():
        page.set_viewport_size({"width": 375, "height": 800})
        page.wait_for_timeout(400)
        page.screenshot(path=str(SHOTS / "04_手機.png"), full_page=True)
        w = page.evaluate("[document.documentElement.scrollWidth, window.innerWidth]")
        return w[0] <= w[1] + 1, f"手機寬度 375 時頁面寬 {w[0]}，會左右捲動"
    check("手機寬度不會跑版（沒有左右捲動）", c_mobile)
    check("整個過程沒有 JavaScript 錯誤", lambda: (not errors, errors[:2]))
    browser.close()
srv.shutdown()
report()
