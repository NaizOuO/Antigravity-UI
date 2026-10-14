# -*- coding: utf-8 -*-
"""本機 Antigravity CLI (agy) 介面伺服器。只用 Python 標準函式庫。

介面上的文字 / 圖片 / 檔案 -> 以 `agy -p` 子程序執行 -> 把串流結果回傳到網頁。
"""
import base64
import glob
import json
import os
import re
import subprocess
import sys
import threading
import time
import uuid
import webbrowser
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlparse

import components
import updater
import version

BOOT_ID = uuid.uuid4().hex[:8]     # 每次啟動不同；網頁用它判斷伺服器是否已經重啟

HOST, PORT = "127.0.0.1", int(os.environ.get("AGUI_PORT") or 8765)   # AGUI_PORT：開發測試用
LOCAL_HOSTS = ("127.0.0.1", "localhost", "::1")   # 只接受用這些名稱連進來的請求
ROOT = Path(__file__).resolve().parent
STATIC = ROOT / "static"
DATA = ROOT / "data"               # 所有個人資料都在這裡，分享時不要附上
WORK = DATA / "workspace"          # agy 的工作目錄
UPLOADS = WORK / "uploads"
HISTORY = DATA / "history"
PROJECTS = DATA / "projects"       # 每個專案：projects/<id>/project.json + files/
OUTPUT = ROOT / "output"           # 對話中請 AI 產生的檔案（md、csv、程式碼…）
SETTING_DIR = ROOT / "setting"     # 使用者設定：config.json、背景圖片（images/）
SETTINGS = SETTING_DIR / "config.json"
BG_DIR = SETTING_DIR / "images"
BG_EXTS = (".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp")
for p in (WORK, UPLOADS, HISTORY, PROJECTS, OUTPUT, BG_DIR):
    p.mkdir(parents=True, exist_ok=True)
# v1.3.x 以前設定存在 data/settings.json：第一次啟動新版時複製過來（舊檔保留）
_OLD_SETTINGS = DATA / "settings.json"
if not SETTINGS.exists() and _OLD_SETTINGS.exists():
    SETTINGS.write_bytes(_OLD_SETTINGS.read_bytes())

# 用 pythonw 在背景跑時沒有主控台，錯誤訊息寫進 data/server.log 方便除錯
if sys.stderr is None or sys.stdout is None:
    _log = open(DATA / "server.log", "a", encoding="utf-8", buffering=1)
    sys.stderr = sys.stdout = _log

DEFAULT_SETTINGS = {
    "instructions": "請用繁體中文回答。數學式請用 LaTeX（行內 $...$，獨立式 $$...$$）。",
    "shortcut_prompt": True,      # 第一次開啟時詢問要不要建立桌面捷徑；回答過就不再問
    # 背景（設計參考 Naiz Studio）：圖片放在 setting/images
    "bg_random": True,            # 每次開啟隨機挑一張（內建背景＋使用者的圖）；關掉就固定用 bg_image
    "bg_image": "",               # 空字串 = 不使用背景；「builtin:名稱」= 內建背景（依深淺色自動選版本）
    "bg_mode": "cover",           # cover 覆蓋／contain 完整／stretch 延展／center 置中／tile 並排／manual 自由
    "bg_alpha": 90,               # 透明度 0～100（越大越明顯）；內建背景本身就很淡，所以預設接近不透明
    "bg_blur": 0,                 # 模糊 0～20 px（Gemini 建議：背景花俏時模糊一點比較好讀）
    "bg_x": 50, "bg_y": 50,       # 置中、自由模式的位置（%）
    "bg_scale": 100,              # 自由模式的縮放（%）
    # 一般
    "send_key": "enter",          # enter：Enter 送出／ctrl_enter：Ctrl+Enter 送出、Enter 換行
    "default_model": "",          # 新對話的預設模型；空字串 = 沿用上次用的
    "default_search": False,      # 新對話預設開啟聯網搜尋
    # 外觀
    "font_size": "normal",        # small／normal／large
    "chat_width": "normal",       # narrow／normal／wide／full
    # 回覆
    "notify_sound": True,         # 回答完成時（視窗不在前面）播放提示音
    "notify_title": True,         # 回答完成時分頁標題提醒
    "latex_copy": True,           # 點公式複製 LaTeX 原始碼
    "pdf_verify": True,           # PDF 編譯後把頁面轉成圖片讓模型檢查成品，有錯自動修正
    "suggest_reply": True,       # 回答完預測使用者的下一句，輸入框按 Tab 採用（用 Gemini Flash）
    # 用量
    "quota_warn": 15,             # 目前模型的額度剩下低於幾 % 時提醒（0 = 不提醒）
    # 更新
    "update_check": True,         # 開啟時檢查新版本
}
# ---------- agy 的暫存（~/.gemini/antigravity-cli/brain/<對話 id>/） ----------
# agy 每段對話都會在 brain 裡留一個資料夾（對話紀錄、讀過的 PDF 副本、生成的圖片），不會自己清。
# 這個介面只清「自己建立的」資料夾：使用者在終端機用 agy 的對話也在同一個地方，絕不動。
AGY_BRAIN = Path.home() / ".gemini" / "antigravity-cli" / "brain"
AGY_RUNS = DATA / "agy_runs.json"          # 這個介面建立過的 agy 對話 id
AGY_RUNS_LOCK = threading.Lock()
IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".webp")
OUR_MARKERS = ("[使用者的常駐指示]", "記憶整理員", "請幫下面這段對話取一個簡短的標題", "[輸出檔案]", "[專案資料]")


