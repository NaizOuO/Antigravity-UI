"""選用元件：使用者同意後才下載（設計參考 Naiz Studio 的 deps.py）。

每個元件記錄版本、下載網址、SHA-256、大小與要從壓縮檔取出的檔案。下載會：
- 先檢查磁碟空間
- 斷線或逾時自動重試，從斷掉的地方接著下載（HTTP Range）
- 下載完核對 SHA-256，再解壓到 components/<id>/；途中失敗不會留下半套
- 可以隨時取消
元件放在主目錄的 components/，和 data/ 一樣不會被更新覆蓋。
"""

import hashlib
import shutil
import threading
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
COMPONENTS_DIR = ROOT / "components"
RETRIES = 5
TIMEOUT = 20
CHUNK = 1 << 16

# Typst 會用到的套件（固定版本：skill 的範例檔就是用這些版本測過的）。
# 安裝時先下載到元件資料夾，之後沒有網路也能編譯。
TYPST_PACKAGES = {"physica": "0.9.8", "cetz": "0.5.2", "fletcher": "0.5.8", "zap": "0.6.0",
                  "touying": "0.8.0", "charged-ieee": "0.1.4", "noteworthy": "0.4.0", "theorion": "0.6.0",
                  "gentle-clues": "1.3.1"}
PARALLEL = 4                       # 大檔分段同時下載的連線數（GitHub 單一連線常常很慢）
PARALLEL_MIN = 8 * 1024 * 1024

COMPONENTS = {
    "typst": {
        "name": "Typst（PDF 排版）",
        "purpose": "把 AI 寫好的講義、報告、簡報排版成 PDF（數學公式、圖表、中文都支援）",
        "version": "0.15.1",
        "url": "https://github.com/typst/typst/releases/download/v0.15.1/typst-x86_64-pc-windows-msvc.zip",
        "sha256": "19ce3551153c2fe7ee9fa2f95208310c8f4d3209fedb699e0333faf8913f6736",
        "size": 22463684,
        "size_text": "下載約 21 MB，安裝後約 55 MB（含常用套件）",
        "license": "Apache-2.0",
        "extract": {"typst-x86_64-pc-windows-msvc/typst.exe": "typst.exe"},
        "check": "typst.exe",
    },
}


class Cancelled(Exception):
    pass


def human(n):
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.0f} {unit}" if unit in ("B", "KB") else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def folder(cid):
    return COMPONENTS_DIR / cid


def installed(cid):
    c = COMPONENTS.get(cid)
    return bool(c) and (folder(cid) / c["check"]).is_file()


def status():
    out = {}
    for cid, c in COMPONENTS.items():
        f = folder(cid)
        used = sum(p.stat().st_size for p in f.rglob("*") if p.is_file()) if f.exists() else 0
        out[cid] = {k: c[k] for k in ("name", "purpose", "version", "size_text", "license")}
        out[cid].update(installed=installed(cid), used=human(used) if used else "")
    return out


def _fetch_range(url, start, end, path, counter, cancel):
    """下載 [start, end] 這一段到 path（斷線就從已經下載的地方接著）。"""
    for attempt in range(1, RETRIES + 1):
        have = path.stat().st_size if path.exists() else 0
        if start + have > end:
            return
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Antigravity-UI", "Range": f"bytes={start + have}-{end}"})
            with urllib.request.urlopen(req, timeout=TIMEOUT) as r, open(path, "ab") as out:
                if r.status != 206:
                    raise RuntimeError("伺服器不支援分段下載")
                while True:
                    if cancel.is_set():
                        raise Cancelled()
                    chunk = r.read(CHUNK)
                    if not chunk:
                        break
                    out.write(chunk)
                    with counter["lock"]:
                        counter["done"] += len(chunk)
            if path.stat().st_size >= end - start + 1:
                return
        except Cancelled:
            raise
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as e:
            if attempt == RETRIES:
                raise RuntimeError(f"下載失敗（重試 {RETRIES} 次）：{e}")
            time.sleep(min(2 * attempt, 8))


