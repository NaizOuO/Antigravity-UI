"""Lumina 的工具伺服器（MCP，stdio，只用 Python 內建功能）。

agy 啟動工作階段時會依 .agents/mcp_config.json 啟動這支程式，模型就能直接呼叫這些工具。
同一套工具之後也能給本地模型的後端使用（MCP 是公開標準）。

設計原則（參考 Claude Code）：
- 每個工具的說明自己講清楚「什麼時候用、怎麼用、什麼叫做好」，不靠開頭的系統提示
- 工具回傳結果時附上當下的提醒（例如截圖後要寫出檢查了什麼）
- 這裡也會自己檢查路徑；要不要詢問使用者由伺服器的 hook 決定（見 server.py 的 decide_lumina_tool）

參數：--roots 允許讀取的資料夾（; 分隔） --work 工作資料夾（Code 區才有；截圖、虛擬環境只能在這裡用）
"""

import argparse
import contextlib
import ctypes
import io
import json
import re
import threading
import subprocess
import sys
import time
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import lumina  # noqa: E402  讀 PDF 的功能沿用 lumina.py

ap = argparse.ArgumentParser()
ap.add_argument("--roots", default="")
ap.add_argument("--work", default="")
ARGS, _ = ap.parse_known_args()
ROOTS = [Path(r).resolve() for r in ARGS.roots.split(";") if r]
WORK = Path(ARGS.work).resolve() if ARGS.work else None
SHOTS = lumina.PAGES_DIR                    # output/.lumina：模型讀得到的地方

# ---------------------------------------------------------------- 工具說明（模型看到的就是這些）
PDF_FILE = {"type": "string", "description": "PDF 的完整路徑（附件或專案資料夾裡的檔案）"}
PAGES = {"type": "string", "description": "PDF 的第幾頁（從 1 開始，不是書上印的頁碼），例如 \"63\"、\"63-65\"、\"5,9\""}
TOOLS = [
    {"name": "pdf_info", "description":
        "看一份 PDF 的基本資訊：總頁數、書籤目錄、書上印的頁碼和 PDF 頁碼的差、哪些頁是掃描圖片。"
        "要找「課本 p.63」「第 3 章」之類的位置時先用這個。讀 PDF 一律用 pdf_ 系列工具，不要用 view_file 讀整本、也不要自己寫程式讀。",
     "inputSchema": {"type": "object", "properties": {"file": PDF_FILE}, "required": ["file"]}},
    {"name": "pdf_find", "description":
        "在 PDF 全文搜尋文字（例如習題編號 \"3.4\"、關鍵字），回傳在 PDF 第幾頁和前後文。掃描的頁面沒有文字層，搜尋不到時改用 pdf_thumbs。",
     "inputSchema": {"type": "object", "properties": {"file": PDF_FILE, "query": {"type": "string"}}, "required": ["file", "query"]}},
    {"name": "pdf_text", "description": "取出指定頁的文字（一次最多 15 頁）。只讀需要的頁，不要整本讀。",
     "inputSchema": {"type": "object", "properties": {"file": PDF_FILE, "pages": PAGES}, "required": ["file", "pages"]}},
    {"name": "pdf_page", "description":
        "把指定頁畫成清楚的圖片（一次最多 4 頁），回傳圖片路徑，再用 view_file 打開來看。"
        "公式、圖表、掃描頁一定要看這個；抄題目或公式時以這張圖為準。",
     "inputSchema": {"type": "object", "properties": {"file": PDF_FILE, "pages": PAGES}, "required": ["file", "pages"]}},
    {"name": "pdf_thumbs", "description":
        "把最多 12 頁縮小排成一張總覽圖，用來在掃描檔裡快速找到目標在哪幾頁（不要一頁一頁翻）。"
        "縮圖只能用來找位置，題目、公式、數字一律再用 pdf_page 看清楚的版本。",
     "inputSchema": {"type": "object", "properties": {"file": PDF_FILE, "pages": PAGES}, "required": ["file", "pages"]}},
    {"name": "run_capture", "description":
        "（只有 Lumina Code 的工作資料夾能用）在一個新的視窗執行工作資料夾裡的程式，等幾秒後截下那個視窗的畫面，然後自動關閉程式。"
        "用來確認畫面真的對：文字有沒有亂碼、版面、繪圖結果。會一直跑的程式（時鐘、遊戲、伺服器）或需要鍵盤輸入的程式，"
        "都用這個看結果，不要用 run_command 直接執行（會卡住停不下來）。使用者會先看到詢問。"
        "看完截圖後，回答裡一定要具體寫出你檢查了哪些地方、各自是否正確；看到問題就修正後再截一次，不要只說「看起來沒問題」。",
     "inputSchema": {"type": "object", "properties": {
         "program": {"type": "string", "description": "工作資料夾裡要執行的程式（.exe、.bat、.py），相對路徑或完整路徑"},
         "args": {"type": "array", "items": {"type": "string"}, "description": "傳給程式的參數"},
         "seconds": {"type": "number", "description": "執行多久後截圖（1～15 秒，預設 3）"},
         "utf8": {"type": "boolean", "description": "主控台程式要不要先切成 UTF-8（chcp 65001）。預設 false＝跟使用者平常執行的環境一樣"},
         "input": {"type": "string", "description": "程式需要鍵盤輸入時，先準備好要輸入的內容，一行代表一次輸入加 Enter（例如按兩次 Enter 再輸入 3：兩個空行加上一行 3）。"
                   "用的是標準輸入，所以只對讀標準輸入（cin、input()）的程式有效"}},
         "required": ["program"]}},
    {"name": "venv_create", "description":
        "（只有 Lumina Code 能用）在工作資料夾建立 Python 虛擬環境 .venv。Python 專案要安裝套件前先建立，"
        "之後一律用 .venv\\Scripts\\python 執行程式，不要裝到電腦全域的 Python。已經有 .venv 就不用再建。",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "venv_install", "description":
        "（只有 Lumina Code 能用）用工作資料夾的 .venv 安裝 Python 套件（會連網下載，使用者每次都會先看到詢問）。"
        "只裝真的需要的套件，用官方名稱（例如 numpy、pygame），不要裝來路不明的套件或網址。",
     "inputSchema": {"type": "object", "properties": {
         "packages": {"type": "array", "items": {"type": "string"}, "description": "套件名稱，可以帶版本，例如 numpy、pygame==2.6.1"}},
         "required": ["packages"]}},
]