def _load_runs():
    try:
        return set(json.loads(AGY_RUNS.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return set()


def remember_run(cid):
    if not cid:
        return
    with AGY_RUNS_LOCK:
        runs = _load_runs()
        if cid not in runs:
            runs.add(cid)
            AGY_RUNS.write_text(json.dumps(sorted(runs)), encoding="utf-8")


def brain_dir(cid):
    p = AGY_BRAIN / re.sub(r"[^0-9a-zA-Z-]", "", cid or "")
    return p if cid and p != AGY_BRAIN else None


def drop_brain(cid):
    """刪掉這個介面建立的某段對話在 brain 裡的資料夾（不是自己建立的不動）。"""
    p = brain_dir(cid)
    if p and p.is_dir() and (cid in _load_runs() or _looks_ours(p)):
        import shutil
        shutil.rmtree(p, ignore_errors=True)


def _looks_ours(folder):
    """舊版沒有記錄 id：看對話紀錄的開頭有沒有這個介面才會送出的固定字樣。"""
    t = folder / ".system_generated" / "logs" / "transcript.jsonl"
    try:
        with open(t, encoding="utf-8", errors="replace") as f:
            head = f.read(6000)
    except OSError:
        return False
    return any(m in head for m in OUR_MARKERS)


GENERAL_OUT = "一般對話"
TYP_SRC = ".原始檔"                  # .typ 原始檔放在各輸出資料夾的這個隱藏子資料夾，資料夾裡只看得到 PDF


def out_dir(pr, create=True):
    """這段對話的輸出資料夾：output/<專案名稱>，不屬於專案的放 output/一般對話。"""
    d = OUTPUT / (safe_name(pr["name"]).rstrip(". ") if pr else GENERAL_OUT)
    if create:
        d.mkdir(parents=True, exist_ok=True)
    return d


def typ_dir(out):
    d = out / TYP_SRC
    if not d.exists():
        d.mkdir(parents=True)
        subprocess.run(["attrib", "+h", str(d)], creationflags=NO_WINDOW, capture_output=True)
    return d


def collect_images(cid, since, out):
    """這輪回答中 Nano Banana（agy 的生圖子代理）生成的圖片：搬到 output，回傳新路徑。
    agy 會把圖存在 brain/<對話>/ 下（使用者上傳與暫存的 .user_uploaded、.tempmediaStorage 不算）。"""
    p = brain_dir(cid)
    if not p or not p.is_dir():
        return []
    moved = []
    for f in p.rglob("*"):
        rel = f.relative_to(p).parts
        if (f.is_file() and f.suffix.lower() in IMAGE_EXTS and f.stat().st_mtime >= since - 2
                and not any(part.startswith(".") for part in rel)):
            dest, n = out / f.name, 2
            while dest.exists():
                dest = out / f"{f.stem} ({n}){f.suffix}"
                n += 1
            try:
                import shutil
                shutil.move(str(f), str(dest))
                moved.append(dest)
            except OSError:
                pass
    return moved


def agy_storage():
    """brain 資料夾的統計：這個介面的（還在用／可清理）與其他（終端機的，不動）。"""
    if not AGY_BRAIN.is_dir():
        return {"exists": False}
    live = {p.stem for p in HISTORY.glob("*.json")}
    runs = _load_runs()
    stat = {"in_use": [0, 0], "cleanable": [0, 0], "others": [0, 0]}
    for d in AGY_BRAIN.iterdir():
        if not d.is_dir():
            continue
        size = sum(f.stat().st_size for f in d.rglob("*") if f.is_file())
        ours = d.name in runs or _looks_ours(d)
        key = "others" if not ours else ("in_use" if d.name in live else "cleanable")
        stat[key][0] += 1
        stat[key][1] += size
    return {"exists": True, "path": str(AGY_BRAIN),
            **{k: {"count": c, "size": human_size(s)} for k, (c, s) in stat.items()}}


def clean_agy_storage():
    """清掉「這個介面建立、但對話已經不在」的 brain 資料夾（包含記憶整理、取標題用的暫時對話）。"""
    import shutil
    live = {p.stem for p in HISTORY.glob("*.json")}
    runs = _load_runs()
    removed = 0
    for d in AGY_BRAIN.iterdir() if AGY_BRAIN.is_dir() else []:
        if d.is_dir() and d.name not in live and (d.name in runs or _looks_ours(d)):
            shutil.rmtree(d, ignore_errors=True)
            removed += 1
    return removed


# ---------- Skills：程式附帶的 skills/ 同步到每個 agy 工作資料夾的 .agents/skills/ ----------
SKILLS_SRC = ROOT / "skills"


def sync_skills(target):
    """agy 只會讀工作資料夾裡的 .agents/skills：把程式附帶的 skill 複製進去（更新後也會換成新版）。"""
    import shutil
    if not SKILLS_SRC.is_dir():
        return
    dest = Path(target) / ".agents" / "skills"
    for skill in SKILLS_SRC.iterdir():
        if skill.is_dir():
            shutil.rmtree(dest / skill.name, ignore_errors=True)
            shutil.copytree(skill, dest / skill.name)


def sync_all_skills():
    sync_skills(WORK)
    for p in PROJECTS.iterdir():
        if p.is_dir():
            sync_skills(p)


# ---------- PDF：AI 寫好 .typ，伺服器編譯（AI 不需要執行任何指令） ----------
INSTALLING = {}                    # 元件 id -> 取消用的 Event


def new_typ_files(since):
    """這輪新寫的 .typ（任何輸出資料夾）。模型若沒照指示直接存在輸出資料夾，先搬進隱藏的原始檔資料夾。"""
    found = []
    for p in OUTPUT.rglob("*.typ"):
        if p.stat().st_mtime < since - 2:
            continue
        if p.parent.name != TYP_SRC:
            dest = typ_dir(p.parent) / p.name
            try:
                p.replace(dest)
                p = dest
            except OSError:
                pass
        found.append(p)
    return sorted(found)


def tidy_output():
    """舊版把 .typ 和 PDF 混在 output 最上層：把 .typ 收進隱藏的原始檔資料夾（PDF 等其他檔案不動）。"""
    for p in OUTPUT.glob("*.typ"):
        try:
            p.replace(typ_dir(OUTPUT) / p.name)
        except OSError:
            pass


def pdf_path(typ):
    """原始檔在 <資料夾>/.原始檔/x.typ → PDF 放 <資料夾>/x.pdf。"""
    typ = Path(typ)
    return (typ.parent.parent if typ.parent.name == TYP_SRC else typ.parent) / (typ.stem + ".pdf")


def compile_files(paths):
    """編譯多個 .typ：[{typ, ok, pdf | error}]。只接受 output 資料夾裡的 .typ。"""
    results = []
    for p in map(Path, paths):
        if p.suffix.lower() != ".typ" or not p.is_file() or not inside(p, OUTPUT):
            continue
        ok, res = components.compile_typst(p, pdf_path(p))
        results.append({"typ": str(p), "name": p.name, "ok": ok,
                        **({"pdf": str(res), "pdf_name": Path(res).name} if ok else {"error": str(res)})})
    return results


FIX_PROMPT = """[介面自動訊息，不是使用者說的話] 你剛才寫的 {path} 用 Typst 編譯時發生錯誤。
請直接修正同一個檔案（用 replace_file_content 或 write_to_file），不要執行任何指令。
修正完只要回一句「已修正」，不需要其他說明。

錯誤訊息：
{error}
"""


def fix_typst(cid, cwd, model, result):
    """在背景請模型依錯誤修正 .typ（同一段 agy 對話，但不出現在介面的對話裡）。"""
    cmd = [agy_exe(), "-p", FIX_PROMPT.format(path=result["typ"], error=result["error"][-2500:]),
           "--output-format", "json", "--conversation", cid,
           "--add-dir", str(UPLOADS), "--add-dir", str(OUTPUT), "--mode", "accept-edits"]
    if model:
        cmd += ["--model", model]
    try:
        subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                       creationflags=NO_WINDOW, timeout=600)
    except Exception:
        pass


VERIFY_PROMPT = """你是排版校對員。使用者的要求是：
<<<
{request}
>>>
依這個要求產生的 {name} 已經編譯成 PDF，實際共 {pages} 頁，原始檔是 {typ}。
不要相信 .typ 原始碼「看起來」的樣子，請用 view_file 逐一打開下面每一頁的圖片，檢查真正的成品：
{images}

檢查項目：
1. 頁數與分頁是否符合使用者的要求（實際是 {pages} 頁；有要求頁數就要對得上，不能有多出來、幾乎空白或被擠到下一頁的內容）。
2. 版面：模板、標題、表格、方框、圖片有沒有跑掉、重疊、超出頁面或被截斷。
3. 公式：括號、上下標、分數的範圍有沒有放錯，符號是否正確顯示（不是原始碼文字）。
4. 內容：有沒有錯字、漏掉的段落、物理或數學上的錯誤。

有問題就直接修正 {typ}（用 replace_file_content 或 write_to_file），不要執行任何指令。
最後只回一行：
- 沒有問題：OK
- 有修正：FIXED: <用一句繁體中文說明修了什麼>
"""


def verify_pdf(cwd, model, result, request):
    """把 PDF 每頁轉成圖片，請模型看成品檢查；回傳 (是否有修改, 說明)。
    用獨立的新對話（只附使用者的要求），不沿用原對話，避免把整段對話再送一次、浪費額度。"""
    typ = Path(result["typ"])
    shots = components.render_pages(typ, typ.parent / f".檢查_{typ.stem}")
    if not shots:
        return False, ""
    prompt = VERIFY_PROMPT.format(name=result["pdf_name"], pages=len(shots), typ=typ, request=request[-3000:],
                                  images="\n".join(str(p) for p in shots))
    want = re.findall(r"(\d+)\s*頁", request)        # 使用者有指定頁數：程式直接比對，不靠模型自己發現
    if len(set(want)) == 1 and int(want[0]) != len(shots):
        prompt = (f"⚠ 頁數不符：使用者要求 {want[0]} 頁，實際編譯出來是 {len(shots)} 頁，一定要修正到 {want[0]} 頁。\n\n"
                  + prompt)
    cmd = [agy_exe(), "-p", prompt,
           "--output-format", "json",
           "--add-dir", str(UPLOADS), "--add-dir", str(OUTPUT), "--mode", "accept-edits"]
    if model:
        cmd += ["--model", model]
    before = typ.stat().st_mtime
    try:
        out = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                             creationflags=NO_WINDOW, timeout=900).stdout
        data = json.loads(out)
        answer = (data.get("response") or "").strip()
        if data.get("conversation_id"):
            drop_brain(data["conversation_id"])
    except Exception:
        answer = ""
    finally:
        import shutil
        shutil.rmtree(shots[0].parent, ignore_errors=True)
    changed = typ.stat().st_mtime != before
    note = answer.split("FIXED:", 1)[1].strip().splitlines()[0] if "FIXED:" in answer else ""
    return changed, note


def error_summary(error):
    """錯誤訊息的第一行，給使用者看「剛剛為什麼重新產生」。"""
    first = next((l.strip() for l in error.splitlines() if l.strip().startswith("error")), error.strip().splitlines()[0] if error.strip() else "")
    return first.replace("error:", "").strip()[:120]


def pdf_links(results):
    ok = [r for r in results if r["ok"]]
    return "\n".join(f"📄 已產生 PDF：[{r['pdf_name']}](file:///{quote(Path(r['pdf']).as_posix(), safe=':/')})" for r in ok)


# ---------- 匯出、備份、匯入 ----------
def export_markdown(h, out=OUTPUT):
    """把一段對話存成 Markdown 到 output 資料夾，回傳檔案路徑。"""
    fmt = lambda t: datetime.fromtimestamp(t or time.time()).strftime("%Y-%m-%d %H:%M")
    lines = [f"# {h.get('title') or '對話'}", "",
             f"> 建立：{fmt(h.get('created'))}　最後更新：{fmt(h.get('updated'))}　模型：{h.get('model') or ''}", ""]
    for m in h.get("messages", []):
        lines.append("## 🙋 我" if m["role"] == "user" else "## 🤖 AI")
        lines += ["", m.get("text") or ""]
        if m.get("files"):
            lines.append("\n附件：" + "、".join(Path(f).name for f in m["files"]))
        lines.append("")
    name = safe_name(re.sub(r"\s+", " ", h.get("title") or "對話"))[:60]
    dest = out / f"{name}.md"
    n = 2
    while dest.exists():
        dest = out / f"{name} ({n}).md"
        n += 1
    dest.write_text("\n".join(lines), encoding="utf-8")
    return dest


def backup_all():
    """把 data/（對話、專案、附件）和 setting/（設定、背景）打包成 output/備份_日期.zip。"""
    import zipfile
    dest = OUTPUT / f"Antigravity備份_{datetime.now():%Y%m%d_%H%M}.zip"
    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as zf:
        for base in (DATA, SETTING_DIR):
            for p in base.rglob("*"):
                if p.is_file() and p.name not in ("server.log", "update_download.zip"):
                    zf.write(p, p.relative_to(ROOT).as_posix())
    return dest


def import_backup(raw):
    """匯入備份 zip：只補上「目前沒有」的檔案，已經存在的一律不覆蓋。回傳 (新增數, 略過數)。"""
    import io
    import zipfile
    added = skipped = 0
    with zipfile.ZipFile(io.BytesIO(raw)) as zf:
        for name in zf.namelist():
            p = Path(name)
            if name.endswith("/") or p.is_absolute() or ".." in p.parts or p.parts[0] not in ("data", "setting"):
                continue
            dest = ROOT / p
            if dest.exists() or not inside(dest, ROOT):
                skipped += 1
                continue
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(zf.read(name))
            added += 1
    MODELS_CACHE.update(t=0, data=None)
    return added, skipped


def _explorer_windows():
    """目前所有檔案總管視窗：{hwnd: 標題}。"""
    import ctypes
    from ctypes import wintypes
    user32, found = ctypes.windll.user32, {}

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def each(hwnd, _):
        cls = ctypes.create_unicode_buffer(64)
        user32.GetClassNameW(hwnd, cls, 64)
        if cls.value == "CabinetWClass" and user32.IsWindowVisible(hwnd):
            title = ctypes.create_unicode_buffer(260)
            user32.GetWindowTextW(hwnd, title, 260)
            found[hwnd] = title.value
        return True
    user32.EnumWindows(each, 0)
    return found


