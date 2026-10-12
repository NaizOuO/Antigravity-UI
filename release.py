"""發布新版（開發者用）：改版本號 → 產生 manifest.json 與 zip → git commit、tag、push → 建 GitHub Release。

用法：
    python release.py 1.1.0 "更新內容第一行" "第二行" ...
    python release.py --pack           只產生 dist/ 裡的 zip（不改版本、不上傳）

使用者的電腦會從 Release 的 zip 自動更新，zip 裡只放 PROGRAM_FILES，
data/（個人資料）不會被打包，也不會進 git（見 .gitignore）。
"""

import hashlib
import json
import re
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DIST = ROOT / "dist"
PROGRAM_FILES = ["server.py", "version.py", "updater.py", "static/index.html",
                 "啟動.vbs", "關閉.bat", "打包分享.bat", "README.md"]


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def current_version():
    return re.search(r'VERSION = "([^"]+)"', (ROOT / "version.py").read_text(encoding="utf-8")).group(1)


def set_version(ver):
    p = ROOT / "version.py"
    p.write_text(re.sub(r'VERSION = "[^"]+"', f'VERSION = "{ver}"', p.read_text(encoding="utf-8")), encoding="utf-8")


def pack(ver):
    manifest = {"version": ver, "files": {f: sha256(ROOT / f) for f in PROGRAM_FILES}}
    (ROOT / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
    DIST.mkdir(exist_ok=True)
    out = DIST / f"Antigravity-UI-v{ver}.zip"
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in PROGRAM_FILES + ["manifest.json"]:
            zf.write(ROOT / f, f"Antigravity-UI/{f}")
    print(f"已產生 {out}")
    return out


def run(*cmd):
    print(">", " ".join(cmd))
    subprocess.run(cmd, cwd=ROOT, check=True)


def main(argv):
    sys.stdout.reconfigure(encoding="utf-8")
    if argv[:1] == ["--pack"]:
        pack(current_version())
        return
    if not argv or not re.fullmatch(r"\d+\.\d+\.\d+", argv[0]):
        sys.exit(__doc__)
    ver, notes = argv[0], argv[1:] or ["更新"]
    set_version(ver)
    zip_path = pack(ver)
    body = "\n".join(f"- {n}" for n in notes)
    run("git", "add", "-A")
    run("git", "commit", "-m", f"Antigravity UI v{ver}\n\n{body}")
    run("git", "tag", f"v{ver}")
    run("git", "push", "origin", "HEAD", f"v{ver}")
    run("gh", "release", "create", f"v{ver}", str(zip_path), "--title", f"v{ver}", "--notes", body)


if __name__ == "__main__":
    main(sys.argv[1:])
