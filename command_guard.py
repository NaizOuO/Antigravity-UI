"""模型要用工具（尤其是執行指令）前的風險判斷。

agy 在執行任何工具前會呼叫 PreToolUse hook（見 server.py 的 write_hooks），伺服器用這裡的 assess() 決定：
- block：一律擋下（刪除整個資料夾、改系統設定、碰密碼與金鑰、修改 agy 設定或這個介面本身……）
- strict：每次都要使用者確認，不能「永遠允許」（會連網、或是 python / powershell 這類能做任何事的直譯器）
- ask：要使用者確認，可以「永遠允許」同一種唯讀指令（例如 git status、dir）
- pass：不是指令、也沒碰到敏感路徑的工具（讀檔、寫輸出檔……）照 agy 原本的規則

注意：黑名單永遠不可能完整，真正的保護是「使用者看過指令才允許」。這裡的工作是把明顯危險的擋掉，
並把風險標示清楚，讓使用者知道自己在允許什麼。
"""

import re
from pathlib import Path

# ---------- 一律擋下 ----------
BLOCK_PATTERNS = [
    (r"\brm\s+(-\w*[rf]\w*\s+)+", "遞迴／強制刪除（rm -rf）"),
    (r"\b(rmdir|rd)\b[^\n]*\s/s\b", "刪除整個資料夾（rmdir /s）"),
    (r"\b(del|erase)\b[^\n]*\s/[sq]\b", "大量刪除檔案（del /s /q）"),
    (r"\bremove-item\b[^\n]*-r(ecurse)?\b", "遞迴刪除（Remove-Item -Recurse）"),
    (r"\b(ri|rm|rmdir|del)\b[^\n]*-r(ecurse)?\b[^\n]*-fo(rce)?\b", "遞迴刪除（PowerShell 別名）"),
    (r"\bformat(\.com)?\s+[a-z]:", "格式化磁碟"),
    (r"\b(diskpart|cipher\s+/w|vssadmin|bcdedit|wbadmin|fsutil)\b", "磁碟或開機設定工具"),
    (r"\b(reg(\.exe)?\s+(add|delete|import|load|restore|copy)|regedit)\b", "修改登錄檔"),
    (r"\b(set-itemproperty|new-itemproperty|remove-itemproperty)\b[^\n]*\b(hk(lm|cu|cr|u|cc)|registry::)", "修改登錄檔"),
    (r"\bschtasks\b[^\n]*/(create|change|delete)\b|\bregister-scheduledtask\b", "建立或修改排程工作"),
    (r"\bsc(\.exe)?\s+(create|config|delete|stop)\b|\b(new|set|stop)-service\b", "修改系統服務"),
    (r"\b(shutdown|restart-computer|stop-computer|logoff)\b", "關機或登出"),
    (r"\b(takeown|icacls|cacls)\b", "修改檔案權限"),
    (r"\bnet(\.exe)?\s+(user|localgroup|share|use)\b|\bnetsh\b", "修改帳號或網路設定"),
    (r"\b(set|add|remove)-mppreference\b|\bmpcmdrun\b", "修改 Windows Defender"),
    (r"\bset-executionpolicy\b", "修改 PowerShell 執行原則"),
    (r"(^|\s)-(e|ec|en|enc|enco|encodedcommand)\s+[a-z0-9+/=]{16,}", "執行編碼過的 PowerShell 指令"),
    (r"\b(iex|invoke-expression)\b|frombase64string", "動態執行字串（容易藏指令）"),
    (r"\b(certutil|bitsadmin|mshta|rundll32|regsvr32|wmic|msiexec|installutil|cmstp)\b", "常被濫用的系統工具"),
    (r"\b(wscript|cscript)\b", "執行 Windows Script"),
    (r"-verb\s+runas\b|\brunas\b", "以系統管理員身分執行"),
    (r"\bmklink\b|\bnew-item\b[^\n]*-itemtype\s+(symboliclink|junction|hardlink)", "建立連結（可能繞過資料夾限制）"),
    (r"\bstart-process\b", "另外啟動程式（會脫離這次的檢查）"),
    (r"\bstart\s+(/\w+\s+)*\S+\.(exe|bat|cmd|ps1|vbs|js)\b", "另外啟動程式（會脫離這次的檢查）"),
    (r"\bgit\s+(push|config|remote\s+(add|set-url))|\bgit\s+reset\s+--hard|\bgit\s+clean\s+-\w*f", "會改動 git 遠端、設定或丟掉修改"),
]

