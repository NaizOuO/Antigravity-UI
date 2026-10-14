"""檢查點：每輪對話開始前記下資料夾的狀態，模型改壞了可以一鍵復原。

Windows 版 agy 沒有沙盒，擋不住所有破壞；檢查點補的是「出事後能還原」。
- 內容以 SHA-256 存在 data/checkpoints/objects/，相同內容只存一份（課本 PDF 不會每輪多存一次）
- 用「大小 + 修改時間」判斷沒變的檔案，不必每輪重新讀檔計算
- 復原時，這輪新增的檔案移到 data/checkpoints/trash/，不直接刪除
"""

import hashlib
import json
import shutil
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent
STORE = ROOT / "data" / "checkpoints"
OBJECTS, SNAPS, TRASH = STORE / "objects", STORE / "snapshots", STORE / "trash"
INDEX = STORE / "index.json"                 # 路徑 -> [大小, 修改時間, sha]：沒變的檔案不用重算
SKIP_DIRS = {".agents", ".git", "node_modules", "__pycache__", ".venv", "venv", "conversations", "uploads"}  # uploads：附件本來就是副本
SKIP_FILES = {"project.json"}                 # 介面自己管理的檔案（專案設定與記憶），不能被復原蓋掉
MAX_FILE = 200 * 1024 * 1024                 # 超過的檔案不納入（例如影片）
KEEP_SNAPSHOTS = 300


def _skip(rel_parts):
    return any(p in SKIP_DIRS or p.startswith(".檢查_") for p in rel_parts)


def _files(root):
    root = Path(root)
    if not root.is_dir():
        return
    for p in root.rglob("*"):
        try:
            if (p.is_file() and not p.is_symlink() and p.name not in SKIP_FILES
                    and not _skip(p.relative_to(root).parts[:-1])):
                yield p
        except OSError:
            continue


def _load_index():
    try:
        return json.loads(INDEX.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _hash_store(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    sha = h.hexdigest()
    obj = OBJECTS / sha[:2] / sha
    if not obj.exists():
        obj.parent.mkdir(parents=True, exist_ok=True)
        tmp = obj.with_suffix(".tmp")
        shutil.copyfile(p, tmp)
        tmp.replace(obj)
    return sha


def take(roots):
    """記下 roots 裡所有檔案的狀態，回傳檢查點 id。"""
    index, state = _load_index(), {}
    for root in map(Path, roots):
        files = {}
        for p in _files(root):
            try:
                st = p.stat()
                if st.st_size > MAX_FILE:
                    continue
                key = str(p)
                old = index.get(key)
                if old and old[0] == st.st_size and old[1] == st.st_mtime and (OBJECTS / old[2][:2] / old[2]).exists():
                    sha = old[2]
                else:
                    sha = _hash_store(p)
                    index[key] = [st.st_size, st.st_mtime, sha]
                files[str(p.relative_to(root))] = sha
            except OSError:
                continue
        state[str(root)] = files
    STORE.mkdir(parents=True, exist_ok=True)
    INDEX.write_text(json.dumps(index), encoding="utf-8")
    SNAPS.mkdir(parents=True, exist_ok=True)
    sid = f"{time.strftime('%Y%m%d%H%M%S')}_{uuid.uuid4().hex[:6]}"
    (SNAPS / f"{sid}.json").write_text(json.dumps({"t": time.time(), "state": state}, ensure_ascii=False),
                                       encoding="utf-8")
    _prune()
    return sid


def _load(sid):
    if not sid or "/" in sid or "\\" in sid or ".." in sid:
        return None
    try:
        return json.loads((SNAPS / f"{sid}.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _current(root):
    """目前的狀態（只算路徑與 sha；用 index 加速）。"""
    index, out = _load_index(), {}
    for p in _files(root):
        try:
            st = p.stat()
            if st.st_size > MAX_FILE:
                continue
            old = index.get(str(p))
            out[str(p.relative_to(root))] = old[2] if old and old[0] == st.st_size and old[1] == st.st_mtime else None
        except OSError:
            continue
    return out


def changes(sid):
    """這個檢查點之後的變動：[{"path", "kind": added|modified|deleted}]。"""
    snap = _load(sid)
    if not snap:
        return []
    out = []
    for root, before in snap["state"].items():
        now = _current(root)
        for rel, sha in now.items():
            if rel not in before:
                out.append({"path": str(Path(root) / rel), "kind": "added"})
            elif sha != before[rel]:           # sha 為 None 表示檔案動過（大小或時間變了），要實際算
                if sha is None:
                    try:
                        h = hashlib.sha256((Path(root) / rel).read_bytes()).hexdigest()
                    except OSError:
                        h = None
                    if h == before[rel]:
                        continue
                out.append({"path": str(Path(root) / rel), "kind": "modified"})
        for rel in before:
            if rel not in now and Path(rel).name not in SKIP_FILES:
                out.append({"path": str(Path(root) / rel), "kind": "deleted"})
    return out


def restore(sid):
    """把資料夾還原成檢查點的狀態。新增的檔案移到回收區。回傳 (還原數, 移走數)。"""
    snap = _load(sid)
    if not snap:
        raise ValueError("找不到這個檢查點")
    restored = moved = 0
    bin_dir = TRASH / time.strftime("%Y%m%d_%H%M%S")
    for c in changes(sid):
        p = Path(c["path"])
        root = next(Path(r) for r in snap["state"] if p == Path(r) or Path(r) in p.parents)
        rel = str(p.relative_to(root))
        if c["kind"] == "added":
            dest = bin_dir / root.name / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(p), str(dest))
            moved += 1
        else:
            sha = snap["state"][str(root)][rel]
            p.parent.mkdir(parents=True, exist_ok=True)
            if p.exists():                      # 被改過的版本也留一份在回收區
                dest = bin_dir / root.name / rel
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(p, dest)
            shutil.copyfile(OBJECTS / sha[:2] / sha, p)
            restored += 1
    return restored, moved


def _prune():
    """只保留最近的檢查點；沒有任何檢查點用到的內容一併清掉。"""
    snaps = sorted(SNAPS.glob("*.json"))
    if len(snaps) <= KEEP_SNAPSHOTS:
        return
    for s in snaps[:-KEEP_SNAPSHOTS]:
        s.unlink(missing_ok=True)
    used = set()
    for s in SNAPS.glob("*.json"):
        try:
            for files in json.loads(s.read_text(encoding="utf-8"))["state"].values():
                used.update(files.values())
        except (OSError, ValueError):
            continue
    for obj in OBJECTS.glob("*/*"):
        if obj.name not in used:
            obj.unlink(missing_ok=True)
