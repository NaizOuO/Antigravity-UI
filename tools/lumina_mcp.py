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
         "actions": {"type": "array", "items": {"type": "string"}, "description":
             "實際操作程式（遊戲、視窗程式）的腳本，一行一個動作，照順序做：「wait 秒數」等待、「shot 名稱」截圖、"
             "「hold 按鍵 秒數」按住（例如 hold right 1.5、hold up+left 0.5）、「press 按鍵」按一下。"
             "按鍵：left right up down space enter esc tab shift ctrl a~z 0~9。"
             "用來確認操作真的有反應：例如 shot 開始 → hold right 1 → shot 往右後，比較兩張截圖角色有沒有移動。"
             "有 actions 時 seconds 不用給（腳本做完就關閉）。"},
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
    {"name": "acceptance_set", "description":
        "（只有 Lumina Code 能用）設定驗收指令。修 bug、加功能、或使用者描述了好幾個要求時，在改程式之前先寫一支驗收測試"
        "（例如 test_acceptance.py）：使用者說的每個症狀或要求各寫一項檢查，除了「應該做到的」也要測「不應該發生的」"
        "（例如門沒開時走不到出口、不會穿牆），用很多組隨機輸入測，全部通過時結束碼 0、有任何一項失敗結束碼非 0 並印出哪一項失敗。"
        "寫好後用這個工具登記執行它的指令。之後每次你回答完，介面會自動再跑一次，沒通過會把結果交還給你修。"
        "驗收測試登記後不要為了通過而放寬它；真的要改測試，改完要重新登記（使用者會再確認一次）。",
     "inputSchema": {"type": "object", "properties": {
         "command": {"type": "string", "description": "在工作資料夾執行的指令，例如 .venv\\Scripts\\python test_acceptance.py"},
         "covers": {"type": "array", "items": {"type": "string"}, "description": "這支測試檢查了哪些要求（一項一句）"}},
         "required": ["command", "covers"]}},
    {"name": "design_review", "description":
        "（只有 Lumina Code 能用）拿到這類程式該有的檢查清單。新做一個程式或加大功能時用兩次："
        "phase=plan：開工前，用清單把使用者的話展開成完整規格（使用者沒說、但這類成品應該要有的東西），寫進 todo_write；"
        "phase=review：說完成之前，對照清單審查成品缺了什麼、哪裡設計不好、什麼操作會出事。",
     "inputSchema": {"type": "object", "properties": {
         "kind": {"type": "string", "enum": ["game", "tool", "web", "cli", "other"],
                  "description": "game 遊戲；tool 處理檔案或資料的桌面工具；web 網站或網頁應用；cli 命令列程式或作業題；other 其他"},
         "phase": {"type": "string", "enum": ["plan", "review"]},
         "reference": {"type": "string", "description": "參考的原作或同類產品（例如「Pac-Man」「Notion」），沒有就留空"}},
         "required": ["kind", "phase"]}},
    {"name": "todo_write", "description":
        "（只有 Lumina Code 能用）任務清單：要做的事有三步以上，或使用者一次提了好幾個問題時，一開始就用它把每一件事列成一項"
        "（使用者描述的每個症狀各列一項，不要合併）。之後每次狀態改變就整份重新送出：開始做的那項設成 in_progress（同時只能一項），"
        "確認真的好了才設 completed。completed 的意思是「已經用執行結果驗證過」，不是「改完程式」：每項完成時在 evidence 寫你怎麼確認的"
        "（跑了什麼、看到什麼數字或畫面）。還有沒完成的項目就不要跟使用者說做完了。",
     "inputSchema": {"type": "object", "properties": {
         "todos": {"type": "array", "items": {"type": "object", "properties": {
             "content": {"type": "string", "description": "這一項要做什麼（具體、可以驗證）"},
             "status": {"type": "string", "enum": ["pending", "in_progress", "completed"]},
             "evidence": {"type": "string", "description": "completed 時必填：怎麼驗證的、結果是什麼"}},
             "required": ["content", "status"]}}},
         "required": ["todos"]}},
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


VK = {"left": 0x25, "up": 0x26, "right": 0x27, "down": 0x28, "space": 0x20, "enter": 0x0D, "esc": 0x1B,
      "tab": 0x09, "shift": 0x10, "ctrl": 0x11, "alt": 0x12}
