"""「PTT 熱門文章爬蟲」題的評分程式（模型看不到）：在本機開一個仿 PTT 的假論壇，不連真的網站。

介面（寫在題目裡）：
    python scraper.py --base <網址> --pages <頁數> --min-push <推文門檻> --out <CSV 檔>
    CSV 要有「標題」「推文數」「網址」三個欄位（可以有其他欄位）；推文「爆」算 100，X1～XX 是負的
用法：python scraper.py <題目資料夾>
"""
import csv
import http.server
import json
import os
import random
import re
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from urllib.parse import urlparse

GAME = Path(sys.argv[1]).resolve()
PROG = GAME / "scraper.py"
PY = GAME / ".venv" / "Scripts" / "python.exe"
PY = PY if PY.exists() else Path(sys.executable)
checks, notes = {}, {}
PER_PAGE = 20


def report():
    print("BENCH_RESULT " + json.dumps({"passed": sum(checks.values()), "total": len(checks), "checks": checks,
                                        "notes": notes}, ensure_ascii=False))


# ---------------------------------------------------------------- 假論壇的資料
rnd = random.Random(11)
WORDS = ["問卦", "新聞", "爆卦", "協尋", "公告", "Re:", "討論"]
TRICKY = ["有沒有逗號, 的卦", '他說"真的假的"', "🔥燒起來了🔥", "日本語のタイトル", "  前後有空白  "]


def make_post(i):
    r = rnd.random()
    push = ("爆" if r < 0.12 else f"X{rnd.randint(1, 9)}" if r < 0.2 else "XX" if r < 0.22 else
            "" if r < 0.32 else str(rnd.randint(1, 99)))
    deleted = rnd.random() < 0.06
    title = (TRICKY[i % len(TRICKY)] if i % 9 == 0 else f"[{rnd.choice(WORDS)}] 第 {i} 篇文章的標題")
    return {"id": f"M.{1760000000 + i * 37}.A.{i:03X}", "push": push, "title": title, "deleted": deleted}


POSTS = [make_post(i) for i in range(1, 6 * PER_PAGE + 1)]          # 第一次：6 頁
NEW_POSTS = [make_post(i) for i in range(6 * PER_PAGE + 1, 6 * PER_PAGE + 9)]   # 第二次多 8 篇
PINNED = [{"id": "M.1700000000.A.AAA", "push": "爆", "title": "[公告] 八卦板板規（置底）", "deleted": False},
          {"id": "M.1700000001.A.BBB", "push": "88", "title": "[公告] 檢舉專區（置底）", "deleted": False}]
state = {"posts": POSTS, "log": [], "n": 0}
lock = threading.Lock()


def push_value(p):
    if p == "爆":
        return 100
    if p == "XX":
        return -100
    if p.startswith("X"):
        return -10 * int(p[1:])
    return int(p) if p else 0


def page_html(n, total):
    posts = state["posts"][(n - 1) * PER_PAGE: n * PER_PAGE]
    rows = []
    for p in posts:
        cls = {"爆": "hl f1"}.get(p["push"], "hl f3")
        title = (f'(本文已被刪除) [user{p["id"][-3:]}]' if p["deleted"]
                 else f'<a href="/bbs/Gossiping/{p["id"]}.html">{http.server.html.escape(p["title"])}</a>')
        rows.append(f'<div class="r-ent"><div class="nrec"><span class="{cls}">{p["push"]}</span></div>'
                    f'<div class="title">{title}</div><div class="meta"><div class="author">user</div>'
                    f'<div class="date"> 1/01</div></div></div>')
    if n == total:                                   # 最新一頁才有置底文
        rows.append('<div class="r-list-sep"></div>')
        for p in PINNED:
            rows.append(f'<div class="r-ent"><div class="nrec"><span class="hl f1">{p["push"]}</span></div>'
                        f'<div class="title"><a href="/bbs/Gossiping/{p["id"]}.html">{p["title"]}</a></div></div>')
    prev = f'<a class="btn wide" href="/bbs/Gossiping/index{n - 1}.html">‹ 上頁</a>' if n > 1 else \
        '<a class="btn wide disabled">‹ 上頁</a>'
    trap = '<a href="/private/admin.html" style="display:none">admin</a>'
    return (f'<!DOCTYPE html><html><head><meta charset="utf-8"><title>看板 Gossiping 文章列表 - 批踢踢實業坊</title></head>'
            f'<body><div id="action-bar-container"><div class="btn-group btn-group-paging">'
            f'<a class="btn wide" href="/bbs/Gossiping/index1.html">最舊</a>{prev}'
            f'<a class="btn wide disabled">下頁 ›</a><a class="btn wide" href="/bbs/Gossiping/index.html">最新</a>'
            f'</div></div><div class="r-list-container action-bar-margin bbs-screen">{"".join(rows)}</div>{trap}</body></html>')


