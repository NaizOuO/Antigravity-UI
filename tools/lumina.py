"""內建工具 lumina：給模型用的唯讀小工具（不會修改任何檔案，頁面圖片只寫到 output\\.lumina）。

模型在對話裡用終端機執行，例如：
    lumina pdf-info "課本.pdf"              頁數、書籤目錄、印刷頁碼和 PDF 頁碼的差、是不是掃描檔
    lumina pdf-find "課本.pdf" "4.21"       全文搜尋，回傳在第幾頁和前後文
    lumina pdf-text "課本.pdf" 63-65        取出指定頁的文字（頁碼是 PDF 的第幾頁，從 1 開始）
    lumina pdf-page "課本.pdf" 63           把那一頁畫成 PNG，再用 view_file 看（公式、圖表）
    lumina pdf-thumbs "課本.pdf" 70-81      12 頁縮圖排成一張總覽，掃描檔快速找到目標頁

伺服器的 hook 只在指令完全符合這個格式、檔案在允許的資料夾裡時才自動允許（見 server.py 的 lumina_ok）。
需要 PDFium 元件（components\\pdfium，使用者同意後下載）。
"""

import re
import struct
import sys
import zlib
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "components" / "pdfium"))
PAGES_DIR = ROOT / "output" / ".lumina"
MAX_TEXT = 30000
MAX_PAGES_TEXT = 15
MAX_HITS = 40


def fail(msg, code=1):
    print(msg)
    sys.exit(code)


def open_pdf(path):
    try:
        import pypdfium2 as pdfium
    except ImportError:
        fail("LUMINA_NEED_COMPONENT pdfium：需要先下載「PDFium（讀取 PDF）」元件。介面會詢問使用者要不要下載；"
             "下載前請先用 view_file 讀檔，或請使用者稍後再試。", 3)
    p = Path(path)
    if not p.is_file():
        fail(f"找不到檔案：{p}")
    try:
        return pdfium.PdfDocument(str(p))
    except Exception as e:
        fail(f"打不開這個 PDF（{e}）")


def page_text(doc, i):
    page = doc[i]
    try:
        tp = page.get_textpage()
        try:
            return tp.get_text_bounded() or ""
        finally:
            tp.close()
    finally:
        page.close()


def parse_pages(spec, n):
    out = []
    for part in spec.split(","):
        part = part.strip()
        m = re.fullmatch(r"(\d+)(?:-(\d+))?", part)
        if not m:
            fail(f"頁碼格式不對：{part}（例如 5、5-7、5,9）")
        a, b = int(m.group(1)), int(m.group(2) or m.group(1))
        out += list(range(a, b + 1))
    bad = [p for p in out if not 1 <= p <= n]
    if bad:
        fail(f"頁碼超出範圍：{bad[:5]}（這份 PDF 共 {n} 頁）")
    return out


def page_offset(doc, n):
    """估計「PDF 第幾頁 − 印刷頁碼」：看頁首頁尾單獨出現的數字。"""
    votes = Counter()
    for i in range(min(8, n - 1), min(n, 120), 3):
        lines = [l.strip() for l in page_text(doc, i).splitlines() if l.strip()]
        for l in lines[:2] + lines[-2:]:
            for num in re.findall(r"(?<![\d.])(\d{1,4})(?![\d.])", l):
                k = i + 1 - int(num)
                if 0 <= k < 80:
                    votes[k] += 1
    if votes:
        k, c = votes.most_common(1)[0]
        if c >= 3:
            return k
    return None