EXTENDED = {0x25, 0x26, 0x27, 0x28}


def vk_of(name):
    name = name.strip().lower()
    if name in VK:
        return VK[name]
    if len(name) == 1 and name.isalnum():
        return ord(name.upper())
    raise ToolError(f"不認得的按鍵：{name}（可以用 left right up down space enter esc tab shift ctrl a~z 0~9）")


def send_keys(hwnd, vks, down):
    """用視窗訊息按下或放開按鍵（送到隱藏桌面上的視窗，不會動到使用者的鍵盤）。"""
    u = ctypes.windll.user32
    for vk in vks:
        sc = u.MapVirtualKeyW(vk, 0)
        lp = 1 | (sc << 16) | ((1 << 24) if vk in EXTENDED else 0)
        if not down:
            lp |= (1 << 30) | (1 << 31)
        u.PostMessageW(ctypes.c_void_p(hwnd), 0x0100 if down else 0x0101, vk, ctypes.c_void_p(lp))


def parse_actions(lines):
    out, total = [], 0.0
    for raw in lines or []:
        parts = str(raw).split()
        if not parts:
            continue
        cmd = parts[0].lower()
        try:
            if cmd == "wait":
                t = min(float(parts[1]), 10.0)
                out.append(("wait", t))
                total += t
            elif cmd == "shot":
                out.append(("shot", " ".join(parts[1:]) or f"第{len(out) + 1}步"))
            elif cmd == "hold":
                t = min(float(parts[2]), 10.0)
                out.append(("hold", [vk_of(k) for k in parts[1].split("+")], t))
                total += t
            elif cmd == "press":
                out.append(("hold", [vk_of(k) for k in parts[1].split("+")], 0.08))
                total += 0.08
            else:
                raise ToolError(f"不認得的動作：{raw}")
        except (IndexError, ValueError):
            raise ToolError(f"動作格式不對：{raw}（例如 wait 1、shot 開始、hold right 1.5、press space）") from None
    if total > 40:
        raise ToolError("操作腳本總長不能超過 40 秒")
    return out


def game_window(wins, tag):
    """有好幾個視窗時（主控台 + 遊戲視窗），挑遊戲視窗：標題不是 tag 的視窗裡面最大的。"""
    import ctypes.wintypes as W
    u = ctypes.windll.user32

    def title(h):
        b = ctypes.create_unicode_buffer(512)
        u.GetWindowTextW(h, b, 512)
        return b.value

    def area(h):
        r = W.RECT()
        u.GetWindowRect(h, ctypes.byref(r))
        return (r.right - r.left) * (r.bottom - r.top)
    others = [h for h in wins if not title(h).startswith(tag)]
    return max(others or wins, key=area) if wins else None


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
    actions = parse_actions(a.get("actions"))
    tag = "LUMINA-" + uuid.uuid4().hex[:6]
    ctypes.windll.user32.SetProcessDPIAware()
    rel = str(prog.relative_to(work))                   # 在工作資料夾裡執行，用相對路徑（避免引號和外層引號黏在一起被 cmd 解析錯）
    if prog.suffix.lower() == ".py":
        venv_py = work / ".venv" / "Scripts" / "python.exe"
        cmdline = subprocess.list2cmdline([str(venv_py if venv_py.exists() else Path(sys.executable)), rel] + args)
    else:
        cmdline = subprocess.list2cmdline([".\\" + rel] + args)
    # 用 conhost 開一個傳統的主控台視窗（不交給 Windows Terminal），標題設成 tag 方便找到這個視窗
    # 隱藏桌面看不到畫面，但聲音照樣會從使用者的喇叭出來：pygame / SDL 的程式一律不出聲
    inner = (f'title {tag} & set SDL_AUDIODRIVER=dummy' + (" & chcp 65001>nul" if a.get("utf8") else "")
             + f" & {cmdline}")
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
            SHOTS.mkdir(parents=True, exist_ok=True)
            shots = []
            if actions:
                for _ in range(60):                     # 等遊戲視窗出來（主控台以外的視窗）
                    time.sleep(0.2)
                    if len(desktop_windows(hdesk, None, tag)) > 1:
                        break
                time.sleep(1.0)
                for act in actions:
                    hw = game_window(desktop_windows(hdesk, None, tag), tag)
                    if act[0] == "wait":
                        time.sleep(act[1])
                    elif act[0] == "hold" and hw:
                        end = time.time() + act[2]
                        send_keys(hw, act[1], True)
                        while time.time() < end:        # 按住期間持續送（像鍵盤按住時的自動重複）
                            time.sleep(0.05)
                            send_keys(hw, act[1], True)
                        send_keys(hw, act[1], False)
                    elif act[0] == "shot" and hw:
                        out = SHOTS / f"play_{prog.stem}_{int(time.time() * 1000)}.png"
                        w, h = grab(hw, out)
                        shots.append(f"[{act[1]}] {out}（{w}×{h}）")
                    if not _alive(pid) and len(desktop_windows(hdesk, None, tag)) <= 1:
                        result["died"] = act[0] + (f" {act[1]}" if act[0] == "shot" else "")
                        break
            else:
                time.sleep(secs)
            alive = _alive(pid) or bool(desktop_windows(hdesk, None, tag))
            wins = [] if actions else desktop_windows(hdesk, None, tag)
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
    t.start(); t.join(secs + 30 + (sum(x[-1] for x in actions if x[0] != "shot") + 20 if actions else 0))
    if "error" in result:
        raise result["error"]
    shots, alive = result.get("shots", []), result.get("alive", False)
    if actions:
        died = result.get("died")
        return ("操作截圖（照腳本順序）：\n" + ("\n".join(shots) or "（沒有截到遊戲視窗）") +
                (f"\n⚠ 程式在「{died}」之後就結束了（可能當掉），用 run_command 執行看錯誤訊息。" if died else "\n程式已經關閉。") +
                "\n（提醒：逐張用 view_file 打開，比較前後差異：按了方向鍵角色有沒有移動、敵人有沒有在動、畫面有沒有出錯。"
                "沒有反應就是 bug，不能說完成；回答時寫出每張圖看到了什麼。）")
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


