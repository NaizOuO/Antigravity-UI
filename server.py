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

import checkpoints
import command_guard
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
    "show_times": True,           # 顯示每則訊息的時間與對話建立時間
    "latex_copy": True,           # 點公式複製 LaTeX 原始碼
    "pdf_verify": True,           # PDF 編譯後把頁面轉成圖片讓模型檢查成品，有錯自動修正
    "suggest_reply": True,       # 回答完預測使用者的下一句，輸入框按 Tab 採用（用 Gemini Flash）
    # 用量
    "quota_warn": 15,             # 目前模型的額度剩下低於幾 % 時提醒（0 = 不提醒）
    # 更新
    "allow_commands": False,       # 允許模型執行指令（每次跳出授權卡詢問；危險的指令一律擋下）
    "update_check": True,        # 開啟時檢查新版本
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
    write_hooks(target)


# ---------- 工具授權：agy 執行任何工具前會呼叫 hook，由伺服器判斷、必要時在介面詢問使用者 ----------
PERMS_FILE = SETTING_DIR / "permissions.json"        # {"always": ["git status", "dir", ...]}
PERM_LOG = DATA / "permission_log.jsonl"
BIG_PDF = 3 * 1024 * 1024                         # 超過就不讓 view_file 整本讀（改用 lumina）
PERM_WAIT = 600                                       # 授權卡預設等 10 分鐘（打字慢、要查資料都來得及）
PERM_MAX = 1800                                       # 按「＋5 分鐘」最多延長到 30 分鐘（hook 的上限再多留一點）
ACTIVE = {}          # conversation id -> {"emit", "cwd", "run_id"}：正在介面上串流的對話
CONV_ALLOW = {}      # conversation id -> {程式名稱}：「這段對話都允許」
PENDING = {}         # 授權請求 id -> {"event", "nonce", "answer", "cid"}
PERM_LOCK = threading.Lock()


def hook_command():
    # agy 用 cmd 執行 hook，而且會把引號跳脫成 \"，所以整行不能有引號：
    # 用 Windows 內建的 curl（路徑沒有空格）把 agy 給的 JSON 原封不動轉給伺服器，伺服器回傳決定
    curl = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "curl.exe"
    return (f"{curl} -s -m {PERM_MAX + 20} -X POST -H Content-Type:application/json --data-binary @- "
            f"http://{HOST}:{PORT}/api/permission/hook")


def write_mcp(target, work_dir=None):
    """在 agy 的工作資料夾寫入 .agents/mcp_config.json：啟動 Lumina 的工具伺服器（讀 PDF、截圖、虛擬環境）。"""
    py = Path(sys.executable)
    if py.name.lower() == "pythonw.exe" and py.with_name("python.exe").exists():
        py = py.with_name("python.exe")
    roots = [str(Path(target).resolve()), str(UPLOADS), str(OUTPUT)]
    args = [str(ROOT / "tools" / "lumina_mcp.py"), "--roots", ";".join(roots)] + (["--work", str(work_dir)] if work_dir else [])
    cfg = {"mcpServers": {"lumina": {"command": str(py), "args": args, "env": {"PYTHONUTF8": "1"}}}}
    p = Path(target) / ".agents" / "mcp_config.json"
    text = json.dumps(cfg, ensure_ascii=False, indent=1)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        if not p.exists() or p.read_text(encoding="utf-8") != text:
            p.write_text(text, encoding="utf-8")
    except OSError:
        pass


def write_hooks(target):
    """在 agy 的工作資料夾寫入 hooks.json（每次對話前都重寫，被改掉也會還原）。"""
    d = Path(target) / ".agents"
    d.mkdir(parents=True, exist_ok=True)
    cfg = {"antigravity-ui-guard": {"PreToolUse": [
        {"matcher": "*", "hooks": [{"type": "command", "command": hook_command(), "timeout": PERM_MAX + 30}]}]}}
    text = json.dumps(cfg, ensure_ascii=False, indent=1)
    p = d / "hooks.json"
    try:
        if not p.exists() or p.read_text(encoding="utf-8") != text:
            p.write_text(text, encoding="utf-8")
    except OSError:
        pass


def load_perms():
    try:
        data = json.loads(PERMS_FILE.read_text(encoding="utf-8"))
        return {"always": [k for k in data.get("always", []) if isinstance(k, str)]}
    except (OSError, ValueError):
        return {"always": []}


def save_perms(p):
    PERMS_FILE.write_text(json.dumps(p, ensure_ascii=False, indent=1), encoding="utf-8")


def log_perm(entry):
    try:
        with open(PERM_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps({"t": time.time(), **entry}, ensure_ascii=False) + "\n")
    except OSError:
        pass


LUMINA_ARG = r"""(?:"[^"`$]*"|[^\s"'`$;&|<>(){}]+)"""
LUMINA_RE = re.compile(r"^\s*lumina(?:\.cmd)?\s+(pdf-info|pdf-find|pdf-text|pdf-page|pdf-thumbs)\s+(" + LUMINA_ARG + r")"
                       r"(?:\s+(" + LUMINA_ARG + r"))?\s*$", re.I)


def lumina_ok(command, cwd, roots):
    """內建工具的指令：格式完全符合、檔案是允許資料夾裡的 PDF、工作資料夾裡沒有冒充的 lumina 才算。"""
    m = LUMINA_RE.match(command or "")
    if not m:
        return False
    f = Path(m.group(2).strip('"'))
    if not f.is_absolute():
        f = Path(cwd or WORK) / f
    if f.suffix.lower() != ".pdf" or not command_guard._inside(f, [Path(r).resolve() for r in roots]):
        return False
    return not any((Path(cwd or WORK) / f"lumina{ext}").exists() for ext in ("", ".bat", ".cmd", ".exe", ".ps1", ".com"))


def install_lumina():
    """data/bin/lumina.cmd 指向內建工具，並把 data/bin 放進 PATH（agy 執行的指令會繼承）。"""
    bin_dir = DATA / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    py = Path(sys.executable)
    if py.name.lower() == "pythonw.exe" and py.with_name("python.exe").exists():
        py = py.with_name("python.exe")             # 要有主控台輸出，模型才讀得到結果
    # cmd 讀 .cmd 檔用的是系統的 OEM 編碼（繁中是 Big5），不是 UTF-8：路徑有中文（例如 OneDrive\桌面）時會變亂碼、
    # 找不到程式。所以程式路徑用 %~dp0（cmd 自己展開，不經過編碼），Python 路徑用系統編碼寫，寫不出來就換成短路徑名稱
    try:
        import ctypes
        oem = f"cp{ctypes.windll.kernel32.GetOEMCP()}"
    except Exception:
        oem = "mbcs"
    exe = str(py)
    try:
        exe.encode(oem)
    except (UnicodeEncodeError, LookupError):
        try:
            import ctypes
            buf = ctypes.create_unicode_buffer(1024)
            if ctypes.windll.kernel32.GetShortPathNameW(exe, buf, 1024):
                exe = buf.value
        except Exception:
            pass
    line = f'@"{exe}" -X utf8 "%~dp0..\\..\\tools\\lumina.py" %*'
    (bin_dir / "lumina.cmd").write_text(line + "\r\n", encoding=oem, errors="replace")
    if str(bin_dir) not in os.environ.get("PATH", ""):
        os.environ["PATH"] = str(bin_dir) + os.pathsep + os.environ.get("PATH", "")


HOOK_INFLIGHT, HOOK_RECENT = {}, {}           # agy 每次用工具會呼叫 hook 兩次：同一個呼叫只判斷（只跳卡）一次