# 敏感路徑：讀寫都擋（金鑰、密碼、瀏覽器資料、系統資料夾）
SENSITIVE_PATHS = [
    (r"[\\/]\.ssh\b|\bid_(rsa|ed25519|ecdsa)\b|\.pem\b|\.pfx\b|\.p12\b", "SSH 金鑰或憑證"),
    (r"(^|[\\/\s])\.env(\.|\b)|\bcredentials?\b|\bsecrets?\b|\.netrc\b|\.git-credentials\b", "密碼或金鑰檔"),
    (r"\b(login data|cookies|web data|local state)\b|[\\/](google[\\/]chrome|microsoft[\\/]edge|bravesoftware|mozilla)[\\/]",
     "瀏覽器的密碼與 Cookie"),
    (r"[\\/]\.claude\b|[\\/]\.config\b|[\\/]appdata[\\/]roaming[\\/]microsoft[\\/](credentials|protect|crypto)",
     "其他程式的設定或金鑰"),
    (r"\bwindows[\\/]system32\b|\bprogram files\b|\bprogramdata\b", "系統資料夾"),
]

# 只擋「修改」的路徑（模型讀 skill 說明是正常的）
CONFIG_PATHS = [
    (r"[\\/]\.gemini\b|[\\/]\.agents\b|hooks\.json\b|[\\/]setting[\\/]|permissions\.json\b",
     "agy 或這個介面的設定（改了可能關掉這個檢查）"),
    # git／npm／pip 的設定檔可以指定「執行某個指令」（例如 core.fsmonitor），
    # 改了之後連 git status 這種看似無害、可能被永遠允許的指令都會跑出任意程式
    (r"[\\/]\.git[\\/]|\.gitconfig\b|\.gitattributes\b|\.gitmodules\b|\.npmrc\b|\.pypirc\b|pip\.ini\b|\.yarnrc",
     "git、npm、pip 的設定（可以藏入會自動執行的指令）"),
]

# ---------- 每次都要確認（不能永遠允許） ----------
NETWORK = re.compile(
    r"\b(curl|wget|invoke-webrequest|iwr|invoke-restmethod|irm|ssh|scp|sftp|ftp|telnet|nc|ncat|"
    r"pip\d?\s+(install|download)|python\s+-m\s+pip|npm\s+(i|install|publish)|npx|yarn|pnpm|winget|choco|scoop|"
    r"git\s+(clone|pull|fetch|submodule)|net\.webclient|downloadstring|downloadfile|requests\.|urllib|http\.client|"
    r"socket|aiohttp|httpx)\b|https?://", re.I)
INTERPRETERS = {"python", "python3", "py", "pythonw", "node", "deno", "bun", "powershell", "pwsh", "cmd", "bash",
                "sh", "zsh", "wsl", "ruby", "perl", "php", "java", "lua", "r", "rscript", "julia", "dotnet"}
# 會執行「專案檔裡寫的指令」的工具：專案檔是模型寫的，等於任意程式
RUNNERS = {"npm", "npx", "yarn", "pnpm", "make", "nmake", "cmake", "cargo", "go", "gradle", "gradlew", "mvn", "ant",
           "pytest", "tox", "nox", "invoke", "pre-commit", "poetry", "pipenv", "uv", "hatch", "msbuild", "jupyter"}