TODOS = []          # 這次工作階段的任務清單（工具伺服器跟著工作階段啟動，所以放記憶體就好）
MARK = {"pending": "☐", "in_progress": "▶", "completed": "☑"}


def todo_text():
    return "\n".join(f"{MARK.get(t['status'], '☐')} {t['content']}" + (f"（驗證：{t['evidence']}）" if t.get("evidence") else "")
                     for t in TODOS)


def t_todo_write(a):
    need_work()
    todos = a.get("todos")
    if not isinstance(todos, list) or not todos:
        raise ToolError("todos 要是一個不是空的清單。")
    clean = []
    for t in todos:
        if not isinstance(t, dict) or not str(t.get("content") or "").strip():
            raise ToolError("每一項都要有 content。")
        st = t.get("status") if t.get("status") in MARK else "pending"
        ev = str(t.get("evidence") or "").strip()
        if st == "completed" and not ev:
            raise ToolError(f"「{t['content']}」設成 completed 但沒有寫 evidence。先實際執行確認，再把怎麼確認的寫進 evidence。")
        clean.append({"content": str(t["content"]).strip(), "status": st, "evidence": ev})
    if sum(t["status"] == "in_progress" for t in clean) > 1:
        raise ToolError("同時只能有一項是 in_progress。")
    TODOS[:] = clean
    left = [t for t in clean if t["status"] != "completed"]
    tip = (f"\n\n還有 {len(left)} 項沒完成，繼續做下一項。" if left else
           "\n\n全部完成。回覆使用者前，再對照一次使用者原本的要求：每個症狀都有對應的一項、而且都實際驗證過了嗎？"
           "有沒驗證到的就加回清單。")
    return "任務清單已更新：\n" + todo_text() + tip