# ---------------------------------------------------------------- 共用
class ToolError(Exception):
    pass


def inside(p, roots):
    try:
        p = Path(p).resolve()
    except (OSError, ValueError):
        return False
    return any(p == r or r in p.parents for r in roots)


def need_file(f, roots, exts=None):
    p = Path(f)
    if not p.is_absolute():
        p = (WORK or Path.cwd()) / p
    if not inside(p, roots):
        raise ToolError(f"{p} 不在可以使用的資料夾裡")
    if not p.is_file():
        raise ToolError(f"找不到檔案：{p}")
    if exts and p.suffix.lower() not in exts:
        raise ToolError(f"這個工具只接受 {'、'.join(exts)} 檔案")
    return p


def need_work():
    if not WORK or not WORK.is_dir():
        raise ToolError("這個工具只能在 Lumina Code 的工作資料夾裡使用（目前是對話區）。")
    return WORK


def call_lumina(fn, *args):
    """沿用 lumina.py 的功能：它用 print 輸出、用 sys.exit 表示錯誤。"""
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            fn(*args)
    except SystemExit:
        pass
    return buf.getvalue().strip()


# ---------------------------------------------------------------- 截圖（Windows：PrintWindow，不用另外安裝東西）
def _win():
    u, g = ctypes.windll.user32, ctypes.windll.gdi32
    vp = ctypes.c_void_p
    for f, res, args in [(u.GetWindowDC, vp, [vp]), (u.ReleaseDC, ctypes.c_int, [vp, vp]),
                         (g.CreateCompatibleDC, vp, [vp]), (g.CreateCompatibleBitmap, vp, [vp, ctypes.c_int, ctypes.c_int]),
                         (g.SelectObject, vp, [vp, vp]), (g.DeleteObject, ctypes.c_int, [vp]), (g.DeleteDC, ctypes.c_int, [vp]),
                         (u.PrintWindow, ctypes.c_int, [vp, vp, ctypes.c_uint]),
                         (g.BitBlt, ctypes.c_int, [vp, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, vp, ctypes.c_int, ctypes.c_int, ctypes.c_uint]),
                         (g.GetDIBits, ctypes.c_int, [vp, vp, ctypes.c_uint, ctypes.c_uint, vp, vp, ctypes.c_uint])]:
        f.restype, f.argtypes = res, args
    return u, g