# 可以「永遠允許」的只有這些唯讀指令（其他一律每次問）
SAFE_ALWAYS = {"dir", "ls", "tree", "type", "cat", "more", "findstr", "find", "where", "echo", "whoami", "hostname",
               "ver", "get-childitem", "gci", "get-content", "gc", "select-string", "sls", "fc", "comp",
               "get-process", "gps", "tasklist", "get-location", "pwd", "test-path", "get-item", "gi", "measure-object"}
# 編譯器：只讀寫原始碼和產生執行檔，可以「永遠允許」；但 -wrapper、-fplugin、-B、@檔案 能讓編譯器執行別的程式，帶這些就不行
COMPILERS = {"g++", "gcc", "c++", "cc", "clang", "clang++", "javac", "tsc", "rustc", "csc"}
COMPILER_RISKY = ("-wrapper", "-fplugin", "-b", "@", "--plugin", "-specs")
SAFE_GIT = {"status", "log", "diff", "show", "branch", "blame", "ls-files", "rev-parse", "describe", "tag"}
SCRIPT_EXT = (".py", ".ps1", ".bat", ".cmd", ".js", ".mjs", ".ts", ".sh", ".rb", ".pl", ".vbs", ".jar")
# 直譯器程式碼裡值得提醒的動作
CODE_RISKS = [
    (r"shutil\.rmtree|os\.(remove|unlink|rmdir|removedirs)|\.unlink\(|\.rmdir\(|send2trash", "會刪除檔案"),
    (r"open\([^)]*['\"][wax]\+?b?['\"]|\.write_(text|bytes)\(|shutil\.(move|copy)|os\.rename|\.rename\(",
     "會寫入、移動或覆蓋檔案"),
    (r"subprocess|os\.system|os\.popen|os\.exec|pty\.|ctypes|winreg|win32api", "會另外執行程式或碰系統"),
    (r"\beval\(|\bexec\(|__import__|compile\(|importlib|getattr\(", "動態執行程式碼"),
    (r"base64|codecs\.decode|bytes\.fromhex|\\x[0-9a-f]{2}|chr\(", "程式碼有編碼過的內容"),
    (r"os\.environ|getpass|keyring", "讀取環境變數或密碼"),
]
# 想藏東西的寫法：%變數%、$(...)、反引號、cmd 的 ^ 跳脫
OBFUSCATION = re.compile(r"%[a-z_]+%|\$\(|`|\^.|\$env:|\$\{|\$[a-z_]+\b", re.I)
SPLIT = re.compile(r"&&|\|\||[;&|\r\n]")

# 這些工具名稱代表會修改檔案：碰到設定路徑或工作資料夾以外就擋
EDIT_TOOLS = re.compile(r"write|replace|edit|create|delete|move|rename|patch", re.I)


def _norm(cmd):
    """去掉引號與 cmd 的 ^ 跳脫，方便比對（例如 r"m" -r^f → rm -rf）。"""
    return re.sub(r"[\"'^]", "", cmd).lower()


def _program(segment):
    tok = segment.strip().split()
    if not tok:
        return "", []
    exe = re.split(r"[\\/]", tok[0].strip("\"'"))[-1].lower()
    exe = re.sub(r"\.(exe|com|cmd|bat)$", "", exe)
    if exe in ("&", "call", "."):                       # PowerShell 的 & "程式"、cmd 的 call
        return _program(" ".join(tok[1:]))
    return exe, tok[1:]


def _inside(path, roots):
    try:
        p = Path(path).resolve()
    except (OSError, ValueError):
        return False
    return any(p == r or r in p.parents for r in roots)


def _path_args(args):
    out = []
    for k, v in (args or {}).items():
        # 只看「像路徑」的值：TargetContent、CodeContent 這類欄位裝的是檔案內容，不是路徑
        if (isinstance(v, str) and "\n" not in v and len(v) < 1000 and not re.search(r"content|text|code|instruction", k, re.I)
                and (re.search(r"path|file|dir|target|cwd", k, re.I) or re.match(r"^[a-z]:[\\/]", v, re.I))):
            out.append(v)
    return out


