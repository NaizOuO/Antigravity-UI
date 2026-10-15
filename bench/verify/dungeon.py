"""地牢題的驗證程式：只透過題目指定的驗收介面檢查，不管模型內部怎麼寫。

驗收介面（寫在題目裡）：
    TILE、PLAYER_SIZE、ENEMY_SIZE                     格子邊長、玩家／敵人碰撞方塊的邊長（像素）
    generate_level(seed) -> dict
        grid: list[str]，每個字元一格：'#' 牆、'.' 地板、'D' 鎖住的門、'E' 出口
        start、key: (x, y) 格子座標；enemies: [(x, y), ...] 格子座標
    step_player(level, pos, direction, dt, has_key) -> (x, y)   pos 是中心點像素座標，direction 是 (dx, dy)，dx、dy 為 -1、0、1
    step_enemy(level, pos, player_pos, dt) -> (x, y)
用法：python dungeon.py <題目資料夾>，最後一行輸出 BENCH_RESULT {...}
"""
import json
import math
import os
import random
import sys
from collections import deque

os.environ["SDL_VIDEODRIVER"] = "dummy"
GAME = sys.argv[1]
sys.path.insert(0, GAME)
os.chdir(GAME)
checks, notes = {}, {}


def report():
    print("BENCH_RESULT " + json.dumps({"passed": sum(checks.values()), "total": len(checks), "checks": checks,
                                        "notes": notes}, ensure_ascii=False))


try:
    import main as G
    T, PS, ES = G.TILE, G.PLAYER_SIZE, G.ENEMY_SIZE
    for name in ("generate_level", "step_player", "step_enemy"):
        getattr(G, name)
    checks["有驗收介面"] = True
except Exception as e:
    checks["有驗收介面"] = False
    notes["介面錯誤"] = f"{type(e).__name__}: {e}"
    report()
    sys.exit()


def walkable(level, x, y, has_key):
    g = level["grid"]
    if not (0 <= y < len(g) and 0 <= x < len(g[y])):
        return False
    c = g[y][x]
    return c in ".E" or (c == "D" and has_key) or c not in "#DE"     # 其他字元（裝飾、物件）當地板


def bfs(level, start, has_key):
    seen, q = {tuple(start)}, deque([tuple(start)])
    while q:
        x, y = q.popleft()
        for n in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
            if n not in seen and walkable(level, *n, has_key):
                seen.add(n); q.append(n)
    return seen


def find(level, ch):
    return [(x, y) for y, row in enumerate(level["grid"]) for x, c in enumerate(row) if c == ch]


# ---------- 1. 連通性：500 張隨機地牢 ----------
N = 500
bad = {"鑰匙到得了": 0, "開門後出口到得了": 0, "沒開門到不了出口": 0, "起點、鑰匙、敵人不在牆裡": 0}
for i in range(N):
    try:
        lv = G.generate_level(i)
        exits = find(lv, "E")
        closed, opened = bfs(lv, lv["start"], False), bfs(lv, lv["start"], True)
        ok = {"鑰匙到得了": tuple(lv["key"]) in closed,
              "開門後出口到得了": bool(exits) and all(e in opened for e in exits),
              "沒開門到不了出口": not any(e in closed for e in exits),
              "起點、鑰匙、敵人不在牆裡": all(lv["grid"][y][x] != "#" for x, y in [lv["start"], lv["key"], *lv["enemies"]])}
    except Exception as e:
        ok = {k: False for k in bad}
        notes.setdefault("生成時出錯", f"seed={i}：{type(e).__name__}: {e}")
    for k, v in ok.items():
        if not v:
            bad[k] += 1
            notes.setdefault(k, f"失敗 {bad[k]} 次以上，例如 seed={i}")
for k, v in bad.items():
    checks[k] = v == 0


# ---------- 2. 穿牆與卡死：玩家亂走、敵人追 ----------
def hits_wall(level, pos, size, has_key):
    x0, y0 = pos[0] - size / 2, pos[1] - size / 2
    for gx in range(int(x0 // T), int((x0 + size - 0.01) // T) + 1):
        for gy in range(int(y0 // T), int((y0 + size - 0.01) // T) + 1):
            if not walkable(level, gx, gy, has_key):
                return True
    return False


clip_p = clip_e = stuck = jump = steps = 0
dirs = [(1, 0), (-1, 0), (0, 1), (0, -1), (1, 1), (-1, -1)]
for i in range(60):
    random.seed(9000 + i)
    try:
        lv = G.generate_level(1000 + i)
        p = ((lv["start"][0] + .5) * T, (lv["start"][1] + .5) * T)
        es = [((x + .5) * T, (y + .5) * T) for x, y in lv["enemies"]]
        d = random.choice(dirs)
        for s in range(1200):
            if s % 25 == 0:
                d = random.choice(dirs)
            dt = random.choice([1 / 60, 1 / 30, 0.1])            # 0.1 秒：模擬嚴重卡頓
            q = G.step_player(lv, p, d, dt, False)
            if math.dist(q, p) > T * 0.9:                         # 一步移動將近一格以上 = 瞬移
                jump += 1
            p = q
            clip_p += hits_wall(lv, p, PS, False)
            es = [G.step_enemy(lv, e, p, dt) for e in es]
            clip_e += sum(hits_wall(lv, e, ES, False) for e in es)
            steps += 1
        moved = any(math.dist(G.step_player(lv, p, dd, 1 / 30, False), p) > 0.5 for dd in dirs[:4])
        stuck += not moved
    except Exception as e:
        notes.setdefault("模擬時出錯", f"{type(e).__name__}: {e}")
        clip_p += 1
checks["玩家不穿牆"] = clip_p == 0
checks["敵人不穿牆"] = clip_e == 0
checks["卡頓時不會瞬移"] = jump == 0
checks["玩家不會被卡死"] = stuck == 0
notes["模擬"] = f"{steps} 步：玩家穿牆 {clip_p}、敵人穿牆 {clip_e}、瞬移 {jump}、卡死 {stuck}"
report()
