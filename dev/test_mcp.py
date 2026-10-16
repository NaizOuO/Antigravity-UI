"""Lumina 工具伺服器（tools/lumina_mcp.py）的自我測試：改了工具之後先跑這支，每種用法都跑一次。

    python dev/test_mcp.py [有 pygame 遊戲的資料夾] [有 playwright 的網頁資料夾]

預設用 lumina code test\\test1（pygame 地牢）和 test28（網頁＋playwright）；找不到就跳過那一項。
"""
import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MCP = ROOT / "tools" / "lumina_mcp.py"
BASE = Path(r"C:\Users\User\Desktop\lumina code test")
GAME = Path(sys.argv[1]) if len(sys.argv) > 1 else BASE / "test1"
WEB = Path(sys.argv[2]) if len(sys.argv) > 2 else BASE / "test28"
results = []


def call(work, name, args):
    req = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": args}}
    r = subprocess.run([sys.executable, str(MCP), "--work", str(work), "--roots", str(work)], input=json.dumps(req) + "\n",
                       capture_output=True, text=True, encoding="utf-8", timeout=240)
    res = json.loads(r.stdout.strip().splitlines()[-1])["result"]
    return res["content"][0]["text"], res.get("isError", False)


def case(label, work, name, args, expect_error=False, must=()):
    if not work.exists():
        results.append((label, None, "跳過（找不到資料夾）"))
        return
    try:
        text, err = call(work, name, args)
        ok = err == expect_error and all(m in text for m in must) and "工具執行失敗" not in text
        results.append((label, ok, text.splitlines()[0][:90] if text else ""))
    except Exception as e:
        results.append((label, False, f"{type(e).__name__}: {e}"))


tmp = Path(tempfile.mkdtemp())
case("todo_write 沒寫 evidence 要拒絕", tmp, "todo_write", {"todos": [{"content": "x", "status": "completed"}]}, True)
case("todo_write 正常", tmp, "todo_write", {"todos": [{"content": "x", "status": "in_progress"}]}, must=["任務清單已更新"])
case("design_review", tmp, "design_review", {"kind": "game", "phase": "plan", "reference": "Pac-Man"}, must=["規格展開清單"])
case("asset_import 拒絕系統檔", tmp, "asset_import", {"source": r"C:\Windows\win.ini", "name": "x.png"}, True)
case("acceptance_set 找不到測試檔要拒絕", tmp, "acceptance_set", {"command": "python nope.py", "covers": ["x"]}, True)
case("run_capture 一般截圖", GAME, "run_capture", {"program": "main.py", "seconds": 3}, must=["截圖"])
case("run_capture 操作腳本", GAME, "run_capture", {"program": "main.py", "actions": ["wait 1", "hold right 0.5", "shot 往右"]},
     must=["[往右]"])
case("run_capture 連拍＋總覽圖", GAME, "run_capture", {"program": "main.py", "actions": ["wait 1", "rec right 0.8 4"]},
     must=["連拍總覽", "4/4"])
case("run_capture 動作格式錯誤", GAME, "run_capture", {"program": "main.py", "actions": ["jump"]}, True)
case("run_capture 網頁連拍", WEB, "run_capture", {"program": "index.html", "actions": ["wait 1", "rec w 0.6 3"]},
     must=["連拍總覽"])

bad = 0
for label, ok, note in results:
    mark = "跳過" if ok is None else ("通過" if ok else "失敗")
    bad += ok is False
    print(f"[{mark}] {label}：{note}")
print(f"\n{len(results) - bad}/{len(results)} 通過" + ("" if not bad else "  ← 有失敗，先修好再用"))
sys.exit(1 if bad else 0)
