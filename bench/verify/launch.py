"""通用的「能不能玩」檢查：在隱藏桌面啟動 main.py 15 秒、截圖（給使用者看），確認沒有當掉。
沿用 Lumina 的 run_capture（tools/lumina_mcp.py），不會在使用者的螢幕上開視窗。

用法：python launch.py <題目資料夾> [程式，預設 main.py]
"""
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
MCP = HERE.parent.parent / "tools" / "lumina_mcp.py"
folder = Path(sys.argv[1]).resolve()
prog = sys.argv[2] if len(sys.argv) > 2 else "main.py"
checks, notes = {}, {}

if not (folder / prog).exists():
    checks["有主程式"] = False
    notes["有主程式"] = f"找不到 {prog}"
else:
    checks["有主程式"] = True
    req = {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
           "params": {"name": "run_capture", "arguments": {"program": prog, "seconds": 15}}}
    r = subprocess.run([sys.executable, str(MCP), "--work", str(folder), "--roots", str(folder)],
                       input=json.dumps(req) + "\n", capture_output=True, text=True, encoding="utf-8", timeout=180)
    res = json.loads(r.stdout.strip().splitlines()[-1])["result"]
    text = res["content"][0]["text"]
    crashed = bool(re.search(r"Traceback|Error:|Exception", text))
    shots = re.findall(r"^([A-Za-z]:\\.+?\.png)", text, re.M)        # 路徑可能有空格（AI sandbox）
    checks["啟動 15 秒沒有當掉"] = not crashed and not res.get("isError")
    checks["有畫面（截到視窗）"] = bool(shots)
    if shots:                                   # 截圖複製到題目資料夾，方便使用者看
        for i, s in enumerate(shots[:3]):
            try:
                shutil.copy(s, folder / f"_bench_screenshot_{i}.png")
            except OSError:
                pass
    notes["run_capture"] = text[-1200:]

print("BENCH_RESULT " + json.dumps({"passed": sum(checks.values()), "total": len(checks), "checks": checks,
                                    "notes": notes}, ensure_ascii=False))
