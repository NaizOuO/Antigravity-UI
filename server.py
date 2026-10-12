# -*- coding: utf-8 -*-
"""本機 Antigravity CLI (agy) 介面伺服器。只用 Python 標準函式庫。

介面上的文字 / 圖片 / 檔案 -> 以 `agy -p` 子程序執行 -> 把串流結果回傳到網頁。
"""
import base64
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
from urllib.parse import parse_qs, unquote, urlparse

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
SETTINGS = DATA / "settings.json"
for p in (WORK, UPLOADS, HISTORY, PROJECTS):
    p.mkdir(parents=True, exist_ok=True)

# 用 pythonw 在背景跑時沒有主控台，錯誤訊息寫進 data/server.log 方便除錯
if sys.stderr is None or sys.stdout is None:
    _log = open(DATA / "server.log", "a", encoding="utf-8", buffering=1)
    sys.stderr = sys.stdout = _log

DEFAULT_SETTINGS = {
    "instructions": "請用繁體中文回答。數學式請用 LaTeX（行內 $...$，獨立式 $$...$$）。",
    "shortcut_prompt": True,      # 第一次開啟時詢問要不要建立桌面捷徑；回答過就不再問
}

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
        return {**DEFAULT_SETTINGS, **json.loads(SETTINGS.read_text(encoding="utf-8"))}
    except (OSError, ValueError):
        return dict(DEFAULT_SETTINGS)


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


def list_models():
    if MODELS_CACHE["data"] and time.time() - MODELS_CACHE["t"] < 600:
        return MODELS_CACHE["data"]
    out = subprocess.run([agy_exe(), "models"], capture_output=True, text=True,
                         encoding="utf-8", errors="replace", creationflags=NO_WINDOW,
                         timeout=60).stdout
    models = []
    for line in out.splitlines():
        if "\t" in line:
            mid, name = line.split("\t", 1)
            models.append({"id": mid.strip(), "name": name.strip()})
    if models:
        MODELS_CACHE.update(t=time.time(), data=models)
    return models


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