def bring_to_front(hwnd):
    """背景程式把視窗切到最前面：暫時接上目前前景視窗的輸入執行緒再切換；
    就算 Windows 還是不給焦點，也先設成「最上層」再取消，至少視窗會出現在瀏覽器前面。"""
    import ctypes
    user32, kernel32 = ctypes.windll.user32, ctypes.windll.kernel32
    SWP = 0x0001 | 0x0002 | 0x0040                     # NOSIZE | NOMOVE | SHOWWINDOW
    user32.ShowWindow(hwnd, 9)                         # SW_RESTORE（最小化時也還原）
    fg = user32.GetForegroundWindow()
    fg_thread = user32.GetWindowThreadProcessId(fg, None)
    me = kernel32.GetCurrentThreadId()
    attached = bool(fg_thread) and fg_thread != me and user32.AttachThreadInput(me, fg_thread, True)
    try:
        user32.SetWindowPos(hwnd, -1, 0, 0, 0, 0, SWP)    # HWND_TOPMOST
        user32.SetWindowPos(hwnd, -2, 0, 0, 0, 0, SWP)    # HWND_NOTOPMOST：保持在前面但不一直置頂
        user32.BringWindowToTop(hwnd)
        user32.SetForegroundWindow(hwnd)
    finally:
        if attached:
            user32.AttachThreadInput(me, fg_thread, False)


def open_folder_front(folder):
    """打開資料夾並把檔案總管視窗拉到最前面。
    伺服器是背景程式，Windows 不讓它開的視窗搶到前景（會被瀏覽器蓋住），
    所以開完後找到那個視窗，用「先送一個 Alt 鍵」的方式取得前景權限再切過去。"""
    if os.name != "nt":
        return
    try:
        before = _explorer_windows()
    except Exception:
        before = {}
    subprocess.Popen(["explorer", str(folder)], creationflags=NO_WINDOW)

    def bring_front():
        import ctypes
        user32 = ctypes.windll.user32
        for _ in range(30):                     # 最多等 3 秒讓視窗出現
            time.sleep(0.1)
            try:
                wins = _explorer_windows()
            except Exception:
                return
            new = [h for h in wins if h not in before]
            # 已經開著同一個資料夾時 explorer 會沿用舊視窗；標題像「output - 檔案總管」
            same = [h for h, t in wins.items() if t == folder.name or t.startswith(folder.name + " - ")]
            target = (new or same or [None])[0]
            if target:
                bring_to_front(target)
                return
    threading.Thread(target=bring_front, daemon=True).start()


# 內建背景（tools/make_backgrounds.py 產生）：每張都有深色、淺色版本
BUILTIN_BG = [{"id": "wave", "name": "波的干涉"}, {"id": "contour", "name": "等高線"},
              {"id": "bloch", "name": "布洛赫球"}, {"id": "dusk", "name": "暮色"},
              {"id": "field", "name": "電偶極場線"}]
FOLDERS = {"output": OUTPUT, "images": BG_DIR, "setting": SETTING_DIR, "data": DATA, "agy": AGY_BRAIN}
OUTPUT_HINT = ("\n\n[輸出檔案] 如果使用者要你產生檔案（例如 md、txt、csv、程式碼），請用 write_to_file 存到 {dir}\\ 資料夾，"
               "檔名取有意義的名字（可以用中文），完成後告訴使用者檔名。不要存到其他地方。"
               "要產生 PDF 時，.typ 原始檔請存到 {dir}\\" + TYP_SRC + "\\ 資料夾（介面會把 PDF 編譯到 {dir}\\）。"
               "如果用生圖工具產生了圖片，不需要另外複製或搬移檔案（不要用指令），介面會自動取回並顯示。")

# ---------- 桌面捷徑 ----------
SHORTCUT_NAME = "Antigravity UI.lnk"


def desktop_dir():
    """真正的桌面位置（桌面被 OneDrive 接管時會在 OneDrive 裡）。"""
    try:
        import ctypes
        buf = ctypes.create_unicode_buffer(260)
        ctypes.windll.shell32.SHGetFolderPathW(None, 0x10, None, 0, buf)   # CSIDL_DESKTOPDIRECTORY
        if buf.value:
            return Path(buf.value)
    except Exception:
        pass
    return Path.home() / "Desktop"


def create_shortcut():
    """在桌面建立「Antigravity UI」捷徑：用 wscript 執行 啟動.vbs，圖示用 static/icon.ico。已存在就覆蓋（例如資料夾搬過）。"""
    script = ("$ws = New-Object -ComObject WScript.Shell; "
              "$s = $ws.CreateShortcut($env:AGUI_LNK); "
              "$s.TargetPath = Join-Path $env:WINDIR 'System32\\wscript.exe'; "
              "$s.Arguments = '\"' + $env:AGUI_VBS + '\"'; "
              "$s.WorkingDirectory = $env:AGUI_DIR; "
              "$s.IconLocation = $env:AGUI_ICO + ',0'; "
              "$s.Description = 'Antigravity UI'; "
              "$s.Save()")
    lnk = desktop_dir() / SHORTCUT_NAME
    env = dict(os.environ, AGUI_LNK=str(lnk), AGUI_VBS=str(ROOT / "啟動.vbs"),
               AGUI_DIR=str(ROOT), AGUI_ICO=str(STATIC / "icon.ico"))
    r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script], env=env,
                       capture_output=True, text=True, encoding="utf-8", errors="replace",
                       creationflags=NO_WINDOW, timeout=30)
    if r.returncode != 0 or not lnk.exists():
        raise RuntimeError((r.stderr or r.stdout or "未知的錯誤").strip()[:300])
    return lnk
SEARCH_HINT = "\n\n[請先用 search_web 上網搜尋最新資料再回答，並在最後列出參考來源網址。]"


def load_settings():
    try:
        # utf-8-sig：使用者用記事本編輯過的檔案開頭可能有 BOM
        s = {**DEFAULT_SETTINGS, **json.loads(SETTINGS.read_text(encoding="utf-8-sig"))}
    except (OSError, ValueError):
        return dict(DEFAULT_SETTINGS)
    if s.get("default_model"):           # 預設模型已下架：改成替代模型（用已經抓到的清單，不在這裡呼叫 agy）
        s["default_model"] = replacement_model(s["default_model"], available_ids(fetch=False))
    return s


def get_usage():
    out = subprocess.run([agy_exe(), "-p", "/usage", "--output-format", "json"], cwd=WORK,
                         capture_output=True, text=True, encoding="utf-8", errors="replace",
                         creationflags=NO_WINDOW, timeout=60).stdout
    data = json.loads(out)
    return (data.get("command") or {}).get("data") or {}

NO_WINDOW = 0x08000000 if os.name == "nt" else 0   # CREATE_NO_WINDOW：不跳出黑色視窗
MAX_ARG = 24000                                   # Windows 命令列長度保守上限

RUNS = {}            # run_id -> Popen
RUNS_LOCK = threading.Lock()
STOPPED = set()      # 使用者按了停止的 run_id（自動重試時就不再重試）
MODEL_RETRIES = 5    # 模型被 agy 判定「不存在」時自動重試的次數
MODELS_CACHE = {"t": 0, "data": None}


def agy_exe():
    import shutil
    local = Path(os.environ.get("LOCALAPPDATA", "")) / "agy" / "bin" / "agy.exe"
    if local.exists():
        return str(local)
    return shutil.which("agy") or "agy"


AGY_MISSING = ("找不到 Antigravity CLI（agy）。請先安裝 agy，並在終端機執行一次 `agy` 完成 Google 帳號登入，"
               "然後重新啟動這個介面（關閉.bat → 啟動.vbs）。")


def agy_installed():
    import shutil
    return Path(agy_exe()).exists() or shutil.which("agy") is not None


def list_models(refresh=False):
    # agy 自動更新後可用模型可能會變（例如 Gemini 3.1 Pro 被拿掉），所以快取只留 2 分鐘，開頁面時也會強制重抓
    if not refresh and MODELS_CACHE["data"] and time.time() - MODELS_CACHE["t"] < 120:
        return MODELS_CACHE["data"]
    out = subprocess.run([agy_exe(), "models"], capture_output=True, text=True,
                         encoding="utf-8", errors="replace", creationflags=NO_WINDOW,
                         timeout=60).stdout
    models = []
    for line in out.splitlines():
        if "\t" in line:
            mid, name = line.split("\t", 1)
            models.append({"id": mid.strip(), "name": name.strip()})
    # 只提供 agy 官方清單裡的模型（Gemini 3.1 Pro 已被 Google 從一般方案移除，不再補回）
    if models:
        MODELS_CACHE.update(t=time.time(), data=models)
    return models


FALLBACK_MODEL = "gemini-3.8-flash-high"
RETIRED_MODELS = ("gemini-3.1-pro",)     # 已確定下架：連清單都還沒抓到時也直接換掉


def _model_version(mid):
    return [int(n) for n in re.findall(r"\d+", mid)]


def replacement_model(mid, ids):
    """模型被下架時的替代：同系列最新版優先（Opus → 較新的 Opus → Sonnet），其次 Gemini Flash。
    例如 11/2 Claude 4.6 下架後，claude-opus-4-6-thinking 會換成清單裡的 claude-opus-5-5-…。"""
    if not mid or mid in ids:
        return mid
    if not ids:
        return FALLBACK_MODEL if mid.startswith(RETIRED_MODELS) else mid
    level = (re.search(r"-(low|medium|high)$", mid) or [None, "high"])[1]
    order = {"claude-opus": ["claude-opus", "claude-sonnet"],
             "claude-sonnet": ["claude-sonnet", "claude-opus"]}
    for prefix in next((v for k, v in order.items() if mid.startswith(k)), []) + ["gemini"]:
        same = [i for i in ids if i.startswith(prefix)]
        if prefix == "gemini":           # Gemini：優先 Flash、同樣的思考強度
            same.sort(key=lambda i: ("flash" in i, i.endswith("-" + level), _model_version(i)), reverse=True)
        else:                            # Claude：最新版、有 thinking 的優先
            same.sort(key=lambda i: (_model_version(i), "thinking" in i), reverse=True)
        if same:
            return same[0]
    return ids[0]


