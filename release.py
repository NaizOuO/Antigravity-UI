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
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DIST = ROOT / "dist"
LEGACY_ASSET = False                # 改名 Lumina 的過渡期：同時附上舊檔名的安裝檔
PROGRAM_FILES = ["server.py", "version.py", "updater.py", "components.py", "command_guard.py", "checkpoints.py", "tools/lumina.py", "tools/lumina_mcp.py", "tools/lumina_web.py", "tools/local_backend.py", "static/index.html",
                 "templates/agents/rules/lumina.md", "templates/agents/rules/lumina-plan.md", "templates/agents/rules/lumina-field-check.md",
                 "templates/agents/skills/lumina-visual-verify/SKILL.md",
                 "skills/pdf-document/SKILL.md", "skills/pdf-document/references/packages.md",
                 "skills/pdf-document/references/notes.typ", "skills/pdf-document/references/report.typ",
                 "skills/pdf-document/references/slides.typ", "skills/pdf-document/references/letter.typ", "static/icon.png", "static/icon.ico",
                 "啟動.vbs", "關閉.bat", "打包分享.bat", "README.md"] + [
                 f"static/backgrounds/{n}-{t}.jpg" for n in ("wave", "contour", "bloch", "dusk", "field")
                 for t in ("dark", "light")]


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
    out = DIST / f"Lumina-v{ver}.zip"
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in PROGRAM_FILES + ["manifest.json"]:
            zf.write(ROOT / f, f"Lumina/{f}")
    # 過渡期：再附一份舊檔名的（內容相同）。v1.7.0 以前的更新程式只認得 Antigravity-UI- 開頭，
    # 很久沒開介面的朋友也能直接更新；確定大家都更新過後，把 LEGACY_ASSET 改成 False 就不再附
    legacy = DIST / f"Antigravity-UI-v{ver}.zip"
    if LEGACY_ASSET:
        shutil.copyfile(out, legacy)
    print(f"已產生 {out}" + (f" 與 {legacy.name}" if LEGACY_ASSET else ""))
    return [out] + ([legacy] if LEGACY_ASSET else [])


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
    zips = pack(ver)
    body = "\n".join(f"- {n}" for n in notes)
    run("git", "add", "-A")
    run("git", "commit", "-m", f"Lumina v{ver}\n\n{body}")
    run("git", "tag", f"v{ver}")
    run("git", "push", "origin", "HEAD", f"v{ver}")
    run("gh", "release", "create", f"v{ver}", *map(str, zips), "--title", f"v{ver}", "--notes", body)


if __name__ == "__main__":
    main(sys.argv[1:])
