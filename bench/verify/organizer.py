"""「整理資料夾」題的評分程式（模型看不到）。

介面（寫在題目裡）：
    python organize.py <資料夾> --dry-run    只顯示會怎麼整理，不動任何檔案
    python organize.py <資料夾>              整理（包含子資料夾裡的檔案）；內容重複的檔案放到「重複檔案」資料夾
    python organize.py <資料夾> --undo       復原上一次的整理
用法：python organizer.py <題目資料夾>
"""
import hashlib
import json
import os
import random
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

GAME = Path(sys.argv[1]).resolve()
PROG = GAME / "organize.py"
PY = GAME / ".venv" / "Scripts" / "python.exe"
PY = PY if PY.exists() else Path(sys.executable)
checks, notes = {}, {}
GROUPS = [{".jpg", ".jpeg", ".png", ".gif"}, {".pdf", ".docx", ".txt", ".xlsx"}, {".mp4"}, {".mp3", ".wav"}, {".zip", ".7z"}]
SAME_OK = {frozenset({2, 3})}          # 影片和音訊放一起或分開都可以


def report():
    print("BENCH_RESULT " + json.dumps({"passed": sum(checks.values()), "total": len(checks), "checks": checks,
                                        "notes": notes}, ensure_ascii=False))


def h(p):
    return hashlib.sha1(p.read_bytes()).hexdigest()


def tree(root):
    """{相對路徑: 內容雜湊}（不含程式自己的紀錄檔：以 . 開頭的檔案或資料夾）"""
    out = {}
    for p in root.rglob("*"):
        rel = p.relative_to(root)
        if p.is_file() and not any(part.startswith(".") for part in rel.parts):
            out[rel.as_posix()] = h(p)
    return out


def make_fixture(root):
    rnd = random.Random(7)
    blob = lambda n: bytes(rnd.getrandbits(8) for _ in range(n))
    files = {
        "IMG_0001.jpg": blob(3000), "截圖 2026-10-01.png": blob(2500), "貓.gif": blob(900),
        "期末報告.pdf": blob(4000), "會議紀錄.docx": blob(2200), "筆記 (final).txt": "量子力學筆記\n".encode() * 50,
        "預算.xlsx": blob(1800), "歌.mp3": blob(5000), "影片 01.mp4": blob(6000), "錄音.wav": blob(1500),
        "資料.zip": blob(2600), "備份.7z": blob(2400), "script.py": b"print('hi')\n", "奇怪的檔案.xyz": blob(300),
        "沒有副檔名": blob(200),
        "子資料夾/報告.pdf": blob(3100), "報告.pdf": blob(3200),           # 同名但內容不同：不能互相覆蓋
        "子資料夾/更深/旅遊照.jpg": blob(2800), "子資料夾/唯讀文件.txt": "不要弄丟我\n".encode() * 20,
        "大檔案.mp4": b"\0" * (20 * 1024 * 1024),
    }
    for rel, data in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
    # 內容重複：不同檔名、不同資料夾
    shutil.copy(root / "IMG_0001.jpg", root / "IMG_0001 (1).jpg")
    shutil.copy(root / "IMG_0001.jpg", root / "子資料夾" / "相片副本.jpg")
    shutil.copy(root / "期末報告.pdf", root / "期末報告 - 複製.pdf")
    os.chmod(root / "子資料夾" / "唯讀文件.txt", stat.S_IREAD)


def run(root, *args, timeout=120):
    return subprocess.run([str(PY), str(PROG), str(root), *args], cwd=GAME, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=timeout,
                          env={**os.environ, "PYTHONIOENCODING": "utf-8"})


def group_of(name):
    ext = Path(name).suffix.lower()
    return next((i for i, g in enumerate(GROUPS) if ext in g), None)


def check(name, fn):
    try:
        ok, note = fn()
    except Exception as e:
        ok, note = False, f"{type(e).__name__}: {e}"[:300]
    checks[name] = bool(ok)
    if note:
        notes[name] = str(note)[:400]


if not PROG.exists():
    checks["有 organize.py"] = False
    report()
    sys.exit()
checks["有 organize.py"] = True

