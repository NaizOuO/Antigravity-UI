"""Lumina Code 題庫（開發者用，不會打包給朋友）：用固定的任務量模型表現，比較提示或工具改動前後的差異。

用法（介面伺服器要開著）：
    python bench/run.py dungeon                 跑一題，自動建立下一個 testN 資料夾
    python bench/run.py dungeon --model gemini-3.8-flash-high

每題記錄四個數字：通過率（題目自己的驗證程式，不是模型說的）、時間、tokens、詢問次數。
詢問一律照一般使用者的習慣回答：能選「這段對話都允許」就選，不然「允許一次」（不碰「永遠允許」，以免改到使用者的設定）。
結果附加到 bench/results.jsonl。
"""

import argparse
import json
import re
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
BASE = Path(r"C:\Users\User\Desktop\lumina code test")     # 使用者指定：測試專案都放這裡，test1、test2、…
API = "http://127.0.0.1:8765"
HDR = {"Content-Type": "application/json", "Origin": API}

TASKS = {
    "dungeon": {
        "prompt": ("用 Python 和 pygame 做一個俯視角的地牢探索小遊戲：每次開始隨機生成地牢（房間加走廊），玩家用方向鍵移動、"
                   "不能穿牆，有幾隻敵人會追玩家、碰到就扣血，地圖裡有鑰匙，撿到鑰匙才能打開門走到出口過關。請做到可以直接玩。\n"
                   "\n另外為了自動驗收，main.py 要提供下面這個介面（遊戲本身也要用同一套邏輯，不能另外寫一份）：\n"
                   "- 常數 TILE（格子邊長，像素）、PLAYER_SIZE、ENEMY_SIZE（玩家、敵人碰撞方塊的邊長，像素）\n"
                   "- generate_level(seed) 回傳 dict：grid 是 list[str]，每個字元一格（'#' 牆、'.' 地板、'D' 鎖住的門、'E' 出口）；"
                   "start、key 是 (x, y) 格子座標；enemies 是 [(x, y), ...] 格子座標\n"
                   "- step_player(level, pos, direction, dt, has_key) 回傳新的 (x, y)：pos 是玩家中心點的像素座標，"
                   "direction 是 (dx, dy)，dx、dy 為 -1、0 或 1，dt 是秒數\n"
                   "- step_enemy(level, pos, player_pos, dt) 回傳敵人新的 (x, y) 像素座標\n"
                   "import main 時不能開啟視窗或開始遊戲（用 if __name__ == \"__main__\":）。"),
        "verify": "verify/dungeon.py",
    },
}


def post(path, data, timeout=3600):
    return urllib.request.urlopen(urllib.request.Request(API + path, json.dumps(data).encode(), HDR), timeout=timeout)


def next_folder():
    nums = [int(m.group(1)) for p in BASE.glob("test*") if (m := re.fullmatch(r"test(\d+)", p.name))]
    d = BASE / f"test{max(nums, default=0) + 1}"
    d.mkdir(parents=True)
    return d


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("task", choices=TASKS)
    ap.add_argument("--model", default="gemini-3.8-flash-high")
    ap.add_argument("--note", default="", help="這次測的是什麼改動（寫進結果）")
    a = ap.parse_args()
    task = TASKS[a.task]
    folder = next_folder()
    ws = json.loads(post("/api/code/create", {"work_dir": str(folder)}).read())
    print(f"題目 {a.task}｜{a.model}｜{folder.name}", flush=True)

    t0, asks, tokens, cid, err, tools = time.time(), 0, None, None, None, 0
    for line in post("/api/chat", {"text": task["prompt"], "model": a.model, "code": ws["id"]}):
        e = json.loads(line)
        ev = e.get("event")
        su = e.get("step_update") or {}
        if su.get("step_type") == "tool" and su.get("state") == "ACTIVE":
            tools += 1
        if ev == "permission":
            asks += 1
            choice = "conv" if e.get("can_conv") else "once"
            print(f"  [{time.time() - t0:4.0f}s] 詢問 {asks}：{e['command'][:100]}", flush=True)
            post("/api/permission/answer", {"id": e["id"], "nonce": e["nonce"], "choice": choice}).read()
        if ev == "result":
            tokens = (e["result"].get("usage") or {}).get("turn_tokens")
        if ev == "done":
            cid, err = e.get("conversation_id"), e.get("error")
    secs = round(time.time() - t0)

    # 用題目自己的驗證程式檢查（跑在題目資料夾的 .venv 裡）
    py = folder / ".venv" / "Scripts" / "python.exe"
    v = subprocess.run([str(py if py.exists() else sys.executable), str(HERE / task["verify"]), str(folder)],
                       capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=900,
                       env={**__import__("os").environ, "PYTHONIOENCODING": "utf-8"})
    m = re.search(r"BENCH_RESULT (\{.*\})", v.stdout)
    verdict = json.loads(m.group(1)) if m else {"passed": 0, "total": 1, "checks": {"驗證程式執行失敗": False},
                                                 "error": (v.stderr or v.stdout)[-800:]}
    row = {"t": time.strftime("%Y-%m-%d %H:%M"), "task": a.task, "model": a.model, "folder": folder.name, "note": a.note,
           "seconds": secs, "tokens": tokens, "asks": asks, "tool_calls": tools, "error": err, "conversation": cid, **verdict}
    with open(HERE / "results.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")
    print(json.dumps(row, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
