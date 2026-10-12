"""檢查更新與自動更新：問 GitHub 最新的 Release，有新版本就讓使用者決定要不要更新。

只讀取公開的 Release 資訊（不送出任何資料）。更新流程：
1. 下載 Release 裡的 zip，核對 GitHub 提供的 sha256
2. 解壓到 update/new，確認裡面真的是這個程式（有 server.py、version.py、manifest.json）
3. 舊的程式檔先備份到 update/backup，再換成新版；換到一半失敗就全部還原
4. 舊版 manifest 有、新版沒有的程式檔才會刪除

個人資料（data/：對話紀錄、專案、上傳的檔案、設定）永遠不會被讀取、覆蓋或刪除：
zip 裡任何 data/ 開頭的路徑都會被忽略。
"""

import hashlib
import json
import shutil
import time
import urllib.request
import zipfile
from pathlib import Path

import version

REPO = "NaizOuO/Antigravity-UI"
LATEST_URL = f"https://api.github.com/repos/{REPO}/releases/latest"
RELEASES_PAGE = f"https://github.com/{REPO}/releases/latest"
ASSET_PREFIX = "Antigravity-UI-"
MANIFEST = "manifest.json"          # 這一版的程式檔清單（release.py 產生）
PROTECTED = ("data", "update", ".git")   # 這些資料夾裡的東西更新時絕對不動
CHECK_TTL = 30 * 60                 # 檢查結果快取 30 分鐘


def _get_json(url, timeout=10):
    req = urllib.request.Request(url, headers={"User-Agent": "Antigravity-UI", "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def _safe(relative):
    """只接受資料夾內的相對路徑，而且不能碰 data/ 等受保護的資料夾。"""
    p = Path(relative)
    if not relative or p.is_absolute() or ":" in relative or ".." in p.parts:
        return False
    return p.parts[0].lower() not in PROTECTED


def file_sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


_cache = {"t": 0, "result": None}


def check(force=False):
    """回傳 {"current", "latest", "available", "notes", "url", "size", "sha256", "page"}；連不上時 error 有原因。"""
    if not force and _cache["result"] and time.time() - _cache["t"] < CHECK_TTL:
        return _cache["result"]
    result = {"current": version.VERSION, "available": False, "page": RELEASES_PAGE}
    try:
        data = _get_json(LATEST_URL)
        tag = str(data.get("tag_name", ""))
        asset = next((a for a in data.get("assets", [])
                      if str(a.get("name", "")).startswith(ASSET_PREFIX) and a["name"].endswith(".zip")), None)
        digest = str((asset or {}).get("digest") or "")
        result.update(
            latest=tag.lstrip("vV"),
            available=bool(asset) and version.parse(tag) > version.parse(version.VERSION),
            notes=str(data.get("body") or "").strip(),
            page=data.get("html_url") or RELEASES_PAGE,
            url=(asset or {}).get("browser_download_url", ""),
            size=int((asset or {}).get("size") or 0),
            sha256=digest.split(":", 1)[1] if digest.startswith("sha256:") else "",
        )
    except Exception as e:
        result["error"] = f"沒辦法連到 GitHub 檢查更新（{e}）"
    _cache.update(t=time.time(), result=result)
    return result


def download(info, target):
    req = urllib.request.Request(info["url"], headers={"User-Agent": "Antigravity-UI"})
    h = hashlib.sha256()
    with urllib.request.urlopen(req, timeout=60) as r, open(target, "wb") as out:
        for chunk in iter(lambda: r.read(1 << 16), b""):
            out.write(chunk)
            h.update(chunk)
    if info.get("sha256") and h.hexdigest() != info["sha256"]:
        raise RuntimeError("下載的檔案不完整（檢查碼不符），請稍後再試")
    return target


def _read_manifest(folder):
    try:
        return json.loads((Path(folder) / MANIFEST).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def apply(archive, root):
    """把 zip 裡的新版換進 root。成功回傳新版本號；失敗會還原舊檔並丟出例外。"""
    root = Path(root)
    work = root / "update"
    staged, backup = work / "new", work / "backup"
    shutil.rmtree(work, ignore_errors=True)
    staged.mkdir(parents=True)

    # 1. 解壓（不安全或受保護的路徑一律略過）
    with zipfile.ZipFile(archive) as zf:
        names = [n for n in zf.namelist() if not n.endswith("/")]
        top = {n.split("/", 1)[0] for n in names}
        prefix = (next(iter(top)) + "/") if len(top) == 1 and all("/" in n for n in names) else ""
        for name in names:
            rel = name[len(prefix):]
            if not _safe(rel):
                continue
            dest = staged / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(name) as src, open(dest, "wb") as out:
                shutil.copyfileobj(src, out)
    new_manifest = _read_manifest(staged)
    if not new_manifest or not (staged / "server.py").is_file() or not (staged / "version.py").is_file():
        raise RuntimeError("更新檔內容不對（缺少 server.py / version.py / manifest.json）")
    for rel, digest in new_manifest["files"].items():
        if not _safe(rel) or not (staged / rel).is_file() or file_sha256(staged / rel) != digest:
            raise RuntimeError(f"更新檔裡的 {rel} 和清單對不上")

    # 2. 備份將被覆蓋或刪除的舊檔
    old_manifest = _read_manifest(root) or {"files": {}}
    new_files = set(new_manifest["files"])
    removed = [r for r in old_manifest["files"] if r not in new_files and _safe(r) and (root / r).is_file()]
    touched = [r for r in new_files if (root / r).is_file()] + removed
    for rel in touched:
        (backup / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(root / rel, backup / rel)
    had_manifest = (root / MANIFEST).is_file()
    if had_manifest:
        shutil.copy2(root / MANIFEST, backup / MANIFEST)

    # 3. 換上新檔；任何一步失敗就還原
    written = []
    try:
        for rel in sorted(new_files) + [MANIFEST]:
            dest = root / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            tmp = dest.with_name(dest.name + ".updating")
            shutil.copy2(staged / rel, tmp)
            tmp.replace(dest)
            written.append(rel)
        for rel in removed:
            (root / rel).unlink(missing_ok=True)
    except Exception:
        for rel in written:
            if (backup / rel).is_file():
                shutil.copy2(backup / rel, root / rel)
            else:                       # 新版才有的檔案：拿掉
                (root / rel).unlink(missing_ok=True)
        for rel in removed:
            if (backup / rel).is_file() and not (root / rel).exists():
                shutil.copy2(backup / rel, root / rel)
        raise
    shutil.rmtree(work, ignore_errors=True)
    return new_manifest.get("version", "")