def _script_text(prog, rest, cwd, roots, first=""):
    """python x.py、powershell -File x.ps1、直接執行 x.bat：讀出腳本內容給使用者看（只讀允許的資料夾裡的檔案）。"""
    for t in rest if prog in INTERPRETERS else [first]:
        t = t.strip("\"'")
        if t.lower().endswith(SCRIPT_EXT):
            p = Path(t) if Path(t).is_absolute() else Path(cwd or ".") / t
            if _inside(p, roots) and p.is_file():
                try:
                    return p.read_text(encoding="utf-8", errors="replace")[:20000], str(p)
                except OSError:
                    pass
            return None, str(p)
    return None, None


def _inline_code(prog, rest, raw):
    low = [r.lower() for r in rest]
    if prog in ("python", "python3", "py", "pythonw") and "-c" in low:
        return raw.split("-c", 1)[1]
    if prog in ("node", "deno", "bun") and ("-e" in low or "--eval" in low):
        return raw
    if prog in ("powershell", "pwsh") and any(r in ("-c", "-command") for r in low):
        return raw
    if prog == "cmd" and any(r in ("/c", "/k") for r in low):
        return raw
    return None


def assess(tool, args, roots):
    """回傳 {"level": block|strict|ask|pass, "reasons": [...], "warnings": [...], "key": 可永遠允許的鍵,
    "program": 主要程式, "script": 腳本內容, "script_path": ...}"""
    roots = [Path(r).resolve() for r in roots]
    res = {"level": "pass", "reasons": [], "warnings": [], "key": None, "program": "", "script": None,
           "script_path": None}

    def bump(level, why, warn=False):
        order = {"pass": 0, "ask": 1, "strict": 2, "block": 3}
        if order[level] > order[res["level"]]:
            res["level"] = level
        lst = res["warnings"] if warn else res["reasons"]
        if why not in lst:
            lst.append(why)

    tool = (tool or "").lower()
    if tool != "run_command":
        # 非指令工具：碰到敏感路徑就擋；修改類工具不能改設定、也不能改允許的資料夾以外
        editing = bool(EDIT_TOOLS.search(tool))
        for p in _path_args(args):
            for pat, why in SENSITIVE_PATHS + (CONFIG_PATHS if editing else []):
                if re.search(pat, p.lower()):
                    bump("block", f"{why}：{p}")
            if editing and not _inside(p, roots):
                bump("block", f"不能修改工作資料夾以外的檔案：{p}")
        return res

    raw = str((args or {}).get("CommandLine") or "")
    cwd = str((args or {}).get("Cwd") or "")
    norm = _norm(raw)
    if not raw.strip():
        bump("block", "空白的指令")
        return res
    if cwd and not _inside(cwd, roots):
        bump("block", f"只能在工作或專案資料夾裡執行（這次是 {cwd}）")

    for pat, why in BLOCK_PATTERNS + SENSITIVE_PATHS + CONFIG_PATHS:
        if re.search(pat, norm):
            bump("block", why)
    # 指令裡出現的絕對路徑都要在允許的資料夾裡（讀取其他地方的檔案也可能外洩）
    for m in re.finditer(r"[a-z]:[\\/][^\s\"'|&;<>]*", raw, re.I):
        if not _inside(m.group(0), roots):
            bump("strict", f"用到工作資料夾以外的路徑：{m.group(0)}", warn=True)
    if re.search(r"(^|[\\/\s\"'])\.\.([\\/]|$)", raw):
        bump("strict", "用到上一層資料夾（..），可能跑出工作資料夾", warn=True)
    if re.search(r"(^|\s)>{1,2}\s*\S|\bout-file\b|\bset-content\b|\badd-content\b|\bcopy\b|\bmove\b|\bxcopy\b|"
                 r"\brobocopy\b|\bren(ame)?\b", norm):
        bump("strict", "會寫入、搬移或覆蓋檔案", warn=True)
    if OBFUSCATION.search(raw):
        bump("strict", "指令裡有變數展開或跳脫字元，實際執行的內容可能和看到的不同", warn=True)
    if NETWORK.search(norm):
        bump("strict", "會連上網路（下載、上傳或安裝套件）", warn=True)

    segments = [s for s in SPLIT.split(raw) if s.strip()]
    progs = []
    for seg in segments:
        prog, rest = _program(seg)
        progs.append(prog)
        code = _inline_code(prog, rest, seg)
        script, spath = _script_text(prog, rest, cwd, roots, (seg.strip().split() or [""])[0])
        if spath:
            res["script_path"] = spath
            if script is None:
                bump("strict", f"要執行的腳本不在允許的資料夾或讀不到，無法先檢查內容：{spath}", warn=True)
            else:
                res["script"] = script
                code = (code or "") + "\n" + script
        if prog in INTERPRETERS or spath:
            bump("strict", f"用 {prog} 執行程式碼：等於讓模型在你的電腦上做任何事，請看過內容再允許", warn=True)
        if prog == "git" and any(r.lower().startswith(("-c", "--config", "--exec-path")) for r in rest):
            bump("strict", "git -c 可以臨時指定會執行指令的設定（例如 core.pager）", warn=True)
        if prog in RUNNERS:
            bump("strict", f"{prog} 會執行專案檔裡寫的指令（專案檔可能是模型寫的），等於執行任意程式", warn=True)
        # cmd 會先找「目前資料夾」裡的同名程式：模型可以寫一個 git.bat 冒充 git。
        # 明確寫出路徑（.\程式.exe）是要執行自己的程式，不是冒充：改成每次詢問
        first = (seg.strip().split() or [""])[0].strip("\"'&")
        if prog and cwd and re.search(r"[\\/]", first):
            bump("strict", f"執行工作資料夾裡的程式 {first}（可能是模型剛編譯出來的），請確認它要做什麼", warn=True)
        elif prog and cwd:
            for ext in ("", ".bat", ".cmd", ".exe", ".com", ".ps1", ".vbs", ".js"):
                if (Path(cwd) / f"{prog}{ext}").is_file():
                    bump("block", f"工作資料夾裡有同名的 {prog}{ext}，可能是冒充的程式")
                    break
        if code:
            for pat, why in BLOCK_PATTERNS + SENSITIVE_PATHS + CONFIG_PATHS:
                if re.search(pat, _norm(code)):
                    bump("block", f"程式碼裡：{why}")
            for pat, why in CODE_RISKS:
                if re.search(pat, code, re.I):
                    bump("strict", f"程式碼{why}", warn=True)
            if NETWORK.search(code.lower()):
                bump("strict", "程式碼會連上網路", warn=True)
    if len(segments) > 1:
        bump("strict", f"一次串了 {len(segments)} 個指令", warn=True)

    res["program"] = progs[0] if progs else ""
    res["segments"] = len(segments)
    if res["level"] == "pass":
        res["level"] = "ask"
    # 「永遠允許」只開放給唯讀指令；git 只開放看狀態的子指令，而且不能帶能改設定的參數
    if res["level"] == "ask" and len(segments) == 1:
        prog, rest = _program(segments[0])
        if prog in SAFE_ALWAYS:
            res["key"] = prog
        elif prog in COMPILERS and not any(r.lower().startswith(COMPILER_RISKY) for r in rest):
            res["key"] = prog
        elif prog == "git" and rest and rest[0].lower() in SAFE_GIT and not any(
                r.lower().startswith(("-c", "--config", "--exec", "--git-dir", "--work-tree", "--ext-diff",
                                      "--output", "-o")) or "=" in r for r in rest):
            res["key"] = f"git {rest[0].lower()}"
    return res