def available_ids(fetch=True):
    try:
        data = (list_models() if fetch else None) or MODELS_CACHE["data"] or []
    except (OSError, subprocess.SubprocessError):
        data = MODELS_CACHE["data"] or []
    return [m["id"] for m in data]


def forget_model(mid):
    MODELS_CACHE.update(t=0, data=None)


def safe_name(name):
    name = re.sub(r'[\\/:*?"<>|\x00-\x1f]', "_", name).strip() or "file"
    return name[-120:]


def inside(path, base):
    try:
        Path(path).resolve().relative_to(base.resolve())
        return True
    except ValueError:
        return False


def human_size(n):
    for unit in ("B", "KB", "MB"):
        if n < 1024:
            return f"{n:.0f} {unit}"
        n /= 1024
    return f"{n:.1f} GB"


# ---------- 專案 ----------
def proj_dir(pid):
    return PROJECTS / re.sub(r"[^0-9a-f]", "", pid or "")


def load_project(pid):
    p = proj_dir(pid) / "project.json"
    if not pid or not p.exists():
        return None
    return json.loads(p.read_text(encoding="utf-8"))


def save_project(proj):
    d = proj_dir(proj["id"])
    (d / "files").mkdir(parents=True, exist_ok=True)
    (d / "project.json").write_text(json.dumps(proj, ensure_ascii=False, indent=1), encoding="utf-8")


def project_files(pid):
    d = proj_dir(pid) / "files"
    if not d.exists():
        return []
    fs = [f for f in d.iterdir() if f.is_file()]
    fs.sort(key=lambda f: f.stat().st_mtime)
    return [{"name": f.name, "path": str(f), "size": f.stat().st_size, "size_text": human_size(f.stat().st_size)} for f in fs]


def project_block(proj, cid=None):
    files = project_files(proj["id"])
    if not files:
        block = f"\n\n[專案] 這個對話屬於專案「{proj['name']}」，目前專案裡還沒有檔案。"
    else:
        lst = "\n".join(f"- {f['path']}（{f['size_text']}）" for f in files)
        block = (f"\n\n[專案資料] 這個對話屬於專案「{proj['name']}」，專案資料夾裡有以下檔案（使用者可能隨時增減）：\n{lst}\n"
                 "請依照問題判斷需要哪些檔案，用 view_file 讀取相關的檔案即可，不需要每次全部讀；"
                 "已經讀過且內容還記得的不必重讀。回答時請註明引用的檔名與頁碼。")
    conv_dir = proj_dir(proj["id"]) / "conversations"
    others = [p for p in conv_dir.glob("*.md") if p.stem != (cid or "")] if conv_dir.exists() else []
    if others:
        others.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        shown = others[:TRANSCRIPT_LIST_MAX]
        lst = "\n".join(f"- {p}（{transcript_title(p)}，{datetime.fromtimestamp(p.stat().st_mtime):%Y-%m-%d}）" for p in shown)
        more = f"\n（另有 {len(others) - len(shown)} 段較舊的對話也在同一個資料夾）" if len(others) > len(shown) else ""
        block += (f"\n\n[過去的專案對話] 專案記憶只有重點。當使用者提到「之前討論過的」內容、或需要記憶裡沒有的細節時，"
                  f"請從下面的過去對話紀錄挑標題相關的，用 view_file 讀取（不要用 run_command 或終端機指令）。用不到時不要讀。\n{lst}{more}")
    return block


TRANSCRIPT_LIST_MAX = 30          # 提示裡最多列出幾段過去的對話（最新的優先）


def transcript_title(path):
    try:
        with open(path, encoding="utf-8") as f:
            return f.readline().lstrip("# ").strip()[:60] or "（未命名）"
    except OSError:
        return "（未命名）"


# ---------- 專案對話紀錄（給模型搜尋用的 Markdown 副本） ----------
def transcript_path(pid, cid):
    return proj_dir(pid) / "conversations" / f"{re.sub(r'[^0-9a-zA-Z-]', '', cid)}.md"


def write_transcript(h):
    """把屬於專案的對話存一份 Markdown 到專案資料夾；不屬於專案或專案不存在就略過。"""
    pid = h.get("project")
    if not pid or not load_project(pid):
        return
    fmt = lambda t: datetime.fromtimestamp(t or 0).strftime("%Y-%m-%d %H:%M")
    lines = [f"# {h.get('title') or '（未命名）'}", "",
             f"對話 ID：{h['id']}　建立：{fmt(h.get('created'))}　最後更新：{fmt(h.get('updated'))}", ""]
    for m in h.get("messages", []):
        lines.append("## 使用者" if m["role"] == "user" else "## AI")
        lines.append(m.get("text") or "")
        if m.get("files"):
            lines.append("（附件：" + "、".join(Path(f).name for f in m["files"]) + "）")
        lines.append("")
    p = transcript_path(pid, h["id"])
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("\n".join(lines), encoding="utf-8")


def remove_transcript(h):
    if h and h.get("project"):
        transcript_path(h["project"], h["id"]).unlink(missing_ok=True)


def sync_transcripts():
    """啟動時補齊：舊版建立的專案對話還沒有 Markdown 副本，或副本比紀錄舊。"""
    for p in HISTORY.glob("*.json"):
        try:
            h = json.loads(p.read_text(encoding="utf-8"))
            if not h.get("project"):
                continue
            t = transcript_path(h["project"], h["id"])
            if not t.exists() or t.stat().st_mtime < p.stat().st_mtime:
                write_transcript(h)
        except Exception:
            pass


# ---------- 專案記憶（像 claude.ai Projects：同一個專案的對話共享長期重點） ----------
MEMORY_MODEL = "gemini-3.8-flash-low"      # 整理記憶用便宜快速的模型
MEMORY_LOCKS = {}
MEMORY_PROMPT = """你是專案「{name}」的記憶整理員。這份記憶會在這個專案的每個新對話開頭提供給 AI，讓它記得之前的對話。

請根據「目前的記憶」和「新的對話內容」，輸出更新後的完整記憶：
- 用繁體中文 Markdown 條列，可以分幾個小標題（例如：使用者背景與偏好、專案目標、已確認的結論與數值、檔案重點、待解決的問題）
- 只保留之後對話會用到、長期有效的資訊；一次性的閒聊、寒暄、AI 自己的解說過程都不要記
- 新資訊和舊記憶衝突時以新的為準；過時的刪掉；總長度控制在 40 條、{limit} 字以內
- 細節不必全記：過去的對話紀錄另外存在專案資料夾，需要時 AI 可以搜尋，記憶只要記重點
- 沒有值得記的新資訊時，原樣輸出目前的記憶
- 只輸出記憶本身，不要任何前言或結語，也不要使用任何工具

## 目前的記憶
{memory}

## 新的對話內容
{material}
"""


def memory_model():
    try:
        ids = [m["id"] for m in list_models()]
    except Exception:
        return MEMORY_MODEL
    if MEMORY_MODEL in ids:
        return MEMORY_MODEL
    return next((i for i in ids if "flash" in i and i.endswith("-low")), None)


MEMORY_MAX = 3000          # 記憶的硬上限（字數）；每個新對話都會帶上它，太長會一直多花 token
COMPRESS_PROMPT = """下面是專案「{name}」的記憶，目前 {length} 字，超過上限 {limit} 字。
請把它壓縮到 {target} 字以內：合併重複的條目、刪掉較不重要或已過時的細節，保留使用者背景與偏好、專案目標、已確認的結論與數值、待解決的問題。
格式維持繁體中文 Markdown 條列。只輸出壓縮後的記憶本身，不要任何前言或結語，也不要使用任何工具。

{memory}
"""


def run_agy_text(prompt, name):
    """用記憶模型跑一次 agy，回傳文字結果（失敗時丟出 RuntimeError）。"""
    if len(prompt) > MAX_ARG:      # 內容太長：存成檔案讓模型讀
        f = WORK / f"{name}.md"
        f.write_text(prompt, encoding="utf-8")
        prompt = f"請用 view_file 完整讀取這個檔案，並嚴格依照檔案裡的指示輸出：\n- {f}"
    cmd = [agy_exe(), "-p", prompt, "--output-format", "json"]
    model = memory_model()
    if model:
        cmd += ["--model", model]
    try:
        out = subprocess.run(cmd, cwd=WORK, capture_output=True, text=True, encoding="utf-8",
                             errors="replace", creationflags=NO_WINDOW, timeout=300).stdout
        r = json.loads(out)
    except Exception as e:
        raise RuntimeError(f"呼叫 agy 失敗：{e}")
    # 記憶整理、取標題這種一次性的呼叫：agy 留下的對話資料夾用不到，直接清掉
    remember_run(r.get("conversation_id")); drop_brain(r.get("conversation_id"))
    text = (r.get("response") or "").strip()
    if r.get("status") != "SUCCESS" or not text:
        raise RuntimeError("模型沒有回傳內容")
    return text


def clip_memory(text, limit=MEMORY_MAX):
    """最後保險：超過上限時從最後一個完整的行截斷。"""
    if len(text) <= limit:
        return text
    note = "\n- …（記憶已達上限，較後面的內容被截斷）"
    cut = text[:limit - len(note)]
    return cut[:cut.rfind("\n")].rstrip() + note if "\n" in cut else cut + note


def fit_memory(name, text):
    """超過上限就請模型壓縮一次；還是太長才截斷。"""
    if len(text) <= MEMORY_MAX:
        return text
    try:
        text = run_agy_text(COMPRESS_PROMPT.format(name=name, length=len(text), limit=MEMORY_MAX,
                                                   target=int(MEMORY_MAX * 0.8), memory=text), "memory_compress")
    except RuntimeError:
        pass
    return clip_memory(text)