def process_tree(root):
    """root 和它所有子孫程序的 pid（用 Toolhelp 快照）。"""
    import ctypes.wintypes as W

    class PE(ctypes.Structure):
        _fields_ = [("dwSize", W.DWORD), ("cntUsage", W.DWORD), ("th32ProcessID", W.DWORD), ("th32DefaultHeapID", ctypes.c_void_p),
                    ("th32ModuleID", W.DWORD), ("cntThreads", W.DWORD), ("th32ParentProcessID", W.DWORD),
                    ("pcPriClassBase", ctypes.c_long), ("dwFlags", W.DWORD), ("szExeFile", ctypes.c_wchar * 260)]
    k = ctypes.windll.kernel32
    k.CreateToolhelp32Snapshot.restype = ctypes.c_void_p
    snap = k.CreateToolhelp32Snapshot(2, 0)
    pairs, e = [], PE()
    e.dwSize = ctypes.sizeof(PE)
    if k.Process32FirstW(ctypes.c_void_p(snap), ctypes.byref(e)):
        while True:
            pairs.append((e.th32ProcessID, e.th32ParentProcessID))
            if not k.Process32NextW(ctypes.c_void_p(snap), ctypes.byref(e)):
                break
    k.CloseHandle(ctypes.c_void_p(snap))
    tree, grew = {root}, True
    while grew:
        grew = False
        for pid, ppid in pairs:
            if ppid in tree and pid not in tree:
                tree.add(pid); grew = True
    return tree


def find_windows(pids, title_prefix):
    import ctypes.wintypes as W
    u = ctypes.windll.user32
    found = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, W.HWND, W.LPARAM)
    def cb(h, _):
        if u.IsWindowVisible(h):
            pid = W.DWORD()
            u.GetWindowThreadProcessId(h, ctypes.byref(pid))
            buf = ctypes.create_unicode_buffer(512)
            u.GetWindowTextW(h, buf, 512)
            if pid.value in pids or buf.value.startswith(title_prefix):
                r = W.RECT()
                u.GetWindowRect(h, ctypes.byref(r))
                if r.right - r.left > 50 and r.bottom - r.top > 50:
                    found.append(h)
        return True
    u.EnumWindows(cb, 0)
    return found


DESKTOP = "LuminaCapture"


def hidden_desktop():
    """一個看不見的 Windows 桌面（參考 Ghost MCP 的做法）：程式開在這裡，不會出現在使用者的螢幕、工作列，也搶不到焦點。"""
    u = ctypes.windll.user32
    u.CreateDesktopW.restype = ctypes.c_void_p
    u.CreateDesktopW.argtypes = [ctypes.c_wchar_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint, ctypes.c_uint, ctypes.c_void_p]
    h = u.CreateDesktopW(DESKTOP, None, None, 0, 0x10000000, None)        # GENERIC_ALL
    if not h:
        raise ToolError("無法建立隱藏桌面")
    return h


def start_on_desktop(cmdline, cwd):
    """在隱藏桌面上啟動程式，回傳 pid。"""
    import ctypes.wintypes as W

    class SI(ctypes.Structure):
        _fields_ = [("cb", W.DWORD), ("lpReserved", W.LPWSTR), ("lpDesktop", W.LPWSTR), ("lpTitle", W.LPWSTR),
                    ("dwX", W.DWORD), ("dwY", W.DWORD), ("dwXSize", W.DWORD), ("dwYSize", W.DWORD),
                    ("dwXCountChars", W.DWORD), ("dwYCountChars", W.DWORD), ("dwFillAttribute", W.DWORD),
                    ("dwFlags", W.DWORD), ("wShowWindow", W.WORD), ("cbReserved2", W.WORD), ("lpReserved2", ctypes.c_void_p),
                    ("hStdInput", W.HANDLE), ("hStdOutput", W.HANDLE), ("hStdError", W.HANDLE)]

    class PI(ctypes.Structure):
        _fields_ = [("hProcess", W.HANDLE), ("hThread", W.HANDLE), ("dwProcessId", W.DWORD), ("dwThreadId", W.DWORD)]
    si, pi = SI(), PI()
    si.cb = ctypes.sizeof(SI)
    si.lpDesktop = "WinSta0\\" + DESKTOP
    buf = ctypes.create_unicode_buffer(cmdline)
    k = ctypes.windll.kernel32
    if not k.CreateProcessW(None, buf, None, None, False, 0x10, None, str(cwd), ctypes.byref(si), ctypes.byref(pi)):  # CREATE_NEW_CONSOLE
        raise ToolError("無法啟動程式")
    k.CloseHandle(pi.hThread); k.CloseHandle(pi.hProcess)
    return pi.dwProcessId