def cmd_info(path):
    doc = open_pdf(path)
    n = len(doc)
    print(f"檔案：{Path(path).name}\n總頁數：{n}（以下頁碼都是 PDF 的第幾頁，從 1 開始）")
    k = page_offset(doc, n)
    if k is not None:
        print(f"印刷頁碼估計：PDF 第 X 頁 ≈ 書上印的第 X−{k} 頁（例如書上 p.63 ≈ PDF 第 {63 + k} 頁）。請用 pdf-text 確認。")
    sample = list(range(0, n, max(1, n // 24)))
    empty = sum(1 for i in sample if len(page_text(doc, i).strip()) < 30)
    if empty:
        pct = round(empty / len(sample) * 100)
        print(f"⚠ 抽查 {len(sample)} 頁，約 {pct}% 沒有文字層（掃描的圖片）：那些頁 pdf-find／pdf-text 找不到內容，"
              "要用 pdf-page 轉成圖片來看。" + ("印刷頁碼的估計也可能不準。" if pct > 50 else ""))
    toc = list(doc.get_toc(max_depth=4))
    if toc:
        print(f"\n書籤目錄（{len(toc)} 項，最多列 120 項）：")
        for item in toc[:120]:
            idx = item.page_index
            print(f"{'  ' * item.level}- {item.title}  → PDF 第 {idx + 1 if idx is not None else '?'} 頁")
    else:
        print("\n沒有書籤目錄。")


def cmd_find(path, query):
    doc = open_pdf(path)
    q = query.strip().lower()
    if not q:
        fail("搜尋的文字是空的")
    hits = 0
    for i in range(len(doc)):
        t = page_text(doc, i)
        low = t.lower()
        start = 0
        while True:
            j = low.find(q, start)
            if j < 0:
                break
            snippet = re.sub(r"\s+", " ", t[max(0, j - 90): j + len(q) + 90])
            print(f"PDF 第 {i + 1} 頁：…{snippet}…")
            hits += 1
            start = j + len(q)
            if hits >= MAX_HITS:
                print(f"（超過 {MAX_HITS} 筆，只列前面的；請用更精確的關鍵字）")
                return
    if not hits:
        print(f"找不到「{query}」。如果是掃描檔或公式，文字可能抓不到，請用 pdf-info 看目錄後用 pdf-page 看圖。")


def cmd_text(path, spec):
    doc = open_pdf(path)
    pages = parse_pages(spec, len(doc))
    if len(pages) > MAX_PAGES_TEXT:
        fail(f"一次最多 {MAX_PAGES_TEXT} 頁，請分批讀")
    out = []
    for p in pages:
        out.append(f"===== PDF 第 {p} 頁 =====\n{page_text(doc, p - 1).strip() or '（這一頁沒有文字層，請用 pdf-page 看圖）'}")
    text = "\n\n".join(out)
    print(text[:MAX_TEXT] + ("\n…（太長，後面省略；請減少頁數）" if len(text) > MAX_TEXT else ""))


def write_png(path, width, height, rgb, stride):
    raw = b"".join(b"\x00" + rgb[y * stride: y * stride + width * 3] for y in range(height))
    chunk = lambda tag, data: struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data))
    png = (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
           + chunk(b"IDAT", zlib.compress(raw, 6)) + chunk(b"IEND", b""))
    Path(path).write_bytes(png)


def cmd_page(path, spec):
    doc = open_pdf(path)
    pages = parse_pages(spec, len(doc))
    if len(pages) > 4:
        fail("一次最多 4 頁，請分批看")
    PAGES_DIR.mkdir(parents=True, exist_ok=True)
    stem = re.sub(r"[^\w\-]+", "_", Path(path).stem)[:40]
    for p in pages:
        page = doc[p - 1]
        w = page.get_width()
        scale = min(2.2, 1500 / max(w, 1))           # 長邊約 1500 像素：公式看得清楚又不會太大
        bmp = page.render(scale=scale, rev_byteorder=True, may_draw_forms=True, fill_color=(255, 255, 255, 255))
        out = PAGES_DIR / f"{stem}_p{p}.png"
        if bmp.n_channels == 3:
            write_png(out, bmp.width, bmp.height, bytes(bmp.buffer), bmp.stride)
        else:                                         # RGBA → RGB
            buf, rows = bytes(bmp.buffer), []
            for y in range(bmp.height):
                row = buf[y * bmp.stride: y * bmp.stride + bmp.width * 4]
                rows.append(bytes(b for i, b in enumerate(row) if i % 4 != 3))
            write_png(out, bmp.width, bmp.height, b"".join(rows), bmp.width * 3)
        page.close()
        print(f"PDF 第 {p} 頁的圖片：{out}　← 請用 view_file 開這個檔案來看")


def _rgb(page, width):
    """把頁面畫成指定寬度的 RGB（bytes, 寬, 高）。"""
    scale = width / max(page.get_width(), 1)
    bmp = page.render(scale=scale, rev_byteorder=True, fill_color=(255, 255, 255, 255))
    w, h, buf, st, ch = bmp.width, bmp.height, bytes(bmp.buffer), bmp.stride, bmp.n_channels
    rows = []
    for y in range(h):
        row = buf[y * st: y * st + w * ch]
        rows.append(row if ch == 3 else bytes(b for i, b in enumerate(row) if i % 4 != 3))
    return rows, w, h


def cmd_thumbs(path, spec):
    """一次把最多 12 頁縮小排成一張總覽圖（4×3），快速找出「習題在哪幾頁」，再用 pdf-page 看清楚。"""
    doc = open_pdf(path)
    pages = parse_pages(spec, len(doc))
    if len(pages) > 12:
        fail("一次最多 12 頁（例如 70-81）")
    cols, tw, gap = 4, 340, 12
    thumbs = []
    for p in pages:
        page = doc[p - 1]
        thumbs.append(_rgb(page, tw))
        page.close()
    th = max(h for _, _, h in thumbs)
    rows_n = -(-len(thumbs) // cols)
    W = cols * tw + (cols + 1) * gap
    white = b"\xff" * 3
    out_rows = []
    for r in range(rows_n):
        out_rows += [b"\xcc\xcc\xcc" * W] * gap
        cell = thumbs[r * cols:(r + 1) * cols]
        for y in range(th):
            parts = [b"\xcc\xcc\xcc" * gap]
            for c in range(cols):
                if c < len(cell):
                    rows, w, h = cell[c]
                    parts.append((rows[y] if y < h else white * w) + white * (tw - w))
                else:
                    parts.append(b"\xcc\xcc\xcc" * tw)
                parts.append(b"\xcc\xcc\xcc" * gap)
            out_rows.append(b"".join(parts))
    out_rows += [b"\xcc\xcc\xcc" * W] * gap
    PAGES_DIR.mkdir(parents=True, exist_ok=True)
    stem = re.sub(r"[^\w\-]+", "_", Path(path).stem)[:40]
    out = PAGES_DIR / f"{stem}_thumbs_{pages[0]}-{pages[-1]}.png"
    write_png(out, W, len(out_rows), b"".join(out_rows), W * 3)
    order = "、".join(f"第{i + 1}格=PDF 第 {p} 頁" for i, p in enumerate(pages))
    print(f"總覽圖：{out}　← 用 view_file 開來看（由左到右、由上到下：{order}）。縮圖只能用來「找位置」：題目、公式、數字一律要再用 pdf-page 開那一頁的清楚版本來讀，不要從縮圖抄。")


def main(argv):
    sys.stdout.reconfigure(encoding="utf-8")
    usage = "用法：lumina pdf-info 檔案 | pdf-find 檔案 關鍵字 | pdf-text 檔案 頁碼 | pdf-page 檔案 頁碼 | pdf-thumbs 檔案 頁碼範圍"
    if len(argv) < 2:
        fail(usage)
    cmd, args = argv[0], argv[1:]
    table = {"pdf-info": (cmd_info, 1), "pdf-find": (cmd_find, 2), "pdf-text": (cmd_text, 2), "pdf-page": (cmd_page, 2),
             "pdf-thumbs": (cmd_thumbs, 2)}
    if cmd not in table or len(args) != table[cmd][1]:
        fail(usage)
    table[cmd][0](*args)


if __name__ == "__main__":
    main(sys.argv[1:])