def decide_tool(payload):
    """同一個工具呼叫（同對話、同工具、同參數）在處理中或 20 秒內又送來：直接沿用同一個決定。"""
    call = payload.get("toolCall") or {}
    key = (str(payload.get("conversationId") or ""), str(call.get("name") or ""),
           json.dumps(call.get("args") or {}, sort_keys=True, ensure_ascii=False))
    with PERM_LOCK:
        for k in [k for k, (t, _) in HOOK_RECENT.items() if time.time() - t > 20]:
            HOOK_RECENT.pop(k, None)
        if key in HOOK_RECENT:
            return HOOK_RECENT[key][1]
        waiting = HOOK_INFLIGHT.get(key)
        if not waiting:
            HOOK_INFLIGHT[key] = threading.Event()
    if waiting:                                # 第二次呼叫：等第一次的結果
        waiting.wait(PERM_MAX + 30)
        with PERM_LOCK:
            return HOOK_RECENT.get(key, (0, {"decision": "deny", "reason": "等候授權逾時"}))[1]
    try:
        result = _decide_tool(payload)
    except Exception:
        result = {"decision": "deny", "reason": "介面判斷這個操作時發生錯誤"}
    with PERM_LOCK:
        HOOK_RECENT[key] = (time.time(), result)
        HOOK_INFLIGHT.pop(key).set()
    return result


def _decide_tool(payload):
    """agy 的 PreToolUse 輸入 → hook 的回覆 {"decision", "reason"}。"""
    call = payload.get("toolCall") or {}
    tool, args = str(call.get("name") or ""), call.get("args") or {}
    cid = str(payload.get("conversationId") or "")
    run = ACTIVE.get(cid)
    work = bool(run and run.get("work_dir"))       # 工作模式：專案綁定了資料夾
    roots = [WORK, OUTPUT, UPLOADS] + ([Path(run["cwd"])] if run else []) + ([Path(run["work_dir"])] if work else [])
    command = str(args.get("CommandLine") or "")
    if tool == "run_command" and lumina_ok(command, str(args.get("Cwd") or ""), roots):
        if not components.installed("pdfium"):
            if run:                                 # 跳出下載元件的同意卡
                run["emit"]({"event": "need_component", "id": "pdfium", "info": components.status()["pdfium"], "files": []})
            return {"decision": "deny", "reason": "讀 PDF 的元件還沒下載，介面已經在畫面上詢問使用者要不要下載。"
                                                  "不要用 view_file 讀整份 PDF（很慢，掃描檔也讀不到），請直接結束這次回答，"
                                                  "告訴使用者：下載完成後按「重新回答」就能查。"}
        log_perm({"tool": tool, "command": command, "cid": cid, "level": "builtin", "decision": "auto_approve",
                  "note": "內建工具"})
        return {"decision": "auto_approve"}
    # 讀 agy 工作區以外的檔案：agy 自己會拒絕，而且一拒絕整輪就中斷、沒有回答。
    # 所以在這裡先拒絕並說明原因（模型收到原因會改用其他方法繼續）
    if run and tool != "run_command":
        # agy 會先讀自己存的 MCP 工具說明（~/.gemini/antigravity-cli/mcp/…）才呼叫工具，這個要放行
        allowed = ([Path(run["cwd"]), UPLOADS, OUTPUT, AGY_BRAIN.parent / "mcp"]
                   + ([Path(run["work_dir"])] if work else []))
        outside = [f for f in command_guard._path_args(args)
                   if Path(f).is_absolute() and not command_guard._inside(f, [a.resolve() for a in allowed])]
        if outside:
            log_perm({"tool": tool, "command": outside[0], "cid": cid, "level": "outside", "decision": "deny",
                      "note": "工作區以外的檔案"})
            return {"decision": "deny", "reason": f"{outside[0]} 不在可以讀取的資料夾裡（agy 不允許）。"
                                                  "不要再嘗試讀它；改用已經有的資訊，或請使用者把檔案加為附件，然後繼續回答。"}
    # 用 view_file 讀整本大 PDF：內容會一直留在對話裡，之後每一輪都變慢、變貴（課本 34 MB 實際發生過）
    if tool == "view_file":
        f = Path(str(args.get("AbsolutePath") or ""))
        try:
            big = f.suffix.lower() == ".pdf" and f.stat().st_size > BIG_PDF
        except OSError:
            big = False
        if big:
            log_perm({"tool": tool, "command": str(f), "cid": cid, "level": "big_pdf", "decision": "deny",
                      "note": "大型 PDF 改用 lumina"})
            return {"decision": "deny", "reason": f"這個 PDF 有 {f.stat().st_size // 1048576} MB，整本讀進來會讓之後每一輪都變慢。"
                                                  "請改用 lumina：pdf-info 看目錄、pdf-find 搜尋、pdf-text 讀指定頁、"
                                                  "pdf-thumbs 找位置、pdf-page 看清楚的頁面。"}
    if tool == "call_mcp_tool":                # Lumina 的工具伺服器（讀 PDF、截圖、虛擬環境）或其他 MCP 工具
        r = mcp_assess(args, run, work, cid)
        if "decision" in r:
            return r
        a, command = r["a"], r["command"]
        args = {**args, "Cwd": run["work_dir"] if work else ""}
    else:
        # 修改檔案：對話區只能寫 output；Code 區只能改工作資料夾（不要順手同步到 output 之類的其他地方）
        edit_roots = [Path(run["work_dir"])] if work else [OUTPUT]
        a = command_guard.assess(tool, args, roots if tool == "run_command" else edit_roots)
    base = {"tool": tool, "command": command, "cwd": str(args.get("Cwd") or ""), "cid": cid, "level": a["level"]}

    def done(decision, reason="", note="", asked=False):
        log_perm({**base, "decision": decision, "note": note or reason})
        if run and tool in ("run_command", "call_mcp_tool"):
            run["emit"]({"event": "permission_done", "command": command, "decision": decision,
                         "note": note or reason, "asked": asked})
        return {"decision": decision, **({"reason": reason} if reason else {})}

    if a["level"] == "pass":
        return {"decision": "allow"}
    if a["level"] == "block":
        why = "；".join(a["reasons"])
        return done("deny", f"使用者的安全設定不允許這個操作（{why}）。這是使用者的要求，請遵守、不要換個寫法再試；"
                            "改用其他方法完成，或直接回答使用者。", why)
    if tool not in ("run_command", "call_mcp_tool"):
        return {"decision": "allow"}
    if not work:
        return done("deny", "這是對話模式，不能執行終端機指令（內建的 lumina 工具除外）。請改用 view_file、lumina 讀取資料後直接回答；"
                            "如果真的需要執行程式，請告訴使用者：切到左上角的「Code」區、加入工作資料夾後就可以。", "對話模式")
    if not run:
        return done("deny", "背景工作不能執行指令。", "背景工作")
    network = any("網路" in w for w in a["warnings"])
    if a["level"] == "ask" and a["key"] and a["key"] in load_perms()["always"]:
        return done("auto_approve", note=f"永遠允許：{a['key']}")
    if a["program"] in CONV_ALLOW.get(cid, set()) and not network:
        return done("auto_approve", note=f"這段對話允許 {a['program']}")

    rid, nonce = uuid.uuid4().hex[:12], uuid.uuid4().hex
    req = {"event": threading.Event(), "nonce": nonce, "answer": None, "cid": cid, "started": time.time(),
           "deadline": time.time() + PERM_WAIT}
    with PERM_LOCK:
        PENDING[rid] = req
    run["emit"]({"event": "permission", "id": rid, "nonce": nonce, "command": command, "cwd": base["cwd"],
                 "summary": str(args.get("toolSummary") or args.get("toolAction") or ""), "level": a["level"],
                 "warnings": a["warnings"], "key": a["key"], "program": a["program"],
                 # 串了好幾個指令時不給「這段對話都允許」：選項只會以第一個指令命名，按了等於允許其他沒看清楚的指令
                 "can_conv": bool(a["program"]) and not network and a.get("segments", 1) == 1,
                 "script": (a["script"] or "")[:6000], "script_path": a["script_path"], "wait": PERM_WAIT})
    req["deadline"] = time.time() + PERM_WAIT
    while not req["event"].wait(1):            # 使用者按「＋5 分鐘」會把 deadline 往後延
        if time.time() > req["deadline"]:
            break
    with PERM_LOCK:
        PENDING.pop(rid, None)
    ans = req["answer"] or {"choice": "timeout"}
    choice = ans.get("choice")
    if choice == "once":
        return done("auto_approve", asked=True, note="允許一次")
    if choice == "conv" and a["program"] and not network:
        CONV_ALLOW.setdefault(cid, set()).add(a["program"])
        return done("auto_approve", asked=True, note=f"這段對話都允許 {a['program']}")
    if choice == "always" and a["key"]:
        p = load_perms()
        if a["key"] not in p["always"]:
            p["always"].append(a["key"])
            save_perms(p)
        return done("auto_approve", asked=True, note=f"永遠允許：{a['key']}")
    if choice == "timeout":
        return done("deny", "使用者沒有在時間內允許這個指令。請不要再執行指令，改用其他方法或直接回答。", "逾時未回應", asked=True)
    reason = str(ans.get("reason") or "").strip()[:500]
    return done("deny", "使用者拒絕執行這個指令。" + (f"使用者說：{reason}" if reason else "請改用其他方法或直接回答。"),
                "拒絕" + (f"：{reason}" if reason else ""), asked=True)