def _download_parallel(c, part, progress, cancel):
    """把檔案切成 PARALLEL 段同時下載，再接起來（大檔、慢的連線會快很多）。"""
    total = c["size"]
    step = -(-total // PARALLEL)
    pieces = [(i * step, min(total, (i + 1) * step) - 1, part.with_name(f"{part.name}.{i}")) for i in range(PARALLEL)]
    counter = {"done": sum(p.stat().st_size for *_, p in pieces if p.exists()), "lock": threading.Lock()}
    errors, stop = [], threading.Event()

    class Either:                            # 使用者取消，或其他段失敗（要其他段一起停）
        @staticmethod
        def is_set():
            return cancel.is_set() or stop.is_set()

    def worker(s, e, p):
        try:
            _fetch_range(c["url"], s, e, p, counter, Either)
        except BaseException as ex:
            errors.append(ex)
            stop.set()
    threads = [threading.Thread(target=worker, args=pc, daemon=True) for pc in pieces]
    for t in threads:
        t.start()
    while any(t.is_alive() for t in threads):
        progress(counter["done"], total)
        time.sleep(0.3)
    if cancel.is_set():
        raise Cancelled()
    if errors:
        raise next((e for e in errors if not isinstance(e, Cancelled)), errors[0])
    with open(part, "wb") as out:
        for *_, p in pieces:
            with open(p, "rb") as f:
                shutil.copyfileobj(f, out)
            p.unlink()


def _download(c, dest, progress, cancel):
    """下載到 dest（.part 續傳），核對 SHA-256。大檔先試分段同時下載，不行再用單一連線。"""
    part = dest.with_name(dest.name + ".part")
    total = c["size"]
    free = shutil.disk_usage(COMPONENTS_DIR).free
    if free < total * 3 + 100 * 1024 * 1024:
        raise RuntimeError(f"磁碟空間不足：需要約 {human(total * 3)}，目前只剩 {human(free)}")
    if total >= PARALLEL_MIN and not part.exists():
        try:
            _download_parallel(c, part, progress, cancel)
        except Cancelled:
            raise
        except Exception:
            for p in part.parent.glob(part.name + ".*"):      # 分段失敗：清掉，改用單一連線
                p.unlink(missing_ok=True)
            part.unlink(missing_ok=True)
    for attempt in range(1, RETRIES + 1):
        done = part.stat().st_size if part.exists() else 0
        if done >= total:
            break
        try:
            req = urllib.request.Request(c["url"], headers={"User-Agent": "Antigravity-UI",
                                                            **({"Range": f"bytes={done}-"} if done else {})})
            with urllib.request.urlopen(req, timeout=TIMEOUT) as r, open(part, "ab" if done else "wb") as out:
                if done and r.status != 206:          # 伺服器不支援續傳：從頭來
                    out.seek(0); out.truncate(); done = 0
                while True:
                    if cancel.is_set():
                        raise Cancelled()
                    chunk = r.read(CHUNK)
                    if not chunk:
                        break
                    out.write(chunk)
                    done += len(chunk)
                    progress(done, total)
        except Cancelled:
            raise
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as e:
            if attempt == RETRIES:
                raise RuntimeError(f"下載失敗（重試 {RETRIES} 次）：{e}")
            time.sleep(min(2 * attempt, 8))
    h = hashlib.sha256()
    with open(part, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    if h.hexdigest() != c["sha256"]:
        part.unlink(missing_ok=True)
        raise RuntimeError("下載的檔案不完整（檢查碼不符），請再試一次")
    part.replace(dest)
    return dest


def install(cid, progress=lambda done, total, msg="": None, cancel=None):
    """下載並安裝元件；先裝在暫存資料夾，全部成功才換上。"""
    c = COMPONENTS[cid]
    cancel = cancel or threading.Event()
    COMPONENTS_DIR.mkdir(parents=True, exist_ok=True)
    work = COMPONENTS_DIR / f".{cid}.installing"
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True)
    try:
        archive = _download(c, COMPONENTS_DIR / f".{cid}.zip", lambda d, t: progress(d, t, "下載中"), cancel)
        progress(c["size"], c["size"], "解壓縮中")
        with zipfile.ZipFile(archive) as zf:
            for member, target in c["extract"].items():
                (work / target).parent.mkdir(parents=True, exist_ok=True)
                with zf.open(member) as src, open(work / target, "wb") as out:
                    shutil.copyfileobj(src, out)
        if cid == "typst":
            progress(c["size"], c["size"], "下載常用的 Typst 套件")
            _prefetch_typst_packages(work)
        dest = folder(cid)
        shutil.rmtree(dest, ignore_errors=True)
        work.replace(dest)
        archive.unlink(missing_ok=True)
    except BaseException:
        shutil.rmtree(work, ignore_errors=True)
        raise


def remove(cid):
    shutil.rmtree(folder(cid), ignore_errors=True)


# ---------- Typst ----------
def typst_exe():
    return folder("typst") / "typst.exe"


def typst_cache():
    return folder("typst") / "packages"


def _prefetch_typst_packages(base):
    """用一份會 import 所有常用套件的小文件編譯一次，讓 Typst 把套件下載到元件資料夾裡的快取。"""
    import json
    import subprocess
    latest = dict(TYPST_PACKAGES)
    src = base / "_prefetch.typ"
    src.write_text("\n".join(f'#import "@preview/{n}:{v}"' for n, v in latest.items()) + "\n= ok\n", encoding="utf-8")
    subprocess.run([str(base / "typst.exe"), "compile", "--package-cache-path", str(base / "packages"),
                    str(src), str(base / "_prefetch.pdf")], capture_output=True, timeout=300,
                   creationflags=0x08000000)
    src.unlink(missing_ok=True)
    (base / "_prefetch.pdf").unlink(missing_ok=True)
    (base / "packages.json").write_text(json.dumps(latest, indent=1), encoding="utf-8")


def typst_packages():
    """已下載的套件與版本：{名稱: 版本}（skill 裡用到的 import 版本以這個為準）。"""
    import json
    try:
        return json.loads((folder("typst") / "packages.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def compile_typst(src, out=None):
    """編譯 .typ → PDF。回傳 (ok, pdf 路徑或錯誤訊息)。不連網：套件只用元件資料夾裡的快取。"""
    import subprocess
    src = Path(src)
    out = Path(out) if out else src.with_suffix(".pdf")
    r = subprocess.run([str(typst_exe()), "compile", "--package-cache-path", str(typst_cache()),
                        "--root", str(src.parent), str(src), str(out)],
                       capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120,
                       creationflags=0x08000000)
    if r.returncode == 0 and out.exists():
        return True, out
    return False, (r.stderr or r.stdout or "未知的錯誤").strip()[-3000:]