def desktop_windows(hdesk, pids, title_prefix):
    import ctypes.wintypes as W
    u = ctypes.windll.user32
    found = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, W.HWND, W.LPARAM)
    def cb(h, _):
        pid = W.DWORD()
        u.GetWindowThreadProcessId(h, ctypes.byref(pid))
        buf = ctypes.create_unicode_buffer(512)
        u.GetWindowTextW(h, buf, 512)
        # 隱藏桌面是 Lumina 專用的：上面看得到的視窗都是這次啟動的程式（Python 虛擬環境會透過轉接程式再啟動，
        # 父子關係會斷掉、pygame 也會改標題，所以不能只靠 pid 或標題找）
        if u.IsWindowVisible(h) and (pids is None or pid.value in pids or buf.value.startswith(title_prefix)):
            r = W.RECT()
            u.GetWindowRect(h, ctypes.byref(r))
            if r.right - r.left > 50 and r.bottom - r.top > 50:
                found.append(h)
        return True
    u.EnumDesktopWindows(ctypes.c_void_p(hdesk), cb, 0)
    return found


def grab(hwnd, out):
    import ctypes.wintypes as W
    u, g = _win()
    r = W.RECT()
    ctypes.windll.user32.GetWindowRect(ctypes.c_void_p(hwnd), ctypes.byref(r))
    w, h = r.right - r.left, r.bottom - r.top
    hdc = u.GetWindowDC(hwnd)
    mdc = g.CreateCompatibleDC(hdc)
    bmp = g.CreateCompatibleBitmap(hdc, w, h)
    g.SelectObject(mdc, bmp)
    if not u.PrintWindow(hwnd, mdc, 2):                 # PW_RENDERFULLCONTENT；不行就直接從螢幕複製
        g.BitBlt(mdc, 0, 0, w, h, hdc, 0, 0, 0x00CC0020)

    class BIH(ctypes.Structure):
        _fields_ = [("biSize", W.DWORD), ("biWidth", ctypes.c_long), ("biHeight", ctypes.c_long), ("biPlanes", W.WORD),
                    ("biBitCount", W.WORD), ("biCompression", W.DWORD), ("biSizeImage", W.DWORD), ("biXPelsPerMeter", ctypes.c_long),
                    ("biYPelsPerMeter", ctypes.c_long), ("biClrUsed", W.DWORD), ("biClrImportant", W.DWORD)]
    bi = BIH(40, w, -h, 1, 24, 0, 0, 0, 0, 0, 0)
    stride = (w * 3 + 3) // 4 * 4
    buf = ctypes.create_string_buffer(stride * h)
    g.GetDIBits(mdc, bmp, 0, h, buf, ctypes.byref(bi), 0)
    g.DeleteObject(bmp); g.DeleteDC(mdc); u.ReleaseDC(hwnd, hdc)
    raw, rows = buf.raw, []
    for y in range(h):
        row = bytearray(raw[y * stride: y * stride + w * 3])
        row[0::3], row[2::3] = row[2::3], row[0::3]       # BGR → RGB
        rows.append(bytes(row))
    lumina.write_png(out, w, h, b"".join(rows), w * 3)
    return w, h