def mcp_assess(args, run, work, cid):
    """MCP 工具呼叫的判斷：回傳 {"decision": …}（直接決定）或 {"a": 風險評估, "command": 給使用者看的文字}（要詢問）。"""
    server, name = str(args.get("ServerName") or ""), str(args.get("ToolName") or "")
    a = args.get("Arguments") or {}
    risk = lambda level, warnings, key=None, program="": {"level": level, "reasons": [], "warnings": warnings, "key": key,
                                                          "program": program, "script": None, "script_path": None, "segments": 1}
    if server != "lumina":                   # 使用者自己加的其他 MCP 工具：每次詢問
        return {"a": risk("strict", [f"使用其他 MCP 工具（{server}）"]),
                "command": f"MCP 工具 {server} / {name}：{json.dumps(a, ensure_ascii=False)[:300]}"}
    if name.startswith("pdf_"):              # 讀 PDF：唯讀、檔案範圍由工具伺服器自己檢查
        if not components.installed("pdfium"):
            if run:
                run["emit"]({"event": "need_component", "id": "pdfium", "info": components.status()["pdfium"], "files": []})
            return {"decision": "deny", "reason": "讀 PDF 的元件還沒下載，介面已經在畫面上詢問使用者。不要改用 view_file 讀整份 PDF；"
                                                  "請直接結束這次回答，告訴使用者下載完成後按「重新回答」。"}
        log_perm({"tool": "lumina." + name, "command": str(a.get("file") or ""), "cid": cid, "level": "builtin",
                  "decision": "auto_approve", "note": "內建工具"})
        return {"decision": "auto_approve"}
    if name in ("run_capture", "venv_create", "venv_install") and not work:
        return {"decision": "deny", "reason": f"{name} 只能在 Lumina Code 的工作資料夾裡使用。請直接回答使用者；"
                                              "需要執行程式時，告訴使用者切到左上角的「Code」區。"}
    if name == "run_capture":
        extra = " ".join(map(str, a.get("args") or []))
        typed = f"，先輸入 {json.dumps(a['input'], ensure_ascii=False)}" if a.get("input") else ""
        return {"a": risk("strict", ["會在螢幕上開一個視窗執行這個程式幾秒，截下那個視窗的畫面後自動關閉"], program="run_capture"),
                "command": f"執行並截圖：{a.get('program', '')} {extra}".strip() + f"（{a.get('seconds') or 3} 秒{typed}）"}
    if name == "venv_create":
        return {"a": risk("ask", ["在工作資料夾建立 Python 虛擬環境 .venv"], key="lumina venv_create", program="venv_create"),
                "command": "建立 Python 虛擬環境：.venv"}
    if name == "venv_install":
        pk = "、".join(map(str, a.get("packages") or []))
        return {"a": risk("strict", [f"會連上網路下載並安裝套件：{pk}（請確認是你知道的套件）"], program="venv_install"),
                "command": f"安裝 Python 套件到 .venv：{pk}"}
    return {"decision": "deny", "reason": f"沒有 {name} 這個工具。"}


def deny_pending(cid):
    """使用者按停止：還在等授權的請求一律拒絕。"""
    with PERM_LOCK:
        for r in PENDING.values():
            if r["cid"] == cid:
                r["answer"] = {"choice": "deny", "reason": "使用者停止了這次回答"}
                r["event"].set()


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


def tail_thinking(cid, emit, stop, thoughts):
    """agy 的串流不含思考內容，但它會寫進 brain/<對話>/.system_generated/logs/transcript_full.jsonl 的 thinking 欄位。
    回答進行中每秒讀一次新增的部分，送到畫面（思考過程，可以展開看）。"""
    log = AGY_BRAIN / cid / ".system_generated" / "logs" / "transcript_full.jsonl"
    seen = 0
    try:                                       # 接著之前的對話：舊的思考不要再送一次
        seen = sum(1 for _ in open(log, encoding="utf-8", errors="replace"))
    except OSError:
        pass
    while True:
        finished = "done" in stop
        try:
            with open(log, encoding="utf-8", errors="replace") as f:
                lines = f.readlines()
        except OSError:
            lines = []
        for line in lines[seen:]:
            try:
                t = (json.loads(line).get("thinking") or "").strip()
            except ValueError:
                break                          # 寫到一半的行：下次再讀
            seen += 1
            if t:
                thoughts.append(t)
                emit({"event": "thinking", "text": t})
        if finished:
            return
        time.sleep(1)


CODE_CHECKS = {".cpp": ["g++", "-fsyntax-only", "-std=c++17"], ".cc": ["g++", "-fsyntax-only", "-std=c++17"],
               ".c": ["gcc", "-fsyntax-only"], ".py": ["python", "-m", "py_compile"]}
CODE_FIX_PROMPT = """[介面自動訊息，不是使用者說的話] 你剛才寫的 {path} 編譯檢查失敗。
請直接修正同一個檔案（用 replace_file_content 或 write_to_file），修正完只要回一句「已修正」。

錯誤訊息：
{error}
"""


def check_code(path):
    """編譯檢查：通過回傳 ""，有錯回傳錯誤訊息，電腦上沒有編譯器回傳 None（就不檢查）。"""
    import shutil as _sh
    cmd = CODE_CHECKS[path.suffix.lower()]
    if not _sh.which(cmd[0]):
        return None
    try:
        r = subprocess.run(cmd + [str(path)], cwd=path.parent, capture_output=True, text=True, encoding="utf-8",
                           errors="replace", creationflags=NO_WINDOW, timeout=120)
    except Exception:
        return None
    return "" if r.returncode == 0 else (r.stderr or r.stdout or "編譯失敗").strip()[-2500:]


def fix_code(cid, cwd, model, path, error, work_dir):
    cmd = [agy_exe(), "-p", CODE_FIX_PROMPT.format(path=path, error=error), "--output-format", "json",
           "--conversation", cid, "--add-dir", str(UPLOADS), "--add-dir", str(OUTPUT), "--add-dir", work_dir,
           "--mode", "accept-edits"] + (["--model", model] if model else [])
    try:
        subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                       creationflags=NO_WINDOW, timeout=600)
    except Exception:
        pass


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

WORK_HINT = ("\n\n[Lumina Code] 這個工作階段的工作資料夾是 {dir}。程式碼、專案檔案一律建立和修改在這個資料夾裡"
             "（不是 output）；執行指令時把 Cwd 設成這個資料夾。寫完或改完程式後，一定要實際編譯或執行一次確認沒有錯誤"
             "（例如 g++ 編譯、python 執行），有錯就修到通過，不要沒驗證就說完成。執行指令前使用者會看到授權卡，請簡短說明用途。"
             "只修改這個資料夾裡的檔案，不要另外複製一份到 output 或其他地方。"
             "不要直接執行會一直跑下去（例如時鐘、遊戲迴圈、伺服器）或需要鍵盤輸入的程式：它會卡住、停不下來；"
             "要確認畫面就加一個執行一次就結束的測試參數（例如 --test），或只編譯並請使用者自己執行。"
             "能一個指令做完就不要用 ; 串好幾個指令（使用者要逐一確認）。"
             "有畫面的程式（主控台繪圖、視窗程式）用 run_capture 執行並截圖確認。"
             "完成的標準：程式編譯或執行沒有錯誤，而且你實際看過執行結果；回答最後用一兩句說明你驗證了什麼、結果如何。"
             "Python 專案要安裝套件時先 venv_create，再用 venv_install，執行用 .venv\\Scripts\\python。")