CHECKLISTS = {
    "game": [
        "核心玩法：原作（或這類遊戲）最重要的 3～5 個機制都有，而且規則正確",
        "完整流程：標題畫面 → 開始 → 遊玩 → 死亡／失敗（要有動畫或停頓，不能瞬間重來）→ 過關或結束 → 重新開始",
        "回饋：音效（用程式產生也可以）、受傷閃爍、得分跳字、畫面震動等，玩家的每個動作都有感覺得到的反應",
        "敵人：種類不只一種、行為各不相同、有可以被看穿的規律，不是只會朝玩家直線衝",
        "操作手感：按鍵反應即時、轉向與碰撞不卡、視角方向符合直覺（例如 3D 左右不能顛倒）",
        "獎勵與目標：分數、道具或收集品、難度隨關卡上升，讓人想繼續玩",
        "地圖與場景：沒有走不到的區域、沒有卡死的位置、邊界處理正確",
        "畫面：角色有造型，介面顯示必要資訊（分數、生命、關卡），中文不亂碼",
        "【驗收測試一定要用程式長時間模擬，不能只看截圖】跑 30～120 秒的遊戲迴圈（不開視窗），檢查："
        "每個會動的角色真的走了很多格（記錄走過的格子數）、不會卡在同一個地方（例如門口、牆角）；"
        "玩家站著不動時敵人最後會抓到他；玩家亂按方向鍵幾千步不穿牆、不出錯；吃到道具後狀態真的改變並且會恢復；"
        "死亡、過關、遊戲結束這些流程真的會發生。模擬要用遊戲實際的每幀時間（例如 1/60 秒）跑，"
        "另外再用較大的時間（1/30、0.1 秒，模擬卡頓）各跑一次——只用一種時間間隔，常會漏掉只在實際幀率出現的卡住問題。"
        "截圖只用來看外觀，判斷「有沒有在動」一律用模擬的數字",
    ],
    "tool": [
        "資料安全：寫檔先寫到暫存檔再改名，失敗時原檔不受影響；不會覆蓋使用者的原始檔",
        "資源釋放：檔案、執行緒、子程序、大量記憶體用完一定關閉或釋放，連續處理很多檔案後記憶體不會一直漲",
        "中斷與當機：處理到一半取消、關閉視窗、斷電後重開，不會留下壞檔或卡住的暫存檔",
        "極端輸入：空檔案、超大檔案、損壞的檔案、路徑有空格或中文、沒有權限的資料夾",
        "進度與回應：長時間工作要顯示進度、可以取消，介面不能凍住",
        "錯誤訊息：出錯時告訴使用者發生什麼事、怎麼處理，不是只丟出例外",
        "相容性：不同 Windows 設定（縮放比例、語系）、沒有網路時的行為",
    ],
    "web": [
        "主要流程從頭到尾都走得通（新增、修改、刪除、重新整理後資料還在）",
        "輸入檢查：空值、超長、特殊字元、重複送出",
        "安全：不把密碼或金鑰寫在前端、防止注入、權限檢查在伺服器端",
        "錯誤與載入狀態：網路慢或失敗時有提示，不會白畫面",
        "手機寬度可以用、深色模式可讀",
    ],
    "cli": [
        "題目或需求的每一條規格都有對應，輸入輸出格式完全照要求",
        "邊界輸入：空輸入、最大值、負數、重複資料",
        "錯誤輸入時不當機，給清楚的訊息",
        "效率：資料量大時時間和記憶體在合理範圍",
    ],
    "other": [
        "使用者明說的每個要求都有做到",
        "同類成品通常會有、但使用者沒提到的功能，列出來判斷要不要補",
        "最可能出錯或讓使用者困惑的地方",
        "資料與資源：會不會損毀檔案、佔住記憶體或檔案不放",
    ],
}