# ---------------------------------------------------------------- 工具實作
def t_pdf(name, a):
    f = need_file(a.get("file", ""), ROOTS + ([WORK] if WORK else []), [".pdf"])
    fn = {"pdf_info": lumina.cmd_info, "pdf_find": lumina.cmd_find, "pdf_text": lumina.cmd_text,
          "pdf_page": lumina.cmd_page, "pdf_thumbs": lumina.cmd_thumbs}[name]
    extra = [] if name == "pdf_info" else [str(a.get("query") if name == "pdf_find" else a.get("pages", ""))]
    out = call_lumina(fn, str(f), *extra)
    if "LUMINA_NEED_COMPONENT" in out:
        raise ToolError("讀 PDF 的元件（PDFium）還沒下載。請告訴使用者：到「⚙ 設定 → 元件」下載後再問一次。不要改用 view_file 讀整本。")
    if name == "pdf_page":
        out += "\n（提醒：抄寫題目、公式、數字以這些圖片為準，一定要用 view_file 打開來看。）"
    return out


def _alive(pid):
    h = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)        # PROCESS_QUERY_LIMITED_INFORMATION
    if not h:
        return False
    code = ctypes.c_ulong()
    ctypes.windll.kernel32.GetExitCodeProcess(h, ctypes.byref(code))
    ctypes.windll.kernel32.CloseHandle(h)
    return code.value == 259                                           # STILL_ACTIVE


def t_run_capture(a):
    work = need_work()
    prog = need_file(a.get("program", ""), [work], [".exe", ".bat", ".cmd", ".py"])
    args = [str(x) for x in (a.get("args") or [])]
    secs = min(15.0, max(1.0, float(a.get("seconds") or 3)))
    tag = "LUMINA-" + uuid.uuid4().hex[:6]
    ctypes.windll.user32.SetProcessDPIAware()
    rel = str(prog.relative_to(work))                   # 在工作資料夾裡執行，用相對路徑（避免引號和外層引號黏在一起被 cmd 解析錯）
    if prog.suffix.lower() == ".py":
        venv_py = work / ".venv" / "Scripts" / "python.exe"
        cmdline = subprocess.list2cmdline([str(venv_py if venv_py.exists() else Path(sys.executable)), rel] + args)
    else:
        cmdline = subprocess.list2cmdline([".\\" + rel] + args)
    # 用 conhost 開一個傳統的主控台視窗（不交給 Windows Terminal），標題設成 tag 方便找到這個視窗
    inner = f'title {tag}' + (" & chcp 65001>nul" if a.get("utf8") else "") + f" & {cmdline}"
    result = {}

    def work_on_hidden_desktop():
        hdesk = hidden_desktop()
        ctypes.windll.user32.SetThreadDesktop(ctypes.c_void_p(hdesk))
        pid = start_on_desktop(f'conhost.exe cmd.exe /d /s /c "{inner} "', work)   # /s：保留路徑裡的引號（有空格的資料夾）
        result["pid"] = pid
        wins = []
        try:
            if a.get("input"):                          # 需要鍵盤輸入：等視窗出來，一個字一個字打進去（視窗訊息，不動使用者的鍵盤）
                for _ in range(30):
                    time.sleep(0.2)
                    wins = desktop_windows(hdesk, None, tag)
                    if wins:
                        break
                time.sleep(0.5)
                for hw in wins[:1]:
                    for ch in str(a["input"]).replace("\r\n", "\n"):
                        ctypes.windll.user32.PostMessageW(ctypes.c_void_p(hw), 0x0102, 13 if ch == "\n" else ord(ch), 0)
                        time.sleep(0.05)
            time.sleep(secs)
            alive = _alive(pid) or bool(desktop_windows(hdesk, None, tag))
            wins = desktop_windows(hdesk, None, tag)
            SHOTS.mkdir(parents=True, exist_ok=True)
            shots = []
            for i, hw in enumerate(wins[:3]):
                out = SHOTS / f"capture_{prog.stem}_{int(time.time())}_{i}.png"
                w, h = grab(hw, out)
                shots.append(f"{out}（{w}×{h}）")
            result.update(shots=shots, alive=alive)
        except Exception as e:
            result["error"] = e
        finally:
            # 關掉這次啟動的程式：原本的程序樹，加上隱藏桌面上所有視窗的擁有者（轉接程式另外啟動的那個）
            import ctypes.wintypes as W
            owners = {pid}
            for hw in desktop_windows(hdesk, None, tag):
                o = W.DWORD()
                ctypes.windll.user32.GetWindowThreadProcessId(ctypes.c_void_p(hw), ctypes.byref(o))
                owners.add(o.value)
            for o in owners:
                subprocess.run(["taskkill", "/T", "/F", "/PID", str(o)], capture_output=True, creationflags=0x08000000)
    t = threading.Thread(target=work_on_hidden_desktop)
    t.start(); t.join(secs + 30)
    if "error" in result:
        raise result["error"]
    shots, alive = result.get("shots", []), result.get("alive", False)
    if not shots:
        return (f"沒有截到視窗：程式在 {secs:g} 秒內{'已經結束' if not alive else '沒有開出視窗'}。"
                "如果它執行完就關閉，改用 run_command 執行並看輸出；或把 seconds 調短。")
    return ("截圖：\n" + "\n".join(shots) + f"\n程式執行了 {secs:g} 秒，已經關閉。"
            "\n（提醒：用 view_file 打開截圖仔細看。回答時具體寫出你檢查了哪些地方（例如：中文是否正常、版面、數值）、"
            "各自是否正確；有問題就修正、重新編譯後再截一次。）")


