"""Lumina Code 題庫（開發者用，不會打包給朋友）：用固定的任務量模型表現，比較提示或工具改動前後的差異。

用法（介面伺服器要開著）：
    python bench/run.py dungeon                 跑一題，自動建立下一個 testN 資料夾
    python bench/run.py dungeon --model gemini-3.8-flash-high

每題記錄四個數字：通過率（題目自己的驗證程式，不是模型說的）、時間、tokens、詢問次數。
詢問一律照一般使用者的習慣回答：能選「這段對話都允許」就選，不然「允許一次」（不碰「永遠允許」，以免改到使用者的設定）。
結果附加到 bench/results.jsonl。
"""

import argparse
import os
import shutil
import threading
from concurrent.futures import ThreadPoolExecutor
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
    # 比較接近朋友的薩爾達案例：接手一份有 bug 的既有程式，修好並維持介面
    "dungeon_fix": {
        "seed": "seeds/dungeon_buggy",
        "prompt": ("這個資料夾裡的 main.py 是朋友寫的俯視角地牢小遊戲（pygame），玩起來很多問題："
                   "有時候會穿牆、卡頓一下角色會飛出去、敵人會穿過牆追人、有時候拿不到鑰匙或走不到出口、角色偶爾卡在牆邊動不了。"
                   "請把這些問題都修好，讓遊戲可以正常玩完。\n"
                   "注意：main.py 裡的 TILE、PLAYER_SIZE、ENEMY_SIZE、generate_level(seed)、step_player(level, pos, direction, dt, has_key)、"
                   "step_enemy(level, pos, player_pos, dt) 是自動驗收要用的介面，名稱、參數和回傳格式都不能改"
                   "（grid 是 list[str]，'#' 牆、'.' 地板、'D' 門、'E' 出口；座標格式照原本的寫法），遊戲本身也要繼續用這幾個函式。"),
        "verify": "verify/dungeon.py",
    },
}
# 中型專案（14 個模組）：一個功能牽動資料、角色、戰鬥、商店、存檔、介面。評分含 44 項模型看不到的回歸測試
TASKS["rpg_weight"] = {
    "seed": "seeds/rpg", "verify": "verify/rpg.py", "verify_args": ["weight"],
    "prompt": ("這個資料夾是我的文字 RPG 專案（說明在 README.md）。我想加入「負重」系統：\n"
               "1. 每個物品要有重量（公斤）：武器 6、防具 10（板甲 18）、飾品 1、消耗品 0.5、素材 1、任務物品 0。\n"
               "2. 玩家的負重上限 = 20 + 目前的攻擊力（裝備、狀態效果、等級都會影響攻擊力，所以上限也會跟著變）。\n"
               "3. 目前重量 = 背包裡所有物品 + 身上的裝備。\n"
               "4. 超過上限（剛好等於不算）就是超重：敏捷減半（無條件捨去，在其他加成都算完之後才減半），"
               "所以戰鬥的行動順序和逃跑機率都會受影響。超重不會影響負重上限本身。\n"
               "5. 在商店買東西時，買了會超重就不能買（丟 ShopError，錢和背包都不變）。戰利品和任務獎勵照拿，可以超重。\n"
               "6. 存檔格式版本升到 3；以前的存檔（版本 1、2）都要還能讀。\n"
               "7. 狀態畫面（cli.py 的 show_status）要顯示負重，格式像「負重 6/28」。\n"
               "\n介面（自動驗收會用）：物品的 .weight；玩家的 carried_weight()、carry_capacity()、overweight（屬性，True/False）；"
               "超重時 player.stats().agi 就是減半後的值。\n"
               "原本的功能都不能壞掉。"),
}
# 模糊需求：像真實使用者一樣只講想要什麼，要改哪些地方由模型自己找（評分只看合理行為）
TASKS["rpg_weight_vague"] = {
    "seed": "seeds/rpg", "verify": "verify/rpg.py", "verify_args": ["weight_vague"],
    "prompt": ("這是我的文字 RPG（說明在 README.md）。我想加負重：東西帶太多角色會變慢，商店也不該讓人買到背不動，"
               "狀態畫面要看得到目前負重。數值你決定，合理就好。\n"
               "自動驗收會用到：物品的 .weight、玩家的 carried_weight()、carry_capacity()、overweight（True/False）。"
               "原本的功能不能壞掉。"),
}
# 分散的 bug：症狀和原因在不同模組，只用玩家的口吻描述
TASKS["rpg_bugs"] = {
    "seed": "seeds/rpg_buggy", "verify": "verify/rpg.py", "verify_args": ["bugs"],
    "prompt": ("這是我的文字 RPG（說明在 README.md），朋友試玩後回報了幾個問題：\n"
               "- 讀以前的存檔，角色變弱了，好像身上的裝備都不見了\n"
               "- 戴生命戒指補滿血再拿下來，血量比上限還高\n"
               "- 喝迅捷藥劑、穿疾風靴，戰鬥時還是比野狼晚出手\n"
               "- 讀檔之後去打史萊姆，任務進度一直是 0\n"
               "- 背包滿的時候在商店買東西，錢被扣了但東西沒拿到\n"
               "請幫我修好，原本正常的功能不要改壞。"),
}
# 從零做能玩的遊戲（使用者會親自玩、看截圖打分）
LOOKS = "角色和敵人要有外觀設計（例如有眼睛、會動的造型），不要只用方塊代替。"
TASKS["pacman"] = {
    "verify": "verify/pacman.py", "launch": True,
    "prompt": ("用 Python 和 pygame 做一個 Pac-Man（小精靈）遊戲，要可以直接玩：吃豆子、大力丸、四隻鬼、計分、生命。"
               "四隻鬼要有各自不同的追人方式，請用你自己設計的邏輯，不要照抄原版遊戲的程式碼。"
               "小精靈要會張嘴，鬼要有眼睛和各自的顏色。" + LOOKS + "\n"
               "\n另外為了自動驗收，main.py 要提供下面的介面（遊戲本身也用同一套邏輯）：\n"
               "- 常數 TILE（格子邊長，像素）、PACMAN_SIZE、GHOST_SIZE（碰撞方塊邊長，像素）\n"
               "- Game(seed) 建立一局遊戲；import main 和建立 Game 都不能開視窗（視窗在 if __name__ == \"__main__\": 裡才開）\n"
               "- game.maze：list[str]，'#' 牆、'.' 豆子、'o' 大力丸、' ' 空地、'-' 鬼屋的門（小精靈不能通過）；豆子被吃掉要變成 ' '\n"
               "- game.pacman：小精靈中心點的像素座標 (x, y)\n"
               "- game.ghost_positions()：4 個 (x, y)；game.ghost_modes()：4 個字串，"
               "用 'chase'、'scatter'、'frightened'（吃了大力丸後）、'eaten'（被吃掉回家中）\n"
               "- game.update(dt, direction)：前進 dt 秒，direction 是 (dx, dy) 或 None（不按鍵）\n"
               "- game.score、game.lives、game.pellets_left()、game.over（命用完為 True）"),
}
# 時之笛第三輪：真正的 3D 引擎 + 美術 + 分三輪開發（規格書 seeds/zelda3/SPEC.md）
ZELDA3_ROUNDS = [
    "第二輪：照 SPEC.md 第 5 節的第 2 輪做（草原、陶笛與時之歌石門、蝙蝠、村落 NPC 對話），"
    "做完跑第 6 節的驗收數字，並實際操作截圖確認。",
    "第三輪：照 SPEC.md 第 5 節的第 3 輪做（三段連擊、頭目兩階段、音樂、打擊感、標題、死亡、暫停、結局）。"
    "最後用 design_review（kind=game, phase=review, reference=薩爾達傳說 時之笛）對照整份 SPEC.md 審查，"
    "特別是第 1 節美術方向的每一項，缺的補齊；跑第 6 節的驗收數字並實際操作截圖確認。",
]
ZELDA3_FIRST = ("照這個資料夾的 SPEC.md 做《時之笛》風格的 3D 遊戲，用{ver}。會分三輪做，這是第一輪："
                "只做 SPEC.md 第 5 節列的第 1 輪內容，美術照第 1 節的數值做。"
                "做完跑第 6 節的驗收數字，並用 run_capture 的 actions 實際操作截圖確認手感和畫面。")