def update_memory(pid, material):
    """用 material（對話內容）更新專案記憶；同一個專案一次只跑一個。回傳 (ok, 訊息)。"""
    lock = MEMORY_LOCKS.setdefault(pid, threading.Lock())
    with lock:
        pr = load_project(pid)
        if not pr:
            return False, "專案不存在"
        prompt = MEMORY_PROMPT.format(name=pr["name"], memory=pr.get("memory") or "（目前沒有記憶）",
                                      material=material, limit=MEMORY_MAX)
        try:
            text = fit_memory(pr["name"], run_agy_text(prompt, f"memory_{pid}"))
        except RuntimeError as e:
            return False, f"整理記憶失敗：{e}"
        pr = load_project(pid) or pr       # 期間使用者可能改過名稱或指示
        pr.update(memory=text, memory_updated=time.time())
        save_project(pr)
        return True, "ok"


def turn_material(user_text, reply):
    reply = reply if len(reply) < 8000 else reply[:8000] + "\n…（後略）"
    return f"使用者：{user_text}\n\nAI：{reply}"


def all_conversations_material(pid):
    parts = []
    for p in sorted(HISTORY.glob("*.json"), key=lambda p: p.stat().st_mtime):
        try:
            h = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        if h.get("project") != pid:
            continue
        parts.append(f"### 對話：{h.get('title')}")
        for m in h["messages"]:
            t = m.get("text") or ""
            parts.append(("使用者：" if m["role"] == "user" else "AI：") + (t if len(t) < 4000 else t[:4000] + "…"))
    return "\n\n".join(parts)[-200000:]


def build_prompt(text, files, instructions, extra=""):
    parts = []
    if instructions:
        parts.append(f"[使用者的常駐指示]\n{instructions}\n")
    parts.append((text or "") + extra)
    if files:
        lst = "\n".join(f"- {f}" for f in files)
        parts.append("\n\n[附件] 請先用 view_file 讀取以下檔案（圖片、PDF 等），再回答上面的問題：\n" + lst)
    prompt = "\n".join(parts)
    if len(prompt) > MAX_ARG:   # 太長就存成檔案讓 agy 自己讀
        f = UPLOADS / f"{datetime.now():%Y%m%d}" / f"prompt_{uuid.uuid4().hex[:8]}.md"
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(prompt, encoding="utf-8")
        prompt = f"使用者的訊息很長，已存成檔案。請先用 view_file 完整讀取這個檔案，並依照其中內容回覆：\n- {f}"
    return prompt


# ---------- 對話紀錄 ----------
def hist_path(cid):
    return HISTORY / f"{re.sub(r'[^0-9a-zA-Z-]', '', cid)}.json"


def load_hist(cid):
    p = hist_path(cid)
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


HIST_LOCK = threading.Lock()      # 背景產生標題和存對話可能同時寫同一個檔


def write_hist(h):
    hist_path(h["id"]).write_text(json.dumps(h, ensure_ascii=False, indent=1), encoding="utf-8")
    write_transcript(h)


def save_turn(cid, model, user_msg, reply, project=None):
    with HIST_LOCK:
        h = load_hist(cid) or {"id": cid, "title": (user_msg["text"] or "（附件）")[:40],
                               "created": time.time(), "messages": [], "project": project}
        h["model"] = model
        h["updated"] = time.time()
        h["messages"] += [user_msg, reply]
        write_hist(h)


# ---------- 重問 / 編輯：從某則訊息之前分支出新的對話 ----------
# agy 的非互動模式不能倒回對話，所以把「保留的前半段」整理成背景，開一個新的 agy 對話接著問，
# 完成後用新對話取代舊的（標題、建立時間、專案沿用）。介面上看到的內容就和模型實際記得的一致。
def history_block(prior):
    if not prior:
        return ""
    parts = ["[先前的對話] 以下是這段對話到目前為止的內容（使用者修改了後面的訊息或要求重新回答，所以從這裡接續）。"
             "請把它當成你和使用者之前的對話，接著回答最後的新訊息；需要時可以用 view_file 重新讀附件。"]
    for m in prior:
        t = m.get("text") or ""
        if m["role"] == "assistant" and len(t) > 6000:
            t = t[:6000] + "…（後略）"
        line = ("使用者：" if m["role"] == "user" else "AI：") + t
        if m.get("files"):
            line += "\n（附件：" + "、".join(m["files"]) + "）"
        parts.append(line)
    return "\n\n".join(parts)


def squash(s):
    return re.sub(r"\s+", "", s or "")      # 忽略空白和換行再比對


def user_named(h):
    """標題是不是使用者自己取的。v1.3.0 起改名會記錄 renamed；更早改過的名稱沒有紀錄，
    就看標題是否和第一則訊息原文不同（而且不是自動產生的標題）。"""
    if h.get("renamed"):
        return True
    if h.get("auto_titled"):
        return False
    msgs = h.get("messages") or []
    raw = ((msgs[0].get("text") or "（附件）")[:40]) if msgs else ""
    return squash(h.get("title")) != squash(raw)


def save_fork(old, prior, new_cid, model, user_msg, reply):
    with HIST_LOCK:
        h = {**{k: old.get(k) for k in ("title", "created", "project", "auto_titled")},
             "renamed": user_named(old),      # 舊版改過的名稱也在這裡補上紀錄，之後不會被自動標題覆蓋
             "id": new_cid, "model": model, "updated": time.time(), "messages": prior + [user_msg, reply]}
        write_hist(h)
        if old["id"] != new_cid:          # 舊的分支拿掉（連同專案資料夾裡的副本）
            remove_transcript(old)
            hist_path(old["id"]).unlink(missing_ok=True)


# ---------- 自動標題 ----------
TITLE_PROMPT = """請幫下面這段對話取一個簡短的標題：繁體中文、6～16 個字、抓住主題。
不要標點符號、引號、表情符號，也不要「標題：」之類的前綴。只輸出標題本身，不要使用任何工具。

使用者：{q}

AI：{a}
"""


def auto_title(cid, question, answer):
    """新對話第一輪結束後，在背景用 Flash 產生標題；使用者自己改過名稱的不覆蓋。"""
    try:
        t = run_agy_text(TITLE_PROMPT.format(q=question[:1500], a=answer[:1500]), "title")
    except RuntimeError:
        return
    t = t.strip().splitlines()[0].strip(" \"'「」『』*#:：。") if t.strip() else ""
    if not t:
        return
    with HIST_LOCK:
        h = load_hist(cid)
        if h and not h.get("renamed"):
            h["title"], h["auto_titled"] = t[:30], True
            write_hist(h)


FAILED_REPLY = "（已中止或無回覆）"

# ---------- 預測使用者的下一句（輸入框按 Tab 採用） ----------
SUGGEST_PROMPT = """下面是使用者和 AI 的最後一輪對話。請預測「使用者」接下來最可能輸入的一句話。
規則：繁體中文、用使用者的口吻、25 字以內、具體（例如追問某個步驟、要求舉例、請它輸出成檔案），
不要加引號、編號、表情符號或任何說明，也不要使用任何工具。只輸出那一句話。

使用者：{q}

AI：{a}
"""


def suggest_next(question, answer):
    t = run_agy_text(SUGGEST_PROMPT.format(q=question[:1500], a=answer[-3000:]), "suggest")
    t = t.strip().splitlines()[0].strip(" \"'「」『』*#-") if t.strip() else ""
    return t[:60]