def t_design_review(a):
    need_work()
    kind = a.get("kind") if a.get("kind") in CHECKLISTS else "other"
    ref = str(a.get("reference") or "").strip()
    items = "\n".join(f"- {x}" for x in CHECKLISTS[kind])
    if a.get("phase") == "review":
        return ("設計審查清單" + (f"（參考：{ref}）" if ref else "") + "：\n" + items +
                "\n\n逐項對照成品（實際操作或執行過再判斷，不要憑印象）。"
                "\n- 清單上的項目就是這個成品本來該有的，**屬於這次的工作**：缺了就補上並驗證，更新 todo_write。"
                "不能因為使用者沒明說就改成建議（例如做遊戲卻沒有音效、死亡沒有動畫，都是沒做完）。"
                "\n- 只有這兩類寫成「建議（尚未執行）」：①明顯超出這個成品的新方向（例如多人連線、關卡編輯器、改用別的技術）"
                "②對外或難以復原的事（建立 repo、上傳、發布、刪除檔案、裝大型軟體）。")
    return ("規格展開清單" + (f"（參考：{ref}）" if ref else "") + "：\n" + items +
            "\n\n用這份清單把使用者的話展開成規格：" + (f"先想 {ref} 實際玩起來／用起來有哪些要素，" if ref else "") +
            "列出使用者明說的要求，再補上這類成品應該要有的東西（標示「補充」）——**補充的項目也是這次要做的**，全部寫進 todo_write。"
            "只有明顯超出這個成品的新方向才留到最後當建議。")
    return ("規格展開清單" + (f"（參考：{ref}）" if ref else "") + "：\n" + items +
            "\n\n用這份清單把使用者的話展開成規格：" + (f"先想 {ref} 實際玩起來／用起來有哪些要素，" if ref else "") +
            "列出使用者明說的要求，再補上這類成品應該要有的東西（標示「補充」）。"
            "把規格逐項寫進 todo_write，範圍明顯太大的部分留到最後當建議，不要擅自擴大工作。")


ACCEPT_FILE = ".agents/lumina_acceptance.json"     # 介面伺服器也讀這個檔（server.py 的 run_acceptance）


def command_files(work, command):
    """指令裡提到、而且在工作資料夾裡的檔案和它們的雜湊：測試檔被改過就不會自動執行。"""
    import hashlib
    out = {}
    for tok in re.findall(r'"([^"]+)"|(\S+)', command):
        t = (tok[0] or tok[1]).strip()
        p = (work / t) if not Path(t).is_absolute() else Path(t)
        try:
            if p.is_file() and inside(p, [work]) and p.suffix.lower() not in (".exe",):
                out[str(p.resolve().relative_to(work))] = hashlib.sha1(p.read_bytes()).hexdigest()
        except (OSError, ValueError):
            pass
    return out


def t_acceptance_set(a):
    work = need_work()
    cmd = str(a.get("command") or "").strip()
    covers = [str(c).strip() for c in (a.get("covers") or []) if str(c).strip()]
    if not cmd or not covers:
        raise ToolError("要有 command 和 covers。")
    files = command_files(work, cmd)
    if not files:
        raise ToolError("指令裡找不到工作資料夾裡的測試檔。先把驗收測試寫好存檔，再登記執行它的指令。")
    f = work / ACCEPT_FILE
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps({"command": cmd, "covers": covers, "files": files, "t": time.time()},
                            ensure_ascii=False, indent=1), encoding="utf-8")
    return ("已登記驗收指令：" + cmd + "\n檢查項目：" + "；".join(covers) +
            "\n現在先自己跑一次（應該會失敗，代表測試真的抓得到問題），再開始改程式。之後每次回答結束介面都會重跑。")


def with_todo_reminder(name, text):
    """其他工具回傳時順便提醒還沒完成的項目（提醒放在動作當下，不靠開頭的提示）。"""
    left = [t for t in TODOS if t["status"] != "completed"]
    if name == "todo_write" or not left:
        return text
    return text + "\n\n（任務清單還有 " + str(len(left)) + " 項沒完成：" + "；".join(t["content"] for t in left[:5]) + "）"


def call(name, a):
    if name.startswith("pdf_"):
        return t_pdf(name, a)
    return {"run_capture": t_run_capture, "venv_create": t_venv_create, "venv_install": t_venv_install,
            "todo_write": t_todo_write, "acceptance_set": t_acceptance_set, "design_review": t_design_review}[name](a)


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
                text, err = with_todo_reminder(p.get("name", ""), call(p.get("name", ""), p.get("arguments") or {})), False
            except ToolError as e:
                text, err = str(e), True
            except Exception as e:                            # 工具本身出錯：回報給模型，不要讓伺服器掛掉
                text, err = f"工具執行失敗：{type(e).__name__}: {e}", True
            send({"jsonrpc": "2.0", "id": mid, "result": {"content": [{"type": "text", "text": text}], "isError": err}})
        elif mid is not None:
            send({"jsonrpc": "2.0", "id": mid, "result": {}})


if __name__ == "__main__":
    main()