TASKS["zelda3_py"] = {"seed": "seeds/zelda3", "verify": "verify/launch.py", "followups": ZELDA3_ROUNDS,
                      "prompt": ZELDA3_FIRST.format(ver="版本 A（Python + Ursina）")}
TASKS["zelda3_web"] = {"seed": "seeds/zelda3", "verify": "verify/launch.py", "verify_args": ["index.html"],
                       "followups": ZELDA3_ROUNDS, "prompt": ZELDA3_FIRST.format(ver="版本 B（網頁 + Three.js）")}
# 協作：Claude 寫規格書（SPEC.md）和驗收測試，Lumina 實作
TASKS["pacman_spec"] = {
    "seed": "seeds/pacman_spec", "verify": "verify/pacman.py", "verify_args": ["spec"], "launch": True,
    "prompt": ("照這個資料夾裡的 SPEC.md 做一個 Pac-Man 遊戲。SPEC.md 是完整的需求，每一節都要做到；"
               "acceptance_test.py 是驗收測試（不要修改它），全部通過才算完成。"
               "另外一定要用 run_capture 的 actions 實際玩過，確認手感和畫面。"),
}
TASKS["zelda"] = {
    "verify": "verify/launch.py",
    "prompt": ("用 Python 做一個《薩爾達傳說 時之笛》風格的 3D 遊戲（第三人稱視角，像原作那樣在 3D 場景裡跑動、"
               "用劍打敵人），要可以直接玩，主程式是 main.py。" + LOOKS),
}
# 協作情境：Claude Code 派工時附上驗收測試（Lumina 只負責做到測試通過）
TASKS["dungeon_fix_spec"] = {**TASKS["dungeon_fix"], "give_test": "acceptance_test.py",
    "prompt": TASKS["dungeon_fix"]["prompt"] + (
        "\n\n驗收方式：資料夾裡的 acceptance_test.py 是驗收測試，用 .venv\\Scripts\\python acceptance_test.py . 執行，"
        "最後一行 BENCH_RESULT 裡每一項都要是 true 才算完成。不要修改 acceptance_test.py。"
        "完成後回報：改了哪些地方、acceptance_test.py 的最後結果。")}