base = Path(tempfile.mkdtemp(prefix="org_"))
root = base / "下載 資料夾"                    # 路徑有空格和中文
root.mkdir()
make_fixture(root)
orig = tree(root)
orig_multiset = sorted(orig.values())

# 1. 預覽不能改任何東西
r = run(root, "--dry-run")
check("預覽模式不動任何檔案", lambda: (r.returncode == 0 and tree(root) == orig and len(r.stdout.strip()) > 0,
                                     f"結束碼 {r.returncode}；{(r.stderr or '')[-200:]}" if r.returncode else ""))

# 2. 整理（另外開著一個檔案，模擬被其他程式占用）
locked = open(root / "會議紀錄.docx", "rb")
r = run(root)
locked.close()
after = tree(root)
check("整理時不會當掉（有檔案被占用也一樣）", lambda: (r.returncode == 0, f"結束碼 {r.returncode}；{(r.stderr or r.stdout)[-300:]}"))
check("沒有弄丟或損毀任何檔案", lambda: (sorted(after.values()) == orig_multiset,
                                    f"整理前 {len(orig_multiset)} 個檔案，整理後 {len(after)} 個；"
                                    f"少了 {len(set(orig_multiset) - set(after.values()))} 種內容"))


def c_dupes():
    dup_dir = [k for k in after if "重複檔案" in Path(k).parts]
    outside = [v for k, v in after.items() if "重複檔案" not in Path(k).parts]
    multi = {v for v in outside if outside.count(v) > 1}
    ok = len(dup_dir) == 3 and not multi
    return ok, f"「重複檔案」裡有 {len(dup_dir)} 個（應該 3 個）；重複檔案以外還有 {len(multi)} 種內容出現多次"


check("內容重複的檔案放到「重複檔案」（看內容不是看檔名）", c_dupes)


def c_groups():
    folders = {}
    for k in after:
        parts = Path(k).parts
        if "重複檔案" in parts or k == "會議紀錄.docx":     # 被占用的檔案留在原地是對的
            continue
        g = group_of(k)
        if g is None:
            continue
        folders.setdefault(g, set()).add(parts[0] if len(parts) > 1 else "")
    bad = [i for i, f in folders.items() if len(f) != 1 or "" in f]
    owner = {}
    clash = False
    for i, f in folders.items():
        if len(f) == 1:
            d = next(iter(f))
            j = owner.setdefault(d, i)
            if j != i and frozenset({i, j}) not in SAME_OK:
                clash = True
    return not bad and not clash, f"各類型所在的資料夾：{ {i: sorted(f) for i, f in folders.items()} }"


check("同類型的檔案在同一個資料夾，不同類型分開", c_groups)
check("子資料夾裡的檔案也有整理到", lambda: (not any(k.startswith("子資料夾/") and group_of(k) is not None for k in after),
                                         [k for k in after if k.startswith("子資料夾/")][:5]))
check("檔名保留（中文、空格、括號）", lambda: (all(any(Path(k).name == Path(o).name or Path(k).stem.startswith(Path(o).stem)
                                                     for k in after) for o in orig), ""))

# 3. 再跑一次不會出事
r2 = run(root)
again = tree(root)
check("整理完再跑一次不會亂掉", lambda: (r2.returncode == 0 and sorted(again.values()) == orig_multiset,
                                    f"結束碼 {r2.returncode}"))

# 4. 復原：回到和原本一模一樣（第二次整理沒動東西時，復原一次或兩次都要回到原狀）
r3 = run(root, "--undo")
undone = tree(root)
if undone != orig:
    run(root, "--undo")
    undone = tree(root)
diff = sorted(set(orig.items()) ^ set(undone.items()))[:4]
check("復原後和原本一模一樣", lambda: (undone == orig, f"差異：{diff}"))
check("唯讀檔案也照顧到（沒有弄丟、屬性不壞）",
      lambda: ((root / "子資料夾" / "唯讀文件.txt").exists() and not os.access(root / "子資料夾" / "唯讀文件.txt", os.W_OK), ""))

for p in base.rglob("*"):
    try:
        os.chmod(p, stat.S_IWRITE)
    except OSError:
        pass
shutil.rmtree(base, ignore_errors=True)
report()