class H(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def send(self, code, body="", ctype="text/html; charset=utf-8", headers=None):
        b = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(b)))
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        path = urlparse(self.path).path
        with lock:
            state["log"].append((time.time(), path))
            state["n"] += 1
            n = state["n"]
        if path == "/robots.txt":
            return self.send(200, "User-agent: *\nDisallow: /private/\n", "text/plain")
        if path.startswith("/private/"):
            return self.send(200, "secret")
        if n % 7 == 0:                                       # 偶爾太頻繁
            return self.send(429, "Too Many Requests", headers={"Retry-After": "1"})
        if path.startswith("/bbs/Gossiping/") and "over18=1" not in (self.headers.get("Cookie") or ""):
            return self.send(200, '<html><body><form action="/ask/over18" method="post">'
                                  f'<input type="hidden" name="from" value="{path}">'
                                  '<button name="yes" value="yes">我同意，我已年滿十八歲</button></form></body></html>')
        total = (len(state["posts"]) + PER_PAGE - 1) // PER_PAGE
        m = re.fullmatch(r"/bbs/Gossiping/index(\d*)\.html", path)
        if m:
            k = int(m.group(1)) if m.group(1) else total
            if not 1 <= k <= total:
                return self.send(404, "not found")
            return self.send(200, page_html(k, total))
        if re.fullmatch(r"/bbs/Gossiping/M\.\d+\.A\.\w+\.html", path):
            return self.send(200, "<html><body>文章內容</body></html>")
        self.send(404, "not found")

    def do_POST(self):
        path = urlparse(self.path).path
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length).decode("utf-8", "replace")
        with lock:
            state["log"].append((time.time(), "POST " + path))
        if path == "/ask/over18" and "yes=yes" in body:
            frm = re.search(r"from=([^&]+)", body)
            dest = frm.group(1).replace("%2F", "/") if frm else "/bbs/Gossiping/index.html"
            self.send_response(302)
            self.send_header("Set-Cookie", "over18=1; Path=/")
            self.send_header("Location", dest)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        self.send(404, "not found")


def expected(pages, min_push):
    total = (len(state["posts"]) + PER_PAGE - 1) // PER_PAGE
    out = {}
    for k in range(total, max(0, total - pages), -1):
        for p in state["posts"][(k - 1) * PER_PAGE: k * PER_PAGE]:
            if not p["deleted"] and push_value(p["push"]) >= min_push:
                out[f"/bbs/Gossiping/{p['id']}.html"] = p
    return out


def read_csv(path):
    raw = path.read_bytes().decode("utf-8-sig")
    rows = list(csv.DictReader(raw.splitlines()))
    return rows


def url_path(u):
    return urlparse(u.strip()).path if "://" in u else u.strip()


def run(base, out):
    return subprocess.run([str(PY), str(PROG), "--base", base, "--pages", "5", "--min-push", "30", "--out", str(out)],
                          cwd=GAME, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=300,
                          env={**os.environ, "PYTHONIOENCODING": "utf-8"})


def check(name, ok, note=""):
    checks[name] = bool(ok)
    if note and not ok:
        notes[name] = str(note)[:400]


if not PROG.exists():
    check("有 scraper.py", False)
    report()
    sys.exit()
check("有 scraper.py", True)

srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
threading.Thread(target=srv.serve_forever, daemon=True).start()
base = f"http://127.0.0.1:{srv.server_address[1]}"
tmp = Path(tempfile.mkdtemp(prefix="scr_"))
out = tmp / "熱門 文章.csv"

r = run(base, out)
check("執行成功（遇到 429 也不會當掉）", r.returncode == 0 and out.exists(), (r.stderr or r.stdout)[-400:])
rows = read_csv(out) if out.exists() else []
cols = rows[0].keys() if rows else []
check("CSV 有「標題」「推文數」「網址」欄位", rows and {"標題", "推文數", "網址"} <= set(cols), f"欄位：{list(cols)}")
exp1 = expected(5, 30)
got = {url_path(x.get("網址", "")): x for x in rows}
missing = [u for u in exp1 if u not in got]
extra = [u for u in got if u not in exp1]
check("通過 18 歲驗證、抓到最近 5 頁的熱門文章", rows and not missing, f"少了 {len(missing)} 篇，例如 {missing[:2]}")
check("沒有抓到不該抓的（置底公告、刪除文、推文不夠、X 開頭的噓文）", rows and not extra, f"多了 {len(extra)} 篇，例如 {extra[:3]}")
bad_push = [u for u, x in got.items() if u in exp1 and str(x.get("推文數", "")).strip() not in
            (str(push_value(exp1[u]["push"])), exp1[u]["push"])]
check("推文數正確（「爆」算 100）", rows and not bad_push, f"不對的例如 {[(u, got[u].get('推文數')) for u in bad_push[:3]]}")
bad_title = [u for u, x in got.items() if u in exp1 and x.get("標題", "").strip() != exp1[u]["title"].strip()]
check("標題完整（逗號、引號、表情符號、日文）", rows and not bad_title,
      f"例如 {[(got[u].get('標題'), exp1[u]['title']) for u in bad_title[:2]]}")
check("CSV 裡沒有重複的文章", len(rows) == len(got), f"{len(rows)} 列、{len(got)} 篇不同的文章")

# 第二次：論壇多了 8 篇新文章（頁數往後推一頁）
state["posts"] = POSTS + NEW_POSTS
r2 = run(base, out)
rows2 = read_csv(out) if out.exists() else []
got2 = [url_path(x.get("網址", "")) for x in rows2]
exp2 = set(exp1) | set(expected(5, 30))
check("第二次執行：只補上新的熱門文章，舊的不重複", r2.returncode == 0 and len(got2) == len(set(got2)) and set(got2) == exp2,
      f"結束碼 {r2.returncode}；{len(got2)} 列、{len(set(got2))} 篇；應該 {len(exp2)} 篇")

log = state["log"]
check("遵守 robots.txt（沒有去爬 /private/）", not any(p.startswith("/private/") for _, p in log),
      [p for _, p in log if p.startswith("/private/")][:2])
times = sorted(t for t, _ in log)
burst = max((sum(1 for u in times if t <= u < t + 1) for t in times), default=0)
check("不會爬太快（任何 1 秒內最多 8 次請求）", burst <= 8, f"最快 1 秒內 {burst} 次請求")
srv.shutdown()
report()