def post(path, data, timeout=3600):
    return urllib.request.urlopen(urllib.request.Request(API + path, json.dumps(data).encode(), HDR), timeout=timeout)


FOLDER_LOCK = threading.Lock()
WRITE_LOCK = threading.Lock()


def next_folder():
    with FOLDER_LOCK:                     # 同時跑好幾個時，資料夾編號不能撞
        nums = [int(m.group(1)) for p in BASE.glob("test*") if (m := re.fullmatch(r"test(\d+)", p.name))]
        d = BASE / f"test{max(nums, default=0) + 1}"
        d.mkdir(parents=True)
        return d


def diff_stats(seed, folder):
    """跟原始專案比：改了哪些檔案、改了幾行（看模型有沒有亂改）。"""
    import difflib
    if not seed:
        return {}
    skip = {".venv", "__pycache__", ".agents", ".git"}
    files, lines = [], 0
    for f in sorted(seed.rglob("*")):
        if f.is_file() and not skip & set(f.relative_to(seed).parts):
            g = folder / f.relative_to(seed)
            a = f.read_text(encoding="utf-8", errors="replace").splitlines()
            b = g.read_text(encoding="utf-8", errors="replace").splitlines() if g.exists() else []
            n = sum(1 for d in difflib.unified_diff(a, b, lineterm="", n=0) if d[:1] in "+-" and d[:3] not in ("+++", "---"))
            if n:
                files.append(str(f.relative_to(seed)))
                lines += n
    added = [str(g.relative_to(folder)) for g in folder.rglob("*")
             if g.is_file() and not skip & set(g.relative_to(folder).parts) and not (seed / g.relative_to(folder)).exists()]
    return {"changed_files": files, "changed_lines": lines, "added_files": added}