def retitle_legacy(limit=30):
    """舊版建立的對話標題就是第一則訊息原文：啟動時在背景逐一補上標題（一次最多 limit 個）。
    標題和原文不同的（使用者自己改過）、手動改名過、回答失敗的都跳過。"""
    done = 0
    for p in sorted(HISTORY.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        if done >= limit:
            break
        try:
            h = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        msgs = h.get("messages") or []
        answer = next((m.get("text") for m in msgs if m["role"] == "assistant" and m.get("text") != FAILED_REPLY), None)
        if h.get("auto_titled") or not msgs or not answer or user_named(h):
            continue
        auto_title(h["id"], msgs[0].get("text") or "", answer)
        done += 1


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    # ---- helpers ----
    def send_json(self, obj, code=200):
        b = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def send_file(self, path, ctype=None):
        import mimetypes
        data = Path(path).read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", ctype or mimetypes.guess_type(str(path))[0] or "application/octet-stream")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def body(self):
        n = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(n) or b"{}")

    @staticmethod
    def _is_self(netloc):
        """netloc（例如 127.0.0.1:8765）是不是指向這個伺服器本身。"""
        try:
            p = urlparse("//" + netloc)
            return p.hostname in LOCAL_HOSTS and p.port in (PORT, None)
        except ValueError:
            return False

    def host_ok(self):
        # 防 DNS rebinding：惡意網站把自己的網域解析到 127.0.0.1 時，Host 會是它的網域，一律拒絕
        if not self._is_self(self.headers.get("Host") or ""):
            self.send_json({"error": "forbidden host"}, 403)
            return False
        return True

    def local_only(self):
        # 只接受這個介面自己的網頁送來的修改請求（其他網站、或本機其他連接埠的網頁都不行）
        origin = self.headers.get("Origin")
        if origin and not (urlparse(origin).scheme == "http" and self._is_self(urlparse(origin).netloc)):
            self.send_json({"error": "forbidden"}, 403)
            return False
        return True

    # ---- GET ----
    def do_GET(self):
        if not self.host_ok():
            return
        u = urlparse(self.path)
        q = parse_qs(u.query)
        if u.path in ("/", "/index.html"):
            return self.send_file(STATIC / "index.html", "text/html; charset=utf-8")
        if u.path in ("/icon.png", "/icon.ico", "/favicon.ico"):
            name = "icon.png" if u.path == "/icon.png" else "icon.ico"
            return self.send_file(STATIC / name, "image/png" if name.endswith(".png") else "image/x-icon")
        if u.path == "/api/backgrounds":
            names = sorted(p.name for p in BG_DIR.iterdir() if p.is_file() and p.suffix.lower() in BG_EXTS)
            return self.send_json({"images": names, "folder": str(BG_DIR), "builtin": BUILTIN_BG})
        if u.path.startswith("/bgb/"):              # 內建背景：static/backgrounds/<名稱>-dark|light.jpg
            p = STATIC / "backgrounds" / unquote(u.path[5:])
            if p.is_file() and inside(p, STATIC / "backgrounds"):
                return self.send_file(p)
            return self.send_json({"error": "not found"}, 404)
        if u.path.startswith("/bg/"):
            p = BG_DIR / unquote(u.path[4:])
            if p.is_file() and inside(p, BG_DIR) and p.suffix.lower() in BG_EXTS:
                return self.send_file(p)
            return self.send_json({"error": "not found"}, 404)
        if u.path == "/api/agy_storage":
            return self.send_json(agy_storage())
        if u.path == "/api/components":
            return self.send_json(components.status())
        if u.path == "/api/folders":
            return self.send_json({k: str(v) for k, v in FOLDERS.items()})
        if u.path == "/api/shortcut":
            return self.send_json({"exists": (desktop_dir() / SHORTCUT_NAME).exists(),
                                   "prompt": bool(load_settings().get("shortcut_prompt", True))})
        if u.path == "/api/models":
            if not agy_installed():
                return self.send_json({"models": [], "error": AGY_MISSING})
            try:
                models = list_models(refresh=q.get("refresh", ["0"])[0] == "1")
                want = q.get("want", [""])[0]         # 介面上次選的模型；若已下架，告訴介面該換成哪個
                return self.send_json({"models": models,
                                       "replace": replacement_model(want, [m["id"] for m in models]) if want else "",
                                       "error": None if models else
                                       "agy 沒有回傳模型清單，可能還沒登入。請在終端機執行一次 `agy` 完成登入後再重新整理。"})
            except Exception as e:
                return self.send_json({"models": [], "error": str(e)})
        if u.path == "/api/settings":
            return self.send_json(load_settings())
        if u.path == "/api/version":
            return self.send_json({"version": version.VERSION, "boot": BOOT_ID})
        if u.path == "/api/update/check":
            return self.send_json(updater.check(force=q.get("force", ["0"])[0] == "1"))
        if u.path == "/api/usage":
            try:
                return self.send_json(get_usage())
            except Exception as e:
                return self.send_json({"error": str(e)}, 500)
        if u.path == "/api/projects":
            items = []
            for p in PROJECTS.glob("*/project.json"):
                try:
                    pr = json.loads(p.read_text(encoding="utf-8"))
                    items.append({**pr, "file_count": len(project_files(pr["id"]))})
                except Exception:
                    pass
            items.sort(key=lambda x: x.get("created") or 0)
            return self.send_json({"items": items})
        if u.path.startswith("/api/projects/"):
            pr = load_project(u.path.rsplit("/", 1)[1])
            if not pr:
                return self.send_json({"error": "not found"}, 404)
            conv_dir = proj_dir(pr["id"]) / "conversations"
            return self.send_json({**pr, "files": project_files(pr["id"]), "memory_limit": MEMORY_MAX,
                                   "transcripts": len(list(conv_dir.glob("*.md"))) if conv_dir.exists() else 0})
        if u.path == "/api/history":
            want = q.get("project", [""])[0]   # 空字串 = 不屬於任何專案的一般對話
            items = []
            for p in HISTORY.glob("*.json"):
                try:
                    h = json.loads(p.read_text(encoding="utf-8"))
                    if (h.get("project") or "") == want:
                        items.append({k: h.get(k) for k in ("id", "title", "model", "updated")})
                except Exception:
                    pass
            items.sort(key=lambda x: x.get("updated") or 0, reverse=True)
            return self.send_json({"items": items})
        if u.path.startswith("/api/history/"):
            h = load_hist(u.path.rsplit("/", 1)[1])
            return self.send_json(h or {"error": "not found"}, 200 if h else 404)
        if u.path == "/api/file":
            p = unquote(q.get("p", [""])[0])
            if p and inside(p, ROOT) and Path(p).is_file():
                return self.send_file(p)
            if p and inside(p, OUTPUT):             # 輸出檔案被整理到專案資料夾後，舊連結依檔名找
                hit = next(OUTPUT.rglob(glob.escape(Path(p).name)), None)
                if hit and hit.is_file():
                    return self.send_file(hit)
            return self.send_json({"error": "not allowed"}, 404)
        self.send_json({"error": "not found"}, 404)

    # ---- POST ----
    def do_POST(self):
        if not self.host_ok() or not self.local_only():
            return
        u = urlparse(self.path)
        if u.path == "/api/upload":
            d = self.body()
            day = UPLOADS / f"{datetime.now():%Y%m%d}"
            day.mkdir(parents=True, exist_ok=True)
            dest = day / f"{uuid.uuid4().hex[:6]}_{safe_name(d.get('name', 'file'))}"
            dest.write_bytes(base64.b64decode(d["data"].split(",", 1)[-1]))
            return self.send_json({"path": str(dest), "name": d.get("name")})
        if u.path == "/api/stop":
            rid = self.body().get("run_id")
            STOPPED.add(rid)
            with RUNS_LOCK:
                p = RUNS.get(rid)
            if p and p.poll() is None:
                subprocess.run(["taskkill", "/T", "/F", "/PID", str(p.pid)],
                               capture_output=True, creationflags=NO_WINDOW)
            return self.send_json({"ok": True})
        if u.path == "/api/settings":
            s = {**load_settings(), **{k: v for k, v in self.body().items() if k in DEFAULT_SETTINGS}}
            SETTINGS.write_text(json.dumps(s, ensure_ascii=False, indent=1), encoding="utf-8")
            return self.send_json(s)
        if u.path == "/api/delete":
            cid = self.body().get("id", "")
            remove_transcript(load_hist(cid))
            drop_brain(cid)                           # agy 為這段對話留的暫存（讀過的 PDF 副本等）一起清掉
            p = hist_path(cid)
            if p.exists():
                p.unlink()
            return self.send_json({"ok": True})
        if u.path == "/api/rename":
            d = self.body()
            title = (d.get("title") or "").strip()
            with HIST_LOCK:
                h = load_hist(d.get("id", ""))
                if not h or not title:
                    return self.send_json({"error": "not found"}, 404)
                h["title"], h["renamed"] = title[:80], True     # 手動改過名稱：自動標題不再覆蓋
                write_hist(h)
            return self.send_json({"ok": True})
        if u.path.startswith("/api/project/"):
            return self.project_action(u.path.rsplit("/", 1)[1], self.body())
        if u.path == "/api/chat":
            return self.chat(self.body())
        if u.path == "/api/update/install":
            return self.install_update()
        if u.path == "/api/open_folder":          # 在檔案總管打開輸出、背景圖片、設定等資料夾
            folder = FOLDERS.get(self.body().get("which"))
            if not folder:
                return self.send_json({"ok": False, "message": "不認得的資料夾"})
            folder.mkdir(parents=True, exist_ok=True)
            open_folder_front(folder)
            return self.send_json({"ok": True, "path": str(folder)})
        if u.path == "/api/components/install":
            return self.install_component(self.body().get("id"))
        if u.path == "/api/components/cancel":
            ev = INSTALLING.get(self.body().get("id"))
            if ev:
                ev.set()
            return self.send_json({"ok": True})
        if u.path == "/api/components/remove":
            cid = self.body().get("id")
            if cid in components.COMPONENTS and cid not in INSTALLING:
                components.remove(cid)
            return self.send_json({"ok": True, **components.status()})
        if u.path == "/api/typst/compile":       # 剛裝好 Typst：把等著的 .typ 編譯掉
            if not components.installed("typst"):
                return self.send_json({"ok": False, "message": "Typst 還沒安裝"})
            d = self.body()
            results = compile_files(d.get("files") or [])
            if d.get("conversation_id") and any(r["ok"] for r in results):
                with HIST_LOCK:             # 把 PDF 連結補進那段對話的最後一則回答
                    h = load_hist(d["conversation_id"])
                    if h and h["messages"] and h["messages"][-1]["role"] == "assistant":
                        h["messages"][-1]["text"] = h["messages"][-1]["text"].rstrip() + "\n\n" + pdf_links(results)
                        write_hist(h)
            return self.send_json({"ok": True, "results": results, "links": pdf_links(results)})
        if u.path == "/api/suggest":
            d = self.body()
            try:
                return self.send_json({"ok": True, "text": suggest_next(d.get("q") or "", d.get("a") or "")})
            except RuntimeError as e:
                return self.send_json({"ok": False, "message": str(e)})
        if u.path == "/api/agy_storage/clean":
            removed = clean_agy_storage()
            return self.send_json({"ok": True, "removed": removed, **agy_storage()})
        if u.path == "/api/export":                # 目前對話存成 Markdown 到 output
            h = load_hist(self.body().get("id", ""))
            if not h:
                return self.send_json({"ok": False, "message": "找不到這段對話"})
            dest = export_markdown(h, out_dir(load_project(h.get("project"))))
            return self.send_json({"ok": True, "name": dest.name, "path": str(dest)})
        if u.path == "/api/backup":
            dest = backup_all()
            return self.send_json({"ok": True, "name": dest.name, "path": str(dest),
                                   "size": human_size(dest.stat().st_size)})
        if u.path == "/api/import":
            try:
                added, skipped = import_backup(base64.b64decode(self.body()["data"].split(",", 1)[-1]))
            except Exception as e:
                return self.send_json({"ok": False, "message": f"匯入失敗（不是有效的備份檔？）：{e}"})
            sync_transcripts()
            return self.send_json({"ok": True, "added": added, "skipped": skipped})
        if u.path == "/api/backgrounds/upload":    # 從設定面板直接加入背景圖片
            d = self.body()
            name = safe_name(d.get("name", "background.png"))
            if Path(name).suffix.lower() not in BG_EXTS:
                return self.send_json({"ok": False, "message": "只接受圖片檔（png、jpg、webp、gif、bmp）"})
            dest, stem, suf, n = BG_DIR / name, Path(name).stem, Path(name).suffix, 2
            while dest.exists():
                dest = BG_DIR / f"{stem} ({n}){suf}"
                n += 1
            dest.write_bytes(base64.b64decode(d["data"].split(",", 1)[-1]))
            return self.send_json({"ok": True, "name": dest.name})
        if u.path == "/api/shortcut/create":
            try:
                lnk = create_shortcut()
                return self.send_json({"ok": True, "path": str(lnk)})
            except Exception as e:
                return self.send_json({"ok": False, "message": f"建立捷徑失敗：{e}"})
        self.send_json({"error": "not found"}, 404)

    def install_component(self, cid):
        """下載安裝元件，用串流回報進度（介面上的卡片會即時顯示）。"""
        if cid not in components.COMPONENTS:
            return self.send_json({"ok": False, "message": "不認得的元件"})
        if cid in INSTALLING:
            return self.send_json({"ok": False, "message": "已經在下載中"})
        self.send_response(200)
        self.send_header("Content-Type", "application/x-ndjson; charset=utf-8")
        self.end_headers()
        cancel = INSTALLING[cid] = threading.Event()
        last = [0.0]

        def emit(obj):
            try:
                self.wfile.write((json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8"))
                self.wfile.flush()
            except OSError:
                cancel.set()                 # 頁面關掉了：停止下載

        def progress(done, total, msg=""):
            if time.time() - last[0] > 0.3 or done >= total:
                last[0] = time.time()
                emit({"event": "progress", "done": done, "total": total, "msg": msg})
        try:
            components.install(cid, progress, cancel)
            emit({"event": "done", "ok": True})
        except components.Cancelled:
            emit({"event": "done", "ok": False, "cancelled": True, "message": "已取消下載"})
        except Exception as e:
            emit({"event": "done", "ok": False, "message": f"安裝失敗：{e}"})
        finally:
            INSTALLING.pop(cid, None)

    def install_update(self):
        with RUNS_LOCK:
            busy = any(p.poll() is None for p in RUNS.values())
        if busy:
            return self.send_json({"ok": False, "message": "還有回覆在生成中，等它完成再更新。"})
        info = updater.check(force=True)
        if not info.get("available"):
            return self.send_json({"ok": False, "message": info.get("error") or "已經是最新版。"})
        try:
            zip_path = updater.download(info, DATA / "update_download.zip")
            new_version = updater.apply(zip_path, ROOT)
        except Exception as e:
            return self.send_json({"ok": False, "message": f"更新失敗，程式維持舊版：{e}"})
        finally:
            (DATA / "update_download.zip").unlink(missing_ok=True)
        self.send_json({"ok": True, "version": new_version})
        restart_later()

    def project_action(self, action, d):
        if action == "create":
            pr = {"id": uuid.uuid4().hex[:12], "name": (d.get("name") or "新專案").strip()[:60],
                  "instructions": "", "created": time.time()}
            save_project(pr)
            sync_skills(proj_dir(pr["id"]))
            return self.send_json(pr)
        pr = load_project(d.get("id"))
        if not pr:
            return self.send_json({"error": "project not found"}, 404)
        if action == "update":
            if isinstance(d.get("memory"), str) and len(d["memory"].strip()) > MEMORY_MAX:
                return self.send_json({"ok": False, "message": f"專案記憶最多 {MEMORY_MAX} 字（目前 {len(d['memory'].strip())} 字），請刪減後再儲存。"})
            old_out = out_dir(pr, create=False)
            for k in ("name", "instructions", "memory"):
                if isinstance(d.get(k), str):
                    pr[k] = d[k].strip()[:60] if k == "name" else d[k]
            if isinstance(d.get("memory_auto"), bool):
                pr["memory_auto"] = d["memory_auto"]
            if not pr["name"]:
                pr["name"] = "未命名專案"
            new_out = out_dir(pr, create=False)
            if old_out != new_out and old_out.is_dir() and not new_out.exists():   # 專案改名：輸出資料夾跟著改
                try:
                    old_out.rename(new_out)
                except OSError:
                    pass
            save_project(pr)
            return self.send_json(pr)
        if action == "rebuild_memory":     # 用這個專案所有對話重新整理記憶（會等整理完才回應）
            material = all_conversations_material(pr["id"])
            if not material:
                return self.send_json({"ok": False, "message": "這個專案還沒有對話"})
            ok, msg = update_memory(pr["id"], material)
            return self.send_json({"ok": ok, "message": msg, **(load_project(pr["id"]) or {})})
        files_dir = proj_dir(pr["id"]) / "files"
        if action == "upload":
            name = safe_name(d.get("name", "file"))
            dest, stem, suf, n = files_dir / name, Path(name).stem, Path(name).suffix, 2
            while dest.exists():   # 同名就加 (2)、(3)…
                dest = files_dir / f"{stem} ({n}){suf}"
                n += 1
            dest.write_bytes(base64.b64decode(d["data"].split(",", 1)[-1]))
            return self.send_json({"name": dest.name})
        if action == "delete_file":
            f = files_dir / safe_name(d.get("name", ""))
            if f.is_file() and inside(f, files_dir):
                f.unlink()
            return self.send_json({"ok": True})
        if action == "delete":
            import shutil
            # 專案的對話紀錄移回「一般對話」，不刪除
            for p in HISTORY.glob("*.json"):
                try:
                    h = json.loads(p.read_text(encoding="utf-8"))
                    if h.get("project") == pr["id"]:
                        h["project"] = None
                        p.write_text(json.dumps(h, ensure_ascii=False, indent=1), encoding="utf-8")
                except Exception:
                    pass
            shutil.rmtree(proj_dir(pr["id"]), ignore_errors=True)
            return self.send_json({"ok": True})
        return self.send_json({"error": "unknown action"}, 404)

    def chat(self, d):
        text, files = d.get("text", ""), d.get("files", [])
        files = [f for f in files if inside(f, ROOT)]
        model, cid = d.get("model"), d.get("conversation_id")
        # 舊分頁還選著已下架的模型：直接改用替代模型，不浪費時間重試
        asked, model = model, replacement_model(model, available_ids())
        pr = load_project(d.get("project"))
        # 重問 / 編輯：fork = {"from": 舊對話 id, "keep": 保留前幾則訊息}
        fork, old, prior = d.get("fork"), None, []
        if fork:
            old = load_hist(fork.get("from") or "")
            if old:
                keep = max(0, min(int(fork.get("keep") or 0), len(old["messages"])))
                prior = old["messages"][:keep - keep % 2]        # 保持「使用者／AI」成對
                cid = None
                pr = load_project(old.get("project"))
        is_new = not cid and not old
        instructions = None
        if not cid:   # 新對話：附上常駐指示（＋專案指示）
            instructions = load_settings()["instructions"]
            if pr and pr.get("instructions", "").strip():
                instructions += f"\n\n[這個專案的指示]\n{pr['instructions'].strip()}"
            if pr and pr.get("memory", "").strip():
                instructions += ("\n\n[專案記憶] 以下是之前在這個專案的其他對話中累積的重點（可能不完整，"
                                 f"和使用者現在說的衝突時以使用者為準）：\n{clip_memory(pr['memory'].strip())}")
            if prior:
                instructions += "\n\n" + history_block(prior)
        extra_hint = (SEARCH_HINT if d.get("search") else "") + OUTPUT_HINT.format(dir=out_dir(pr))
        prompt = build_prompt(text + extra_hint, files, instructions,
                              project_block(pr, cid or (old and old["id"])) if pr else "")
        # agy 1.3.3 起讀工作區以外的檔案需要授權（非互動模式會直接拒絕），
        # 專案對話在專案資料夾裡跑，附件卻在 uploads：把 uploads 加進工作區
        # output：讓 AI 能把產生的檔案存進去。主目錄在 agy 的「信任工作區」裡，檔案修改預設要人工審查，
        # 非互動模式沒辦法審查就會被拒；accept-edits 只對工作區（cwd + --add-dir）內有效，主目錄其他地方照樣不能改
        cmd = [agy_exe(), "-p", prompt, "--output-format", "stream-json",
               "--add-dir", str(UPLOADS), "--add-dir", str(OUTPUT), "--mode", "accept-edits"]
        if model:
            cmd += ["--model", model]
        # 不傳 --effort：模型 ID 本身已含強度（如 -high），另外加會衝突，Claude 也不支援
        if cid:
            cmd += ["--conversation", cid]

        run_id = uuid.uuid4().hex
        started = time.time()                       # 用來找出這輪 Nano Banana 新生成的圖片
        cwd = proj_dir(pr["id"]) if pr else WORK   # 專案對話在專案資料夾裡跑，模型也能自己列出/搜尋檔案
        self.send_response(200)
        self.send_header("Content-Type", "application/x-ndjson; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()

        def emit(obj):
            try:
                self.wfile.write((json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8"))
                self.wfile.flush()
            except OSError:
                pass

        emit({"event": "run", "run_id": run_id})
        if asked and model != asked:
            emit({"event": "model_switched", "from": asked, "to": model})
        # agy 1.3.3 的模型目錄時有時無（同一個模型大約一半的機率被判定「不存在」），
        # 這種失敗在驗證階段就發生、不耗額度，所以自動重試幾次
        for attempt in range(1, MODEL_RETRIES + 1):
            try:
                proc = subprocess.Popen(cmd, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                        stdin=subprocess.DEVNULL, creationflags=NO_WINDOW)
            except OSError as e:
                emit({"event": "done", "conversation_id": cid, "code": -1,
                      "error": AGY_MISSING if isinstance(e, FileNotFoundError) else f"無法執行 agy：{e}"})
                return
            with RUNS_LOCK:
                RUNS[run_id] = proc
            first = proc.stdout.readline()
            if b"invalid model selection" not in first:
                break                          # 正常開始，往下照常串流
            rest = proc.stdout.read()
            proc.wait()
            text_out = (first + rest).decode("utf-8", errors="replace")
            if attempt == MODEL_RETRIES or run_id in STOPPED:
                proc = None
                pre_output = text_out
                break
            emit({"event": "retry", "attempt": attempt, "max": MODEL_RETRIES})
            time.sleep(2)

        reply, steps, new_cid, raw_other, denied = "", [], cid, [], []
        if proc is None:                        # 重試用完仍失敗：把 agy 的輸出當成錯誤訊息
            raw_other = [l for l in pre_output.splitlines() if l.strip()]
            lines = []
            returncode = 1
        else:
            lines = [first] if first else []
        if proc is not None:
            import itertools
            lines = itertools.chain(lines, proc.stdout)
        for raw in lines:
            line = raw.decode("utf-8", errors="replace").strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                raw_other.append(line)
                emit({"event": "log", "text": line})
                continue
            if ev.get("event") == "init":
                new_cid = ev.get("conversation_id") or new_cid
                remember_run(new_cid)
            su = ev.get("step_update") or {}
            if su.get("step_type") == "agent_response" and su.get("text_delta"):
                reply += su["text_delta"]
            if su.get("step_type") == "tool" and su.get("state") == "ACTIVE":
                steps.append({"tool": su.get("tool_name"), "params": (su.get("tool_info") or {}).get("parameters")})
            if ev.get("event") == "result":
                r = ev.get("result") or {}
                new_cid = r.get("conversation_id") or new_cid
                reply = r.get("response") or reply
                denied = [a.get("display_name") or a.get("action") for a in r.get("denied_actions") or []]
            emit(ev)
        if proc is not None:
            proc.wait()
            returncode = proc.returncode
        with RUNS_LOCK:
            RUNS.pop(run_id, None)
        STOPPED.discard(run_id)

        # Nano Banana 生成的圖片：從 agy 的暫存搬到 output，直接接在回答後面顯示
        images = collect_images(new_cid, started, out_dir(pr)) if new_cid else []
        if images:
            # 路徑要編碼：「AI sandbox」這種有空格的路徑會讓 Markdown 的圖片語法斷掉
            shown = "\n\n".join(f"![{p.stem}](file:///{quote(p.as_posix(), safe=':/')})" for p in images)
            names = "、".join(p.name for p in images)
            for p in images:      # 模型自己寫的圖片連結指向 agy 暫存（已經搬走）：拿掉，改用下面正確的
                reply = re.sub(r"!\[[^\]]*\]\([^)]*" + re.escape(p.stem) + r"[^)]*\)\n?", "", reply)
            reply = (reply.rstrip() + "\n\n" if reply.strip() else "") + f"{shown}\n\n🖼 已存到 output／{out_dir(pr).name}：{names}"
            emit({"event": "images", "paths": [str(p) for p in images], "reply": reply})
        # PDF：這輪 AI 在 output 寫了 .typ → 有 Typst 就編譯；沒有就請介面詢問要不要下載
        typs = new_typ_files(started) if returncode == 0 else []
        if typs and components.installed("typst"):
            results = compile_files(typs)
            fixed_notes = []
            # 編譯失敗：在背景請模型修正再重新編譯（最多 2 次），修正過程不顯示在對話裡
            for attempt in (1, 2, 3):          # Typst 一次只回報一個錯誤，錯好幾處時要多修幾輪
                failed = [r for r in results if not r["ok"]]
                if not failed or not new_cid:
                    break
                for r in failed:
                    emit({"event": "pdf_fixing", "attempt": attempt, "name": r["name"]})
                    reason = error_summary(r["error"])
                    fix_typst(new_cid, cwd, model, r)
                    again = compile_files([r["typ"]])[0]
                    results[results.index(r)] = again
                    if again["ok"]:
                        fixed_notes.append(f"{r['name']} 第一次排版時有語法錯誤（{reason}），已自動修正後重新產生。")
            # 看成品檢查：模型只讀 .typ 時看不出頁數、版面、括號位置等問題，所以把每頁轉成圖片給它看（最多 2 輪）
            if new_cid and load_settings().get("pdf_verify", True):
                for i, r in enumerate(results):
                    if not r["ok"]:
                        continue
                    for attempt in (1, 2):
                        emit({"event": "pdf_checking", "attempt": attempt, "name": r["pdf_name"]})
                        backup = Path(r["typ"]).read_text(encoding="utf-8", errors="replace")
                        changed, note = verify_pdf(cwd, model, r, text)
                        if not changed:
                            break
                        again = compile_files([r["typ"]])[0]
                        if not again["ok"]:              # 修正反而編譯失敗：還原原始檔，保留修正前的 PDF
                            Path(r["typ"]).write_text(backup, encoding="utf-8")
                            fixed_notes.append(f"檢查 {r['pdf_name']} 時嘗試修正但產生語法錯誤，保留修正前的版本。")
                            break
                        results[i] = r = again
                        fixed_notes.append(f"檢查成品後修正了 {r['pdf_name']}：{note or '版面或內容的問題'}")
            links = pdf_links(results)
            if links:
                reply = reply.rstrip() + "\n\n" + links
            if fixed_notes:
                reply += "\n\n" + "\n".join(f"> ⚙ {n}" for n in fixed_notes)
            still = [r for r in results if not r["ok"]]
            if still:
                reply += "\n\n" + "\n".join(f"> ⚠ {r['name']} 自動修正 3 次仍無法排版（{error_summary(r['error'])}），"
                                            f".typ 原始檔在該輸出資料夾的 {TYP_SRC}，可以請我再修正一次。" for r in still)
            emit({"event": "pdf", "results": results, "reply": reply})
        elif typs:
            emit({"event": "need_component", "id": "typst", "info": components.status()["typst"],
                  "files": [str(p) for p in typs]})
        if new_cid and (reply or steps or old):
            user_msg = {"role": "user", "text": text, "files": files}
            reply_msg = {"role": "assistant", "text": reply or FAILED_REPLY, "steps": steps}
            if images:
                reply_msg["images"] = [str(p) for p in images]
            if old:
                save_fork(old, prior, new_cid, model, user_msg, reply_msg)
                if old["id"] != new_cid:
                    drop_brain(old["id"])          # 被取代的分支：agy 的暫存也清掉
            else:
                save_turn(new_cid, model, user_msg, reply_msg, pr["id"] if pr else None)
            # 新對話，或重問／編輯第一則（使用者沒自己取過名稱時）：背景用 Flash 取標題
            if reply and (is_new or (old and not prior and not user_named(old))):
                threading.Thread(target=auto_title, args=(new_cid, text, reply), daemon=True).start()
            # 專案對話：背景整理記憶，不影響這次回覆
            if pr and pr.get("memory_auto", True) and reply and returncode == 0:
                threading.Thread(target=update_memory, args=(pr["id"], turn_material(text, reply)),
                                 daemon=True).start()
        error = "\n".join(raw_other[-20:]) if returncode not in (0, None) else None
        if denied and not reply:          # 例如讀檔被拒：說清楚，而不是只顯示「沒有回覆」
            error = (f"agy 拒絕了需要授權的操作（{'、'.join(denied)}），所以沒有產生回答。"
                     "非互動模式無法跳出授權視窗；若是讀取附件，請確認介面已更新到最新版，再按「↻ 重新回答」。")
        model_gone = bool(error) and "invalid model selection" in error
        if model_gone:                    # 選的模型已經不能用（agy 更新後被拿掉）：記下來，介面會重抓清單
            forget_model(model)
            error = (f"模型「{model}」目前無法使用（自動重試 {MODEL_RETRIES} 次都被 agy 拒絕）。"
                     "可以稍後再按「↻ 重新回答」，或在上方改選其他模型。")
        emit({"event": "done", "conversation_id": new_cid, "code": returncode,
              "error": error, "model_gone": model_gone})


SERVER = None


class Server(ThreadingHTTPServer):
    # Windows 的 SO_REUSEADDR 會讓第二個程序也能綁同一個連接埠（兩個伺服器同時在跑），改成獨佔
    allow_reuse_address = False
    allow_reuse_port = False

    def server_bind(self):
        if os.name == "nt":
            import socket
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()


def restart_later():
    """更新完：開一個新的伺服器程序（它會等這個程序放開連接埠），然後關掉自己。"""
    def go():
        time.sleep(0.5)               # 讓「更新成功」的回應先送出去
        subprocess.Popen([sys.executable, str(ROOT / "server.py"), "--no-browser", "--wait-port"],
                         cwd=ROOT, creationflags=NO_WINDOW | 0x00000008,   # DETACHED_PROCESS
                         close_fds=True)
        if SERVER:
            SERVER.shutdown()
        os._exit(0)
    threading.Thread(target=go, daemon=True).start()


def main():
    global SERVER
    url = f"http://{HOST}:{PORT}/"
    srv = None
    # 更新後重啟：舊程序可能還沒放開連接埠，多等一下
    for _ in range(40 if "--wait-port" in sys.argv else 1):
        try:
            srv = Server((HOST, PORT), Handler)
            break
        except OSError:
            time.sleep(0.5)
    if srv is None:   # 已經在跑了，直接開網頁
        if "--no-browser" not in sys.argv:
            webbrowser.open(url)
        return
    SERVER = srv
    threading.Thread(target=sync_transcripts, daemon=True).start()
    threading.Thread(target=sync_all_skills, daemon=True).start()
    threading.Thread(target=tidy_output, daemon=True).start()
    threading.Thread(target=retitle_legacy, daemon=True).start()
    if "--no-browser" not in sys.argv:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    print(f"Antigravity 介面：{url}")
    srv.serve_forever()


if __name__ == "__main__":
    main()