def save_turn(cid, model, user_msg, reply, project=None):
    h = load_hist(cid) or {"id": cid, "title": (user_msg["text"] or "（附件）")[:40],
                           "created": time.time(), "messages": [], "project": project}
    h["model"] = model
    h["updated"] = time.time()
    h["messages"] += [user_msg, reply]
    hist_path(cid).write_text(json.dumps(h, ensure_ascii=False, indent=1), encoding="utf-8")
    write_transcript(h)


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
        if u.path == "/api/shortcut":
            return self.send_json({"exists": (desktop_dir() / SHORTCUT_NAME).exists(),
                                   "prompt": bool(load_settings().get("shortcut_prompt", True))})
        if u.path == "/api/models":
            if not agy_installed():
                return self.send_json({"models": [], "error": AGY_MISSING})
            try:
                models = list_models()
                return self.send_json({"models": models, "error": None if models else
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
            with RUNS_LOCK:
                p = RUNS.get(self.body().get("run_id"))
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
            p = hist_path(cid)
            if p.exists():
                p.unlink()
            return self.send_json({"ok": True})
        if u.path == "/api/rename":
            d = self.body()
            h = load_hist(d.get("id", ""))
            title = (d.get("title") or "").strip()
            if not h or not title:
                return self.send_json({"error": "not found"}, 404)
            h["title"] = title[:80]
            hist_path(h["id"]).write_text(json.dumps(h, ensure_ascii=False, indent=1), encoding="utf-8")
            write_transcript(h)
            return self.send_json({"ok": True})
        if u.path.startswith("/api/project/"):
            return self.project_action(u.path.rsplit("/", 1)[1], self.body())
        if u.path == "/api/chat":
            return self.chat(self.body())
        if u.path == "/api/update/install":
            return self.install_update()
        if u.path == "/api/shortcut/create":
            try:
                lnk = create_shortcut()
                return self.send_json({"ok": True, "path": str(lnk)})
            except Exception as e:
                return self.send_json({"ok": False, "message": f"建立捷徑失敗：{e}"})
        self.send_json({"error": "not found"}, 404)

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
            return self.send_json(pr)
        pr = load_project(d.get("id"))
        if not pr:
            return self.send_json({"error": "project not found"}, 404)
        if action == "update":
            if isinstance(d.get("memory"), str) and len(d["memory"].strip()) > MEMORY_MAX:
                return self.send_json({"ok": False, "message": f"專案記憶最多 {MEMORY_MAX} 字（目前 {len(d['memory'].strip())} 字），請刪減後再儲存。"})
            for k in ("name", "instructions", "memory"):
                if isinstance(d.get(k), str):
                    pr[k] = d[k].strip()[:60] if k == "name" else d[k]
            if isinstance(d.get("memory_auto"), bool):
                pr["memory_auto"] = d["memory_auto"]
            if not pr["name"]:
                pr["name"] = "未命名專案"
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
        pr = load_project(d.get("project"))
        instructions = None
        if not cid:   # 新對話：附上常駐指示（＋專案指示）
            instructions = load_settings()["instructions"]
            if pr and pr.get("instructions", "").strip():
                instructions += f"\n\n[這個專案的指示]\n{pr['instructions'].strip()}"
            if pr and pr.get("memory", "").strip():
                instructions += ("\n\n[專案記憶] 以下是之前在這個專案的其他對話中累積的重點（可能不完整，"
                                 f"和使用者現在說的衝突時以使用者為準）：\n{clip_memory(pr['memory'].strip())}")
        prompt = build_prompt(text + (SEARCH_HINT if d.get("search") else ""), files, instructions,
                              project_block(pr, cid) if pr else "")
        cmd = [agy_exe(), "-p", prompt, "--output-format", "stream-json"]
        if model:
            cmd += ["--model", model]
        # 不傳 --effort：模型 ID 本身已含強度（如 -high），另外加會衝突，Claude 也不支援
        if cid:
            cmd += ["--conversation", cid]

        run_id = uuid.uuid4().hex
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

        try:
            proc = subprocess.Popen(cmd, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                    stdin=subprocess.DEVNULL, creationflags=NO_WINDOW)
        except OSError as e:
            emit({"event": "done", "conversation_id": cid, "code": -1,
                  "error": AGY_MISSING if isinstance(e, FileNotFoundError) else f"無法執行 agy：{e}"})
            return
        with RUNS_LOCK:
            RUNS[run_id] = proc

        emit({"event": "run", "run_id": run_id})
        reply, steps, new_cid, raw_other = "", [], cid, []
        for raw in proc.stdout:
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
            su = ev.get("step_update") or {}
            if su.get("step_type") == "agent_response" and su.get("text_delta"):
                reply += su["text_delta"]
            if su.get("step_type") == "tool" and su.get("state") == "ACTIVE":
                steps.append({"tool": su.get("tool_name"), "params": (su.get("tool_info") or {}).get("parameters")})
            if ev.get("event") == "result":
                r = ev.get("result") or {}
                new_cid = r.get("conversation_id") or new_cid
                reply = r.get("response") or reply
            emit(ev)
        proc.wait()
        with RUNS_LOCK:
            RUNS.pop(run_id, None)

        if new_cid and (reply or steps):
            save_turn(new_cid, model,
                      {"role": "user", "text": text, "files": files},
                      {"role": "assistant", "text": reply or "（已中止或無回覆）", "steps": steps},
                      pr["id"] if pr else None)
            # 專案對話：背景整理記憶，不影響這次回覆
            if pr and pr.get("memory_auto", True) and reply and proc.returncode == 0:
                threading.Thread(target=update_memory, args=(pr["id"], turn_material(text, reply)),
                                 daemon=True).start()
        emit({"event": "done", "conversation_id": new_cid, "code": proc.returncode,
              "error": "\n".join(raw_other[-20:]) if proc.returncode not in (0, None) else None})


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
    if "--no-browser" not in sys.argv:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    print(f"Antigravity 介面：{url}")
    srv.serve_forever()


if __name__ == "__main__":
    main()