def run_one(name, model, note, tag=""):
    task = TASKS[name]
    folder = next_folder()
    if task.get("seed"):
        shutil.copytree(HERE / task["seed"], folder, dirs_exist_ok=True)
    if task.get("give_test"):
        shutil.copy(HERE / task["verify"], folder / task["give_test"])
    ws = json.loads(post("/api/code/create", {"work_dir": str(folder)}).read())
    say = lambda m: print(f"[{folder.name}] {m}", flush=True)
    say(f"題目 {name}｜{model}")

    t0, asks, tokens, cid, err, tools = time.time(), 0, 0, None, None, 0
    # 多輪題目：同一段對話依序交代（模擬真人一輪一輪調整），前一輪出錯就停
    for n, text in enumerate([task["prompt"]] + task.get("followups", [])):
        if n:
            say(f"[{time.time() - t0:4.0f}s] 第 {n + 1} 輪")
        try:
            body = {"text": text, "model": model, "code": ws["id"], **({"conversation_id": cid} if cid else {})}
            for line in post("/api/chat", body):
                e = json.loads(line)
                ev = e.get("event")
                su = e.get("step_update") or {}
                if su.get("step_type") == "tool" and su.get("state") == "ACTIVE":
                    tools += 1
                if ev == "permission":
                    asks += 1
                    choice = "conv" if e.get("can_conv") else "once"
                    say(f"[{time.time() - t0:4.0f}s] 詢問 {asks}：{e['command'][:100]}")
                    post("/api/permission/answer", {"id": e["id"], "nonce": e["nonce"], "choice": choice}).read()
                if ev == "result":
                    tokens += (e["result"].get("usage") or {}).get("turn_tokens") or 0
                    say(f"[{time.time() - t0:4.0f}s] 回答出現（agy 結束）")
                if ev in ("accept_checking", "code_fixing", "pdf"):
                    say(f"[{time.time() - t0:4.0f}s] 回答後處理：{ev} {e.get('name', '')}")
                if ev == "done":
                    cid, err = e.get("conversation_id") or cid, e.get("error")
                    say(f"[{time.time() - t0:4.0f}s] 這一輪結束")
        except Exception as e:             # 連線斷掉之類：照樣評分，記下錯誤
            err = f"{type(e).__name__}: {e}"
        if err:
            break
    secs = round(time.time() - t0)

    # 用題目自己的驗證程式檢查（跑在題目資料夾的 .venv 裡）
    py = folder / ".venv" / "Scripts" / "python.exe"
    v = subprocess.run([str(py if py.exists() else sys.executable), str(HERE / task["verify"]), str(folder),
                        *task.get("verify_args", [])],
                       capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=900,
                       env={**os.environ, "PYTHONIOENCODING": "utf-8"})
    m = re.search(r"BENCH_RESULT (\{.*\})", v.stdout)
    verdict = json.loads(m.group(1)) if m else {"passed": 0, "total": 1, "checks": {"驗證程式執行失敗": False},
                                                 "error": (v.stderr or v.stdout)[-800:]}
    if task.get("launch"):                 # 另外實際啟動、截圖（給使用者看）
        lv = subprocess.run([sys.executable, str(HERE / "verify" / "launch.py"), str(folder)], capture_output=True,
                            text=True, encoding="utf-8", errors="replace", timeout=300,
                            env={**os.environ, "PYTHONIOENCODING": "utf-8"})
        lm = re.search(r"BENCH_RESULT (\{.*\})", lv.stdout)
        if lm:
            lr = json.loads(lm.group(1))
            verdict["checks"].update({"畫面：" + k: v for k, v in lr["checks"].items()})
            verdict["passed"] += lr["passed"]
            verdict["total"] += lr["total"]
    row = {"t": time.strftime("%Y-%m-%d %H:%M"), "task": name, "model": model, "folder": folder.name, "note": note,
           "batch": tag, "seconds": secs, "tokens": tokens, "asks": asks, "tool_calls": tools, "error": err,
           "conversation": cid, **diff_stats(HERE / task["seed"] if task.get("seed") else None, folder), **verdict}
    with WRITE_LOCK, open(HERE / "results.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")
    short = {k: row.get(k) for k in ("folder", "passed", "total", "regression", "feature", "seconds", "tokens",
                                     "asks", "tool_calls", "changed_lines", "error")}
    say("結果 " + json.dumps(short, ensure_ascii=False))
    return row


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("task", choices=TASKS)
    ap.add_argument("--model", default="gemini-3.8-flash-high")
    ap.add_argument("--note", default="", help="這次測的是什麼改動（寫進結果）")
    ap.add_argument("-n", type=int, default=1, help="同時跑幾份（各用自己的 testN 資料夾）")
    a = ap.parse_args()
    tag = time.strftime("%m%d-%H%M%S")
    with ThreadPoolExecutor(a.n) as ex:
        futs = []
        for i in range(a.n):
            futs.append(ex.submit(run_one, a.task, a.model, a.note, tag))
            time.sleep(3)                 # 錯開啟動，避免同時建立資料夾、同時下載套件
        rows = [f.result() for f in futs]
    if a.n > 1:
        ps = [r["passed"] / r["total"] for r in rows]
        print(f"\n== {a.n} 份平均：通過 {sum(ps) / len(ps):.0%}，時間 {sum(r['seconds'] for r in rows) / len(rows):.0f} 秒，"
              f"tokens {sum(r['tokens'] or 0 for r in rows) / len(rows):,.0f}")


if __name__ == "__main__":
    main()