TOOLS_HINT = ("\n\n[內建工具] 這個介面提供 lumina 工具（pdf_info、pdf_find、pdf_text、pdf_page、pdf_thumbs；Code 區另有 run_capture、"
              "venv_create、venv_install），用法寫在各工具的說明裡。讀 PDF 一律用它們，不要用 view_file 讀整本、也不要自己寫程式讀；"
              "先看對話前文和附件裡有沒有需要的內容，有就不要重查。看不到這些工具時，可以改用終端機指令 "
              "lumina pdf-info \"檔案\"（pdf-find、pdf-text、pdf-page、pdf-thumbs 同理）。")

# ---------- 桌面捷徑 ----------
SHORTCUT_NAME = "Lumina.lnk"
OLD_SHORTCUT = "Antigravity UI.lnk"         # v2.0.0 改名前的捷徑：啟動時換成新的


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
    """在桌面建立「Lumina」捷徑：用 wscript 執行 啟動.vbs，圖示用 static/icon.ico。已存在就覆蓋（例如資料夾搬過）。"""
    script = ("$ws = New-Object -ComObject WScript.Shell; "
              "$s = $ws.CreateShortcut($env:AGUI_LNK); "
              "$s.TargetPath = Join-Path $env:WINDIR 'System32\\wscript.exe'; "
              "$s.Arguments = '\"' + $env:AGUI_VBS + '\"'; "
              "$s.WorkingDirectory = $env:AGUI_DIR; "
              "$s.IconLocation = $env:AGUI_ICO + ',0'; "
              "$s.Description = 'Lumina'; "
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


def migrate_shortcut():
    """改名 Lumina 後：桌面上舊的「Antigravity UI」捷徑換成新名稱與新圖示（使用者沒建過捷徑就不動）。"""
    old = desktop_dir() / OLD_SHORTCUT
    if old.exists():
        try:
            create_shortcut()
            old.unlink()
        except Exception:
            pass
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


USAGE_CACHE = DATA / "usage_cache.json"     # 上次查到的用量：開頁面時先顯示，背景查到新的再換掉


def get_usage():
    out = subprocess.run([agy_exe(), "-p", "/usage", "--output-format", "json"], cwd=WORK,
                         capture_output=True, text=True, encoding="utf-8", errors="replace",
                         creationflags=NO_WINDOW, timeout=60).stdout
    data = json.loads(out)
    return (data.get("command") or {}).get("data") or {}

NO_WINDOW = 0x08000000 if os.name == "nt" else 0   # CREATE_NO_WINDOW：不跳出黑色視窗
# agy 執行時若「上次檢查更新」超過 15 分鐘，會另外開一個脫離的「--bg-updater」背景程式，
# 它會自己跳出主控台視窗（畫面閃一下；AGY_CLI_DISABLE_AUTO_UPDATE 實測無效）。
# 所以每次啟動 agy 前先把檢查時間更新成現在（agy 只看這個檔案的修改時間），
# 改由 agy_self_update() 定期在背景隱藏執行 `agy update`。
AGY_LAST_CHECK = Path.home() / ".gemini" / "antigravity-cli" / "last_check.timestamp"
AGY_UPDATE_EVERY = 6 * 3600


def quiet_agy_updater():
    try:
        AGY_LAST_CHECK.parent.mkdir(parents=True, exist_ok=True)
        AGY_LAST_CHECK.touch()
        os.utime(AGY_LAST_CHECK)
    except OSError:
        pass


def agy_self_update():
    while True:
        try:
            subprocess.run([agy_exe(), "update"], capture_output=True, creationflags=NO_WINDOW, timeout=600)
        except Exception:
            pass
        time.sleep(AGY_UPDATE_EVERY)
MAX_ARG = 24000                                   # Windows 命令列長度保守上限

RUNS = {}            # run_id -> Popen
RUNS_LOCK = threading.Lock()
STOPPED = set()      # 使用者按了停止的 run_id（自動重試時就不再重試）
MODEL_RETRIES = 5    # 模型被 agy 判定「不存在」時自動重試的次數
MODELS_CACHE = {"t": 0, "data": None}


def agy_exe():
    """agy 執行檔路徑。每次要啟動 agy 都會呼叫這裡，順便擋掉會跳視窗的背景更新。"""
    import shutil
    quiet_agy_updater()
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
        try:
            MODELS_LAST.write_text(json.dumps(models, ensure_ascii=False), encoding="utf-8")
        except OSError:
            pass
    return models


MODELS_LAST = DATA / "models_last.json"     # 上次成功取得的清單：Google 暫時連不上時沿用，介面才知道用的是哪個模型


def last_models():
    try:
        return json.loads(MODELS_LAST.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []


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
# ---------- Lumina Code：工作區（綁定一個資料夾），和對話區的「專案」完全分開 ----------
CODE = DATA / "code"                 # code/<id>/workspace.json；這個資料夾也是 agy 的工作目錄（放 .agents 的 hooks 與 skills）
CODE.mkdir(parents=True, exist_ok=True)
LUMINA_MD = "LUMINA.md"
LUMINA_MAX = 6000
LUMINA_TEMPLATE = """# {name}

## 這個專案在做什麼
（一兩句說明）

## 怎麼編譯、執行、測試
（例如：g++ main.cpp -o main.exe，然後執行 .\\main.exe）

## 慣例與注意事項
- （命名、檔案放哪裡、使用者的偏好）
"""
LUMINA_HINT = ("\n\n[LUMINA.md：這個資料夾的專案說明] 以下是 {path} 的內容，由使用者和 AI 共同維護，"
               "記錄這個專案的目的、編譯與執行方式、慣例。和使用者現在說的衝突時以使用者為準。\n"
               "如果這次學到之後每次都需要知道的事（例如新的編譯方式、使用者明確要求的慣例），"
               "請用 replace_file_content 簡短更新這個檔案（保持條列、精簡，不要寫這次的過程或程式碼）。\n\n{content}")


def code_dir(wid):
    return CODE / re.sub(r"[^0-9a-f]", "", wid or "")


def load_ws(wid):
    if not wid:
        return None
    try:
        return json.loads((code_dir(wid) / "workspace.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def save_ws(ws):
    d = code_dir(ws["id"])
    d.mkdir(parents=True, exist_ok=True)
    (d / "workspace.json").write_text(json.dumps(ws, ensure_ascii=False, indent=1), encoding="utf-8")


def list_ws():
    out = []
    for f in CODE.glob("*/workspace.json"):
        try:
            out.append(json.loads(f.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            pass
    return sorted(out, key=lambda w: w.get("created") or 0)


def ws_dir(ws):
    """工作區綁定的資料夾還在就回傳路徑字串。"""
    d = Path((ws or {}).get("work_dir") or "")
    return str(d.resolve()) if d.is_absolute() and d.is_dir() else None


TOOL_VERSIONS = {}           # 編譯器版本很少變：查一次就記住


def _tool_version(cmd):
    if cmd not in TOOL_VERSIONS:
        import shutil
        exe = shutil.which(cmd)
        ver = ""
        if exe:
            try:
                out = subprocess.run([exe, "--version"], capture_output=True, text=True, encoding="utf-8", errors="replace",
                                     creationflags=NO_WINDOW, timeout=10)
                ver = ((out.stdout or out.stderr).strip().splitlines() or [""])[0][:80]
            except Exception:
                ver = "（有安裝）"
        TOOL_VERSIONS[cmd] = ver
    return TOOL_VERSIONS[cmd]


def env_block(ws):
    """Code 區新工作階段開頭附上的環境資訊（參考 Claude Code）：模型不用自己一項項去查版本、套件、字型、檔案。"""
    import platform
    d = Path(ws_dir(ws) or "")
    lines = [f"- 作業系統：Windows {platform.release()}；run_command 執行的是 PowerShell（用 PowerShell 語法，不是 bash）",
             f"- 工作資料夾：{d}"]
    venv = d / ".venv"
    if (venv / "Scripts" / "python.exe").exists():
        site = venv / "Lib" / "site-packages"
        pkgs = sorted({p.name.split("-")[0] for p in site.glob("*.dist-info")}) if site.is_dir() else []
        lines.append(f"- Python 虛擬環境：已有 .venv（執行用 .venv\\Scripts\\python）；已安裝：{', '.join(pkgs[:40]) or '（沒有其他套件）'}")
    else:
        lines.append(f"- Python：{platform.python_version()}；還沒有 .venv（要裝套件時先用 venv_create）")
    if sys.version_info >= (3, 14):
        lines.append("- 注意：這台電腦的 Python 很新，pygame 還沒有對應版本，要用 pygame-ce（相容的版本，程式裡一樣 import pygame）")
    tools = [f"{c}（{v}）" for c in ("g++", "gcc", "node", "java", "git") if (v := _tool_version(c))]
    lines.append("- 可用的工具：" + ("、".join(tools) if tools else "沒有偵測到編譯器"))
    fonts = [f for f in (r"C:\Windows\Fonts\msjh.ttc", r"C:\Windows\Fonts\mingliu.ttc") if Path(f).exists()]
    if fonts:
        lines.append(f"- 中文字型：{'、'.join(fonts)}（程式要顯示中文時直接用這個路徑）")
    try:
        items = sorted(p for p in d.iterdir() if p.name not in (".venv", "__pycache__", ".git"))
        shown = [f"{p.name}/" if p.is_dir() else f"{p.name}（{p.stat().st_size // 1024 + 1} KB）" for p in items[:40]]
        lines.append("- 資料夾裡的檔案：" + ("、".join(shown) if shown else "（空的，新專案）")
                     + ("……" if len(items) > 40 else ""))
    except OSError:
        pass
    return "\n\n[環境資訊] 以下是目前的環境，已經確認過，不需要再用指令查詢：\n" + "\n".join(lines)


def lumina_memory(ws):
    """LUMINA.md 的內容（由 Lumina 自己放進新工作階段的開頭，不靠各模型各自找 GEMINI.md／CLAUDE.md，換模型也一樣）。"""
    d = ws_dir(ws)
    if not d:
        return ""
    try:
        return (Path(d) / LUMINA_MD).read_text(encoding="utf-8", errors="replace").strip()[:LUMINA_MAX]
    except OSError:
        return ""


def migrate_work_projects():
    """v2.0.x 的「工作模式專案」改成 Code 區的工作資料夾；專案本身變回一般專案（對話紀錄留在專案裡）。"""
    for f in PROJECTS.glob("*/project.json"):
        try:
            pr = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if pr.get("mode") != "work":
            continue
        wd = pr.get("work_dir") or ""
        if wd and not check_work_dir(wd) and not any(w.get("work_dir") == str(Path(wd).resolve()) for w in list_ws()):
            w = {"id": uuid.uuid4().hex[:12], "name": Path(wd).name or wd, "work_dir": str(Path(wd).resolve()),
                 "created": time.time()}
            save_ws(w)
        pr.pop("mode", None)
        pr.pop("work_dir", None)
        f.write_text(json.dumps(pr, ensure_ascii=False, indent=1), encoding="utf-8")


def work_folder(pr):
    """專案是工作模式且綁定的資料夾還在：回傳路徑字串；否則 None（對話模式）。"""
    if not pr or pr.get("mode") != "work":
        return None
    d = Path(pr.get("work_dir") or "")
    return str(d.resolve()) if d.is_absolute() and d.is_dir() else None


def check_work_dir(path):
    """綁定前檢查：不能是磁碟根目錄、使用者資料夾本身、系統資料夾或這個介面自己。回傳錯誤訊息或空字串。"""
    try:
        d = Path(path).resolve()
    except (OSError, ValueError):
        return "路徑格式不對"
    if not d.is_dir():
        return "找不到這個資料夾"
    home = Path.home().resolve()
    bad = [Path(d.anchor), home, home / "Desktop", home / "Documents", home / "Downloads"]
    if d in bad or len(d.parts) <= 2:
        return "範圍太大：請選一個專案自己的資料夾（不要選整個磁碟、桌面、文件或下載）"
    low = str(d).lower()
    if any(k in low for k in (r"\windows", r"\program files", r"\programdata", r"\appdata")):
        return "不能選系統或程式的資料夾"
    if d == ROOT or ROOT in d.parents or d in ROOT.parents:
        return "不能選這個介面自己的資料夾"
    return ""


TEXT_EXTS = {".cpp", ".c", ".h", ".hpp", ".cc", ".py", ".js", ".ts", ".java", ".cs", ".go", ".rs", ".md", ".txt", ".csv",
             ".json", ".yaml", ".yml", ".toml", ".ini", ".typ", ".tex", ".bat", ".ps1", ".sh", ".log", ".m", ".r"}


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
    # 只列出對話還在的副本（對話紀錄被刪掉、但副本還留著時，不要再當成過去的對話給模型看）
    others = [p for p in conv_dir.glob("*.md") if p.stem != (cid or "") and hist_path(p.stem).exists()] \
        if conv_dir.exists() else []
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
MEMORY_PROMPT = """你是專案「{name}」的記憶整理員。這份記憶會放在這個專案每個新對話的開頭，所以要短、要準、要長期有用。
做法參考 claude.ai 的專案記憶：記「綜合後的重點」，不記過程；單次對話的細節不放這裡（過去的對話全文另外存著，AI 需要時會自己搜尋）。

請根據「目前的記憶」和「新的對話內容」輸出更新後的完整記憶，固定用這四個小標題（沒有內容的小標題省略）：
### 使用者
（身分、背景、長期的偏好與要求，例如回答方式、不要直接給答案）
### 專案目標與範圍
（在做什麼、用哪些教材或檔案、目前進度到哪）
### 重要結論與決定
（之後還會用到的結論、已確認的事實、做過的決定；一條一句，不寫推導過程與計算細節）
### 進行中
（還沒完成、下次可能接著做的事；完成了就刪掉）

規則：
- 每條一句話，全部最多 15 條、{limit} 字以內；寧可少記，不要把細節塞進來
- 不要記：公式推導、數值計算過程、程式碼、色碼與排版參數、檔案路徑細節、AI 的解說、寒暄
- 不要記會變動的環境狀態（例如「目前不能執行指令」「某個模型不能用」），那些不是使用者或專案的事實
- 新資訊和舊記憶衝突時以新的為準；過時或已完成的刪掉；目前的記憶太長時，順便精簡到符合上面的規定
- 這次對話沒有值得長期記住的新資訊時，原樣輸出目前的記憶（大部分對話都是這種情況）
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


MEMORY_MAX = 1500          # 記憶的硬上限（字數）；每個新對話都會帶上它，太長會一直多花 token
COMPRESS_PROMPT = """下面是專案「{name}」的記憶，目前 {length} 字，超過上限 {limit} 字。
請把它壓縮到 {target} 字以內：合併重複的條目、刪掉較不重要或已過時的細節，保留使用者背景與偏好、專案目標、已確認的結論與數值、待解決的問題。
格式維持繁體中文 Markdown 條列。只輸出壓縮後的記憶本身，不要任何前言或結語，也不要使用任何工具。

{memory}
"""


def run_agy_text(prompt, name, model=None, retried=False):
    """用記憶模型跑一次 agy，回傳文字結果（失敗時丟出 RuntimeError）。"""
    original = prompt
    if len(prompt) > MAX_ARG:      # 內容太長：存成檔案讓模型讀
        f = WORK / f"{name}.md"
        f.write_text(prompt, encoding="utf-8")
        prompt = f"請用 view_file 完整讀取這個檔案，並嚴格依照檔案裡的指示輸出：\n- {f}"
    cmd = [agy_exe(), "-p", prompt, "--output-format", "json"]
    # 內容很長（存成檔案讓模型讀）時，Flash Low 常常讀完不回傳任何內容（重建記憶、壓縮對話都遇過），改用 Flash High
    model = model or (FALLBACK_MODEL if len(prompt) < 400 and "view_file" in prompt else memory_model())
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
        try:                                    # 記下 agy 的完整回應，方便查原因
            with open(DATA / "agy_fail.log", "a", encoding="utf-8") as f:
                f.write(json.dumps({"t": time.strftime("%Y-%m-%d %H:%M:%S"), "name": name, "model": model,
                                    "status": r.get("status"), "usage": r.get("usage"),
                                    "error": r.get("error"), "denied": r.get("denied_actions")},
                                   ensure_ascii=False) + "\n")
        except OSError:
            pass
        if model != FALLBACK_MODEL and not retried:   # 偶爾會讀完檔案卻沒有輸出：換 Flash High 再試一次
            return run_agy_text(original, name, FALLBACK_MODEL, retried=True)
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


TOKEN_TOTALS = DATA / "token_totals.json"
TOKEN_LOCK = threading.Lock()


def turn_tokens(cid, total):
    """agy 回報的是對話累計 tokens；回傳這次比上次多用的量（第一次、或累計變小時就用整個值）。"""
    with TOKEN_LOCK:
        try:
            totals = json.loads(TOKEN_TOTALS.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            totals = {}
        prev = totals.get(cid, 0)
        totals[cid] = total
        TOKEN_TOTALS.write_text(json.dumps(totals), encoding="utf-8")
    return total - prev if total >= prev else total


def save_turn(cid, model, user_msg, reply, project=None, code=None):
    with HIST_LOCK:
        h = load_hist(cid) or {"id": cid, "title": (user_msg["text"] or "（附件）")[:40],
                               "created": time.time(), "messages": [], "project": project, "code": code}
        h["model"] = model
        h["updated"] = time.time()
        while h["messages"] and h["messages"][-1].get("pending"):   # 開頭先存的「回答中…」換成真正的這一輪
            h["messages"].pop()
            if h["messages"] and h["messages"][-1]["role"] == "user":
                h["messages"].pop()
        h["messages"] += [user_msg, reply]
        write_hist(h)


def save_pending(cid, user_msg, project, code=None):
    """新對話一建立就先存一筆（使用者的訊息 + 回答中…），左側清單馬上看得到；回答完由 save_turn 換成真正的內容。"""
    with HIST_LOCK:
        if load_hist(cid):
            return
        write_hist({"id": cid, "title": (user_msg["text"] or "（附件）")[:40], "created": time.time(),
                    "updated": time.time(), "project": project, "code": code,
                    "messages": [user_msg, {"role": "assistant", "text": "（回答中…）", "pending": True}]})


# ---------- 重問 / 編輯：從某則訊息之前分支出新的對話 ----------
# agy 的非互動模式不能倒回對話，所以把「保留的前半段」整理成背景，開一個新的 agy 對話接著問，
# 完成後用新對話取代舊的（標題、建立時間、專案沿用）。介面上看到的內容就和模型實際記得的一致。
COMPACT_AT = 100_000      # 一輪的輸入超過這麼多 tokens（≈ 對話目前的長度），下一則訊息先壓縮前文
COMPACT_PROMPT = """把下面這段使用者和 AI 的對話整理成「接手用的摘要」，讓另一個 AI 只看摘要就能無縫接著聊。
用繁體中文，固定用這幾個小標題（沒有內容就省略）：
### 使用者的目標
### 已經完成、已經確認的事（結論、答案、使用者確認過的數值或公式，公式用 LaTeX，要精確）
### 使用者的要求與偏好（例如不要直接給答案、格式要求）
### 目前進行到哪、還沒解決的問題
### 用到的檔案（完整路徑；之後需要時可以重新讀）
只寫之後會用到的內容，不要寫寒暄和 AI 的解說過程；總長度 2500 字以內。只輸出摘要本身，不要使用任何工具。

## 對話
{transcript}
"""


def compact_block(messages):
    """壓縮：前面的對話整理成摘要，最近兩輪保留原文，交給新的 agy 對話接手（參考 Claude Code 的 compaction）。"""
    def fmt(ms, limit):
        out = []
        for m in ms:
            t = (m.get("text") or "")[:limit]
            if m.get("files"):
                t += "\n（附件：" + "、".join(m["files"]) + "）"
            out.append(("使用者：" if m["role"] == "user" else "AI：") + t)
        return "\n\n".join(out)
    head, tail = messages[:-4], messages[-4:]
    summary = ""
    if head:
        try:
            summary = run_agy_text(COMPACT_PROMPT.format(transcript=fmt(head, 4000)[-120000:]), "compact", FALLBACK_MODEL).strip()
        except Exception:
            summary = ""
        if not summary:                     # 摘要失敗：退回原本的做法（附上原文）
            return history_block(messages)
    return ("[先前的對話（已壓縮）] 這段對話很長，前面的部分整理成下面的摘要，最近幾輪附上原文。"
            "請把它們當成你和使用者之前的對話，接著回答最後的新訊息；需要細節時可以用 view_file 重新讀附件或檔案。\n\n"
            + (f"## 前面對話的摘要\n{summary}\n\n" if summary else "") + "## 最近幾輪原文\n" + fmt(tail, 8000))


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
        h = {**{k: old.get(k) for k in ("title", "created", "project", "code", "auto_titled")},
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
        ctype = ctype or mimetypes.guess_type(str(path))[0]
        if not ctype and Path(path).suffix.lower() in TEXT_EXTS or ctype in ("text/x-c", "text/x-c++src", "text/x-python"):
            ctype = "text/plain; charset=utf-8"           # 程式碼、文字檔：在分頁裡直接看，不要下載
        self.send_header("Content-Type", ctype or "application/octet-stream")
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
            if q.get("cached", ["0"])[0] == "1" and last_models():   # 開頁面先用上次的清單，不用等 agy
                return self.send_json({"models": last_models(), "replace": "", "error": None})
            stale = None
            try:
                models = list_models(refresh=q.get("refresh", ["0"])[0] == "1")
            except Exception as e:
                models, stale = [], str(e)
            if not models and last_models():            # 暫時連不上：用上次的清單，不要變成「用預設」而不知道是哪個模型
                return self.send_json({"models": last_models(), "replace": "", "stale": True,
                                       "error": "暫時連不上 Google 取得模型清單，先用上次的清單（回答時如果失敗會自動重試）。"})
            try:
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
        if u.path == "/api/code":                 # Lumina Code 的工作區清單
            return self.send_json({"items": [{**w, "exists": bool(ws_dir(w)),
                                              "has_memory": bool(lumina_memory(w))} for w in list_ws()]})
        if u.path == "/api/code/memory":
            w = load_ws(q.get("id", [""])[0])
            if not w:
                return self.send_json({"error": "not found"}, 404)
            return self.send_json({"text": lumina_memory(w), "path": str(Path(ws_dir(w) or "") / LUMINA_MD),
                                   "template": LUMINA_TEMPLATE.format(name=w["name"])})
        if u.path == "/api/permissions":          # 永遠允許的清單與最近的授權紀錄
            try:
                log = [json.loads(l) for l in PERM_LOG.read_text(encoding="utf-8").splitlines()[-60:] if l.strip()]
            except (OSError, ValueError):
                log = []
            return self.send_json({**load_perms(), "log": log[::-1]})
        if u.path == "/api/usage":
            if q.get("cached", ["0"])[0] == "1":     # 開頁面先顯示上次的數值，不用等 agy
                try:
                    return self.send_json(json.loads(USAGE_CACHE.read_text(encoding="utf-8")))
                except (OSError, ValueError):
                    return self.send_json({})
            try:
                data = get_usage()
                if data.get("groups"):
                    USAGE_CACHE.write_text(json.dumps({**data, "cached_at": time.time()}, ensure_ascii=False),
                                           encoding="utf-8")
                return self.send_json(data)
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
            code = q.get("code", [""])[0]      # 指定工作區：只列這個工作區的工作階段
            items = []
            for p in HISTORY.glob("*.json"):
                try:
                    h = json.loads(p.read_text(encoding="utf-8"))
                    if ((h.get("code") or "") == code) and (code or (h.get("project") or "") == want):
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
        if u.path == "/api/permission/hook":      # agy 的 hook（透過 curl）送來的工具呼叫
            try:
                payload = self.body()
            except ValueError:
                return self.send_json({"decision": "deny", "reason": "無法讀取工具呼叫"})
            return self.send_json(decide_tool(payload))
        if u.path == "/api/permission/answer":    # 介面上的授權卡：要帶事件裡的一次性 nonce（模型的指令拿不到）
            d = self.body()
            with PERM_LOCK:
                r = PENDING.get(str(d.get("id")))
                if not r or d.get("nonce") != r["nonce"]:
                    return self.send_json({"ok": False}, 403)
                if d.get("choice") == "extend":       # 延長等待，不算回答
                    r["deadline"] = min(r["deadline"] + 300, r["started"] + PERM_MAX)
                    return self.send_json({"ok": True, "left": int(r["deadline"] - time.time())})
                r["answer"] = {"choice": d.get("choice"), "reason": d.get("reason", "")}
                r["event"].set()
            return self.send_json({"ok": True})
        if u.path == "/api/code/create":          # 新增工作區：綁定一個資料夾
            d = self.body()
            err = check_work_dir(str(d.get("work_dir") or ""))
            if err:
                return self.send_json({"ok": False, "message": err})
            wd = str(Path(d["work_dir"]).resolve())
            same = next((w for w in list_ws() if w.get("work_dir") == wd), None)
            if same:
                return self.send_json({"ok": True, **same})
            w = {"id": uuid.uuid4().hex[:12], "name": Path(wd).name or wd, "work_dir": wd, "created": time.time()}
            save_ws(w)
            sync_skills(code_dir(w["id"]))
            return self.send_json({"ok": True, **w})
        if u.path == "/api/code/delete":          # 移除工作區：資料夾本身不動，只刪掉 Lumina 裡的工作階段紀錄
            w = load_ws(self.body().get("id"))
            if w:
                for f in HISTORY.glob("*.json"):
                    try:
                        if json.loads(f.read_text(encoding="utf-8")).get("code") == w["id"]:
                            f.unlink()
                    except (OSError, ValueError):
                        pass
                import shutil
                shutil.rmtree(code_dir(w["id"]), ignore_errors=True)
            return self.send_json({"ok": True})
        if u.path == "/api/code/memory":          # 編輯 LUMINA.md（存在使用者的資料夾裡）
            d = self.body()
            w = load_ws(d.get("id"))
            if not w or not ws_dir(w):
                return self.send_json({"ok": False, "message": "找不到工作區的資料夾"})
            (Path(ws_dir(w)) / LUMINA_MD).write_text(str(d.get("text") or "")[:LUMINA_MAX], encoding="utf-8")
            return self.send_json({"ok": True})
        if u.path == "/api/checkpoint/restore":   # 把資料夾還原成某一輪開始前的樣子
            try:
                restored, moved = checkpoints.restore(str(self.body().get("id") or ""))
            except Exception as e:
                return self.send_json({"ok": False, "message": f"復原失敗：{e}"})
            return self.send_json({"ok": True, "message": f"已復原 {restored} 個檔案，"
                                   f"移走 {moved} 個這輪新增的檔案（放在 data\\checkpoints\\trash）"})
        if u.path == "/api/permissions":          # 管理「永遠允許」（只能刪除；新增只能從授權卡）
            d, p = self.body(), load_perms()
            if d.get("remove") in p["always"]:
                p["always"].remove(d["remove"])
                save_perms(p)
            return self.send_json(p)
        if u.path == "/api/stop":
            rid = self.body().get("run_id")
            STOPPED.add(rid)
            for k, v in list(ACTIVE.items()):
                if v["run_id"] == rid:
                    deny_pending(k)
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
            if d.get("mode") in ("chat", "work"):
                if d["mode"] == "work":
                    err = check_work_dir(str(d.get("work_dir") or ""))
                    if err:
                        return self.send_json({"ok": False, "message": f"工作資料夾：{err}"})
                    pr["work_dir"] = str(Path(d["work_dir"]).resolve())
                pr["mode"] = d["mode"]
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
        # 舊分頁還選著已下架的模型：直接改用替代模型，不浪費時間重試；沒選模型就用預設，不要交給 agy 自己挑
        asked, model = model, replacement_model(model, available_ids()) or FALLBACK_MODEL
        ws = load_ws(d.get("code"))          # Lumina Code 的工作區（和專案互斥）
        pr = None if ws else load_project(d.get("project"))
        # 重問 / 編輯：fork = {"from": 舊對話 id, "keep": 保留前幾則訊息}
        fork, old, prior = d.get("fork"), None, []
        compacting = False
        if cid and not fork:                # 上一輪發現對話太長：這次先壓縮前文，開新的 agy 對話接手
            cur = load_hist(cid)
            if cur and cur.get("compact") and cur.get("messages"):
                old, prior, cid, compacting = cur, cur["messages"], None, True
                ws = load_ws(cur.get("code")) or ws
                pr = None if ws else (load_project(cur.get("project")) or pr)
        if fork:
            old = load_hist(fork.get("from") or "")
            if old:
                keep = max(0, min(int(fork.get("keep") or 0), len(old["messages"])))
                prior = old["messages"][:keep - keep % 2]        # 保持「使用者／AI」成對
                cid = None
                ws = load_ws(old.get("code"))
                pr = None if ws else load_project(old.get("project"))
        is_new = not cid and not old
        instructions = None
        if not cid:   # 新對話：附上常駐指示（＋專案指示）
            instructions = load_settings()["instructions"]
            if pr and pr.get("instructions", "").strip():
                instructions += f"\n\n[這個專案的指示]\n{pr['instructions'].strip()}"
            if pr and pr.get("memory", "").strip():
                instructions += ("\n\n[專案記憶] 以下是之前在這個專案的其他對話中累積的重點（可能不完整，"
                                 f"和使用者現在說的衝突時以使用者為準）：\n{clip_memory(pr['memory'].strip())}")
            if ws and ws_dir(ws):
                instructions += env_block(ws)
            mem = lumina_memory(ws) if ws else ""
            if mem:
                instructions += LUMINA_HINT.format(path=Path(ws_dir(ws)) / LUMINA_MD, content=mem)
            if prior:
                instructions += "\n\n" + (compact_block(prior) if compacting or len(prior) > 12 else history_block(prior))
        wd = ws_dir(ws) if ws else None
        extra_hint = ((SEARCH_HINT if d.get("search") else "") + TOOLS_HINT
                      + (WORK_HINT.format(dir=wd) if wd else OUTPUT_HINT.format(dir=out_dir(pr))))
        prompt = build_prompt(text + extra_hint, files, instructions,
                              project_block(pr, cid or (old and old["id"])) if pr else "")
        # agy 1.3.3 起讀工作區以外的檔案需要授權（非互動模式會直接拒絕），
        # 專案對話在專案資料夾裡跑，附件卻在 uploads：把 uploads 加進工作區
        # output：讓 AI 能把產生的檔案存進去。主目錄在 agy 的「信任工作區」裡，檔案修改預設要人工審查，
        # 非互動模式沒辦法審查就會被拒；accept-edits 只對工作區（cwd + --add-dir）內有效，主目錄其他地方照樣不能改
        work_dir = wd                           # Lumina Code：模型可以修改這個資料夾、執行指令（每次詢問）
        cmd = [agy_exe(), "-p", prompt, "--output-format", "stream-json",
               "--add-dir", str(UPLOADS), "--add-dir", str(OUTPUT), "--mode", "accept-edits"]
        if work_dir:
            cmd += ["--add-dir", work_dir]
        if model:
            cmd += ["--model", model]
        # 不傳 --effort：模型 ID 本身已含強度（如 -high），另外加會衝突，Claude 也不支援
        if cid:
            cmd += ["--conversation", cid]

        run_id = uuid.uuid4().hex
        started = time.time()                       # 用來找出這輪 Nano Banana 新生成的圖片
        # 專案對話在專案資料夾裡跑，模型也能自己列出/搜尋檔案；工作區在自己的內部資料夾跑（放 hooks，不弄髒使用者的資料夾）
        cwd = code_dir(ws["id"]) if ws else (proj_dir(pr["id"]) if pr else WORK)
        self.send_response(200)
        self.send_header("Content-Type", "application/x-ndjson; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()

        emit_lock = threading.Lock()

        timeline = []                  # 這輪的過程（思考、工具、授權），依發生順序存進對話紀錄

        def emit(obj):                 # 授權請求會從另一個執行緒送進來，所以要上鎖
            ev = obj.get("event")
            su = obj.get("step_update") or {}
            if su.get("step_type") == "tool" and su.get("state") == "ACTIVE":
                timeline.append({"k": "tool", "tool": su.get("tool_name"),
                                 "params": (su.get("tool_info") or {}).get("parameters")})
            elif ev == "thinking":
                timeline.append({"k": "think", "text": obj.get("text")})
            elif ev == "permission_done":
                timeline.append({"k": "perm", "command": obj.get("command"), "decision": obj.get("decision"),
                                 "note": obj.get("note")})
            with emit_lock:
                try:
                    self.wfile.write((json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8"))
                    self.wfile.flush()
                except OSError:
                    pass

        write_hooks(cwd)               # 每次都重寫：模型就算改掉了 hooks.json 也會被還原
        write_mcp(cwd, work_dir)       # Lumina 的工具伺服器（Code 區才帶工作資料夾：截圖、虛擬環境只能在那裡用）
        try:                           # 檢查點：記下模型這輪可以改到的資料夾，改壞了可以復原
            snap = checkpoints.take([work_dir] if work_dir else [cwd, out_dir(pr)])
        except Exception:
            snap = None
        if cid:
            ACTIVE[cid] = {"emit": emit, "cwd": str(cwd), "run_id": run_id, "work_dir": work_dir}
        emit({"event": "run", "run_id": run_id})
        if compacting:
            emit({"event": "compacted", "count": len(prior)})
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
            busy = b"UNAVAILABLE" in first and b"503" in first     # Google 暫時忙碌（常見，過幾秒就好）
            if b"invalid model selection" not in first and not busy:
                break                          # 正常開始，往下照常串流
            rest = proc.stdout.read()
            proc.wait()
            text_out = (first + rest).decode("utf-8", errors="replace")
            if attempt == MODEL_RETRIES or run_id in STOPPED:
                proc = None
                pre_output = text_out
                break
            emit({"event": "retry", "attempt": attempt, "max": MODEL_RETRIES, "busy": busy})
            time.sleep(4 if busy else 2)

        reply, steps, new_cid, raw_other, denied = "", [], cid, [], []
        compact_next = False
        think_stop, thoughts = [], []          # 思考過程：讀 agy 紀錄檔的執行緒何時停止、讀到的內容
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
                ACTIVE[new_cid] = {"emit": emit, "cwd": str(cwd), "run_id": run_id, "work_dir": work_dir}
                if is_new and new_cid:          # 新對話：馬上出現在左側清單，標題也先用問題產生
                    save_pending(new_cid, {"role": "user", "text": text, "files": files, "t": started},
                                 pr["id"] if pr else None, ws["id"] if ws else None)
                    threading.Thread(target=auto_title, args=(new_cid, text, ""), daemon=True).start()
                    emit({"event": "conv_created", "conversation_id": new_cid})
                if not think_stop:              # 開始讀 agy 的紀錄檔，把思考過程即時送到畫面
                    threading.Thread(target=tail_thinking, args=(new_cid, emit, think_stop, thoughts), daemon=True).start()
                    think_stop.append(True)
            su = ev.get("step_update") or {}
            if su.get("step_type") == "agent_response" and su.get("text_delta"):
                reply += su["text_delta"]
            if su.get("step_type") == "tool" and su.get("state") == "ACTIVE":
                steps.append({"tool": su.get("tool_name"), "params": (su.get("tool_info") or {}).get("parameters")})
            if ev.get("event") == "result":
                r = ev.get("result") or {}
                new_cid = r.get("conversation_id") or new_cid
                # agy 的 tokens 是整段對話的累計值：記下上次的累計，換算成這次用掉的量
                u = r.get("usage") or {}
                if new_cid and isinstance(u.get("total_tokens"), int):
                    u["turn_tokens"] = turn_tokens(new_cid, u["total_tokens"])
                    compact_next = u["turn_tokens"] > COMPACT_AT
                reply = r.get("response") or reply
                denied = [a.get("display_name") or a.get("action") for a in r.get("denied_actions") or []]
            emit(ev)
        if proc is not None:
            proc.wait()
            returncode = proc.returncode
        with RUNS_LOCK:
            RUNS.pop(run_id, None)
        think_stop.append("done")             # 停止讀思考過程
        STOPPED.discard(run_id)
        for k in [k for k, v in ACTIVE.items() if v["run_id"] == run_id]:
            ACTIVE.pop(k, None)

        # Nano Banana 生成的圖片：從 agy 的暫存搬到 output，直接接在回答後面顯示
        images = collect_images(new_cid, started, Path(work_dir) if work_dir else out_dir(pr)) if new_cid else []
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
        # 工作模式：改過的程式檔自動編譯檢查，有錯就在背景請模型修（最多 2 次），像 PDF 的編譯修正
        if work_dir and snap and new_cid and returncode == 0:
            try:
                touched = [Path(c["path"]) for c in checkpoints.changes(snap) if c["kind"] != "deleted"]
            except Exception:
                touched = []
            notes = []
            for f in [p for p in touched if p.suffix.lower() in CODE_CHECKS and command_guard._inside(p, [Path(work_dir)])]:
                for attempt in (0, 1, 2):
                    err = check_code(f)
                    if err is None or not err:
                        if attempt:
                            notes.append(f"> ⚙ {f.name} 第一次編譯有錯誤，已自動修正並重新檢查通過。")
                        break
                    if attempt == 2:
                        notes.append(f"> ⚠ {f.name} 自動修正 2 次後仍無法編譯：{err.splitlines()[0][:150]}")
                        break
                    emit({"event": "code_fixing", "attempt": attempt + 1, "name": f.name})
                    fix_code(new_cid, cwd, model, f, err, work_dir)
            if notes:
                reply = reply.rstrip() + "\n\n" + "\n".join(notes)
                emit({"event": "pdf", "results": [], "reply": reply})
        # 檢查點：這輪改了哪些檔案（回答下方顯示，可以一鍵復原）
        cp = None
        if snap:
            try:
                ch = checkpoints.changes(snap)
                if ch:
                    cp = {"id": snap, "count": len(ch), "changes": ch[:60]}
                    emit({"event": "checkpoint", **cp})
            except Exception:
                pass
        if new_cid and (reply or steps or old):
            user_msg = {"role": "user", "text": text, "files": files, "t": started}
            reply_msg = {"role": "assistant", "text": reply or FAILED_REPLY, "steps": steps, "t": time.time()}
            if images:
                reply_msg["images"] = [str(p) for p in images]
            if cp:
                reply_msg["checkpoint"] = cp
            if timeline:
                reply_msg["timeline"] = timeline[-200:]
            if old:
                save_fork(old, prior, new_cid, model, user_msg, reply_msg)
                if old["id"] != new_cid:
                    drop_brain(old["id"])          # 被取代的分支：agy 的暫存也清掉
            else:
                save_turn(new_cid, model, user_msg, reply_msg, pr["id"] if pr else None, ws["id"] if ws else None)
            if compact_next:                   # 對話已經很長：記下來，下一則訊息先壓縮前文
                with HIST_LOCK:
                    h = load_hist(new_cid)
                    if h:
                        h["compact"] = True
                        write_hist(h)
                emit({"event": "compact_soon"})
            # 新對話，或重問／編輯第一則（使用者沒自己取過名稱時）：背景用 Flash 取標題
            if reply and not is_new and old and not prior and not user_named(old):   # 新對話的標題在開頭就產生了
                threading.Thread(target=auto_title, args=(new_cid, text, reply), daemon=True).start()
            # 專案對話：背景整理記憶，不影響這次回覆
            if pr and pr.get("memory_auto", True) and reply and returncode == 0:
                threading.Thread(target=update_memory, args=(pr["id"], turn_material(text, reply)),
                                 daemon=True).start()
        elif is_new and new_cid:                # 新對話開頭先存了「回答中…」，結果沒有任何回答：換成失敗的提示
            with HIST_LOCK:
                h = load_hist(new_cid)
                if h and h["messages"] and h["messages"][-1].get("pending"):
                    h["messages"][-1] = {"role": "assistant", "text": FAILED_REPLY, "t": time.time()}
                    write_hist(h)
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
    threading.Thread(target=migrate_shortcut, daemon=True).start()
    install_lumina()
    migrate_work_projects()
    threading.Thread(target=agy_self_update, daemon=True).start()
    threading.Thread(target=retitle_legacy, daemon=True).start()
    if "--no-browser" not in sys.argv:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    print(f"Lumina：{url}")
    srv.serve_forever()


if __name__ == "__main__":
    main()