def t_venv_create(a):
    work = need_work()
    venv = work / ".venv"
    if (venv / "Scripts" / "python.exe").exists():
        return f"已經有虛擬環境：{venv}。執行程式用 {venv}\\Scripts\\python.exe。"
    r = subprocess.run([sys.executable, "-m", "venv", str(venv)], capture_output=True, text=True, timeout=300)
    if r.returncode:
        raise ToolError("建立虛擬環境失敗：" + (r.stderr or r.stdout)[-800:])
    return f"已建立虛擬環境：{venv}。之後執行程式用 {venv}\\Scripts\\python.exe，安裝套件用 venv_install。"


PKG = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._\-]*(\[[A-Za-z0-9_,\-]+\])?([<>=!~]=?[A-Za-z0-9.*+\-]+(,[<>=!~]=?[A-Za-z0-9.*+\-]+)*)?$")


def t_venv_install(a):
    work = need_work()
    py = work / ".venv" / "Scripts" / "python.exe"
    if not py.exists():
        raise ToolError("還沒有 .venv，先用 venv_create 建立。")
    pkgs = [str(p).strip() for p in (a.get("packages") or []) if str(p).strip()]
    bad = [p for p in pkgs if not PKG.match(p)]
    if not pkgs or bad:
        raise ToolError(f"套件名稱不正確：{bad or '（沒有指定）'}。只能用套件的官方名稱（可帶版本），不能用網址或檔案路徑。")
    r = subprocess.run([str(py), "-m", "pip", "install", "--disable-pip-version-check", *pkgs],
                       capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=900)
    tail = (r.stdout + r.stderr).strip()[-1500:]
    if r.returncode:
        raise ToolError("安裝失敗：\n" + tail)
    return "安裝完成：\n" + tail


def call(name, a):
    if name.startswith("pdf_"):
        return t_pdf(name, a)
    return {"run_capture": t_run_capture, "venv_create": t_venv_create, "venv_install": t_venv_install}[name](a)


# ---------------------------------------------------------------- MCP（JSON-RPC，一行一則）
def send(obj):
    sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def main():
    sys.stdin.reconfigure(encoding="utf-8")
    sys.stdout.reconfigure(encoding="utf-8")
    for line in sys.stdin:
        if not line.strip():
            continue
        try:
            msg = json.loads(line)
        except ValueError:
            continue
        mid, method = msg.get("id"), msg.get("method")
        if method == "initialize":
            send({"jsonrpc": "2.0", "id": mid, "result": {
                "protocolVersion": (msg.get("params") or {}).get("protocolVersion", "2025-06-18"),
                "capabilities": {"tools": {}}, "serverInfo": {"name": "lumina", "version": "1.0"}}})
        elif method == "tools/list":
            send({"jsonrpc": "2.0", "id": mid, "result": {"tools": TOOLS}})
        elif method == "tools/call":
            p = msg.get("params") or {}
            try:
                text, err = call(p.get("name", ""), p.get("arguments") or {}), False
            except ToolError as e:
                text, err = str(e), True
            except Exception as e:                            # 工具本身出錯：回報給模型，不要讓伺服器掛掉
                text, err = f"工具執行失敗：{type(e).__name__}: {e}", True
            send({"jsonrpc": "2.0", "id": mid, "result": {"content": [{"type": "text", "text": text}], "isError": err}})
        elif mid is not None:
            send({"jsonrpc": "2.0", "id": mid, "result": {}})


if __name__ == "__main__":
    main()
