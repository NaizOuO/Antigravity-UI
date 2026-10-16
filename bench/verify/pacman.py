"""Pac-Man 題的評分程式：只透過題目指定的介面檢查（模型看不到這支）。

介面（寫在題目裡）：
    TILE、PACMAN_SIZE、GHOST_SIZE
    Game(seed) ；import main、建立 Game 都不能開視窗
    game.maze: list[str]  '#' 牆、'.' 豆子、'o' 大力丸、' ' 空地、'-' 鬼屋的門（小精靈不能過）
    game.pacman -> (x, y) 中心點像素座標
    game.ghost_positions() -> 4 個 (x, y)；game.ghost_modes() -> 4 個字串
    game.update(dt, direction)  direction 是 (dx, dy) 或 None
    game.score、game.lives、game.pellets_left()、game.over
用法：python pacman.py <題目資料夾>
"""
import json
import math
import os
import random
import sys
import traceback
from collections import deque

os.environ["SDL_VIDEODRIVER"] = "dummy"
os.environ["SDL_AUDIODRIVER"] = "dummy"
GAME = sys.argv[1]
sys.path.insert(0, GAME)
os.chdir(GAME)
checks, notes = {}, {}


def report():
    print("BENCH_RESULT " + json.dumps({"passed": sum(checks.values()), "total": len(checks), "checks": checks,
                                        "notes": notes}, ensure_ascii=False))


try:
    import main as M
    T, PS, GS = M.TILE, M.PACMAN_SIZE, M.GHOST_SIZE
    g0 = M.Game(0)
    for name in ("maze", "pacman", "score", "lives", "over"):
        getattr(g0, name)
    assert len(g0.ghost_positions()) == 4 and len(g0.ghost_modes()) == 4, "要有 4 隻鬼"
    g0.pellets_left()
    checks["有驗收介面"] = True
except Exception as e:
    checks["有驗收介面"] = False
    notes["介面錯誤"] = f"{type(e).__name__}: {e}"
    report()
    sys.exit()

DIRS = [(1, 0), (-1, 0), (0, 1), (0, -1)]


def tile(p):
    return int(p[0] // T), int(p[1] // T)


def cell(maze, x, y):
    if 0 <= y < len(maze) and 0 <= x < len(maze[y]):
        return maze[y][x]
    return " "                                    # 迷宮外：隧道


def overlaps(maze, pos, size, blocked):
    x0, y0 = pos[0] - size / 2 + 0.5, pos[1] - size / 2 + 0.5     # 容許 0.5 像素誤差
    x1, y1 = pos[0] + size / 2 - 0.5, pos[1] + size / 2 - 0.5
    for gx in range(int(x0 // T), int(x1 // T) + 1):
        for gy in range(int(y0 // T), int(y1 // T) + 1):
            if cell(maze, gx, gy) in blocked:
                return True
    return False


def wrapped(a, b, maze):
    w = len(maze[0]) * T
    return abs(a[0] - b[0]) > w / 2


def path_dir(maze, start, goal_fn):
    """從 start 格子用 BFS 找最近符合 goal_fn 的格子，回傳第一步的方向。"""
    h, w = len(maze), len(maze[0])
    q, seen = deque([(start, None)]), {start}
    while q:
        (x, y), first = q.popleft()
        if goal_fn(x, y) and first:
            return first
        for d in DIRS:
            nx, ny = (x + d[0]) % w, y + d[1]
            if 0 <= ny < h and (nx, ny) not in seen and cell(maze, nx, ny) not in "#-":
                seen.add((nx, ny))
                q.append(((nx, ny), first or d))
    return None


def guard(name, fn):
    try:
        fn()
    except AssertionError as e:
        checks[name] = False
        notes[name] = str(e)[:300]
    except Exception as e:
        checks[name] = False
        notes[name] = f"{type(e).__name__}: {e}"[:200] + " | " + traceback.format_exc().strip().splitlines()[-3][:150]
    else:
        checks.setdefault(name, True)


# ---------- 1. 迷宮：豆子都吃得到 ----------
def c_maze():
    g = M.Game(0)
    maze = g.maze
    st = tile(g.pacman)
    h, w = len(maze), len(maze[0])
    seen, q = {st}, deque([st])
    while q:
        x, y = q.popleft()
        for d in DIRS:
            n = ((x + d[0]) % w, y + d[1])
            if 0 <= n[1] < h and n not in seen and cell(maze, *n) not in "#-":
                seen.add(n)
                q.append(n)
    pel = [(x, y) for y, r in enumerate(maze) for x, c in enumerate(r) if c in ".o"]
    assert len(pel) >= 50, f"豆子太少：{len(pel)}"
    miss = [p for p in pel if p not in seen]
    assert not miss, f"有 {len(miss)} 顆豆子吃不到，例如格子 {miss[0]}"
    assert sum(r.count("o") for r in maze) >= 2, "大力丸少於 2 顆"
    assert g.pellets_left() == len(pel), f"pellets_left() = {g.pellets_left()}，迷宮裡有 {len(pel)} 顆"


guard("迷宮的豆子都吃得到", c_maze)


# ---------- 2. 隨機亂走：穿牆、瞬移、出錯 ----------
def c_random():
    clip_p = clip_g = jump = steps = 0
    for s in range(12):
        rnd = random.Random(100 + s)
        g = M.Game(s)
        d = rnd.choice(DIRS)
        prev_p, prev_g, lives = g.pacman, g.ghost_positions(), g.lives
        prev_st = getattr(g, "state", "playing")
        for i in range(2400):
            if i % 20 == 0:
                d = rnd.choice(DIRS + [None])
            dt = rnd.choice([1 / 60, 1 / 60, 1 / 30, 0.1])
            g.update(dt, d)
            p, gs = g.pacman, g.ghost_positions()
            clip_p += overlaps(g.maze, p, PS, "#-")
            for gp, m in zip(gs, g.ghost_modes()):
                clip_g += overlaps(g.maze, gp, GS, "#")
            st = getattr(g, "state", "playing")
            if (math.dist(p, prev_p) > T and not wrapped(p, prev_p, g.maze) and not g.over and g.lives == lives
                    and st == "playing" and prev_st == "playing"):
                jump += 1                         # 被抓到回到起點不算瞬移
            prev_p, prev_g, lives, prev_st = p, gs, g.lives, st
            steps += 1
            if g.over:
                break
    notes["亂走模擬"] = f"{steps} 步：小精靈穿牆 {clip_p}、鬼穿牆 {clip_g}、小精靈一步超過一格 {jump}"
    checks["鬼不穿牆"] = clip_g == 0
    checks["卡頓時不會瞬移"] = jump == 0
    checks["長時間亂玩不出錯"] = True
    assert clip_p == 0, f"小精靈和牆重疊 {clip_p} 步"


guard("小精靈不穿牆", c_random)
for k in ("鬼不穿牆", "卡頓時不會瞬移", "長時間亂玩不出錯"):
    if k not in checks:
        checks[k] = False


# ---------- 3. 自動駕駛：吃豆、大力丸、吃鬼 ----------
def autopilot(g, want):
    return path_dir(g.maze, tile(g.pacman), lambda x, y: g.maze[y][x] in want) if 0 <= tile(g.pacman)[1] < len(g.maze) else None


def c_eat():
    g = M.Game(1)
    s0, n0 = g.score, g.pellets_left()
    for _ in range(600):
        g.update(1 / 60, autopilot(g, "."))
    assert g.pellets_left() < n0 - 5, f"走了 10 秒只吃掉 {n0 - g.pellets_left()} 顆豆子"
    assert g.score > s0, "吃豆子沒有加分"
    # 迷宮裡的豆子要跟著消失
    left = sum(r.count(".") + r.count("o") for r in g.maze)
    assert left == g.pellets_left(), f"迷宮裡剩 {left} 顆，pellets_left() 說 {g.pellets_left()}"


guard("吃豆子加分、豆子消失", c_eat)


def c_power():
    ok_fright = ok_eat = False
    for seed in range(4):
        g = M.Game(seed)
        for i in range(3600):
            n = sum(r.count("o") for r in g.maze)
            modes = g.ghost_modes()
            if any(m == "frightened" for m in modes):
                ok_fright = True
                # 追最近的害怕的鬼
                fr = [tile(p) for p, m in zip(g.ghost_positions(), modes) if m == "frightened"]
                d = path_dir(g.maze, tile(g.pacman), lambda x, y: (x, y) in fr)
                before = g.score
                g.update(1 / 60, d)
                if any(m == "eaten" for m in g.ghost_modes()) or g.score - before >= 200:
                    ok_eat = True
                    break
            else:
                g.update(1 / 60, autopilot(g, "o") if n else autopilot(g, "."))
            if g.over:
                break
        if ok_eat:
            break
    assert ok_fright, "吃了大力丸，鬼沒有進入 frightened"
    checks["大力丸後可以吃鬼"] = ok_eat
    if not ok_eat:
        notes["大力丸後可以吃鬼"] = "鬼變害怕後追上去，沒有出現 eaten 也沒有加分"


guard("大力丸讓鬼害怕", c_power)
checks.setdefault("大力丸後可以吃鬼", False)


# ---------- 4. 四隻鬼的行為 ----------
def c_ghosts():
    g = M.Game(2)
    trails = [[] for _ in range(4)]
    for i in range(1800):                           # 30 秒，小精靈繞著吃豆子
        g.update(1 / 60, autopilot(g, "."))
        if i % 10 == 0:
            for k, p in enumerate(g.ghost_positions()):
                trails[k].append(tile(p))
        if g.over:
            break
    moved = [len(set(t)) for t in trails]
    assert min(moved) >= 8, f"有鬼幾乎沒有移動（走過的格子數 {moved}）"
    same = []
    for a in range(4):
        for b in range(a + 1, 4):
            n = sum(x == y for x, y in zip(trails[a], trails[b]))
            same.append(n / max(1, len(trails[a])))
    assert max(same) < 0.6, f"有兩隻鬼大部分時間重疊在一起（{max(same):.0%}），行為沒有區分"


guard("四隻鬼都會動、路線各不相同", c_ghosts)


def c_chase():
    """小精靈站著不動，鬼要能抓到牠；一直被抓到最後遊戲結束。"""
    g = M.Game(3)
    lives = g.lives
    lost_at = None
    for i in range(60 * 120):
        g.update(1 / 60, None)
        if g.lives < lives and lost_at is None:
            lost_at = i / 60
        if g.over:
            break
    assert lost_at is not None, "站著不動 2 分鐘都沒被鬼抓到"
    notes["被抓到"] = f"第 {lost_at:.0f} 秒第一次被抓"
    checks["命用完會遊戲結束"] = bool(g.over)


guard("鬼會追到小精靈", c_chase)
checks.setdefault("命用完會遊戲結束", False)


# ======================================================================== 規格書（SPEC.md）的額外要求
if len(sys.argv) > 2 and sys.argv[2] == "spec":
    def run_until(g, cond, secs, d=None):
        for i in range(int(secs * 60)):
            g.update(1 / 60, d(g) if callable(d) else d)
            if cond(g):
                return i / 60
        return None

    def c_no_dead_zone():
        g = M.Game(0)
        maze = g.maze
        h, w = len(maze), len(maze[0])
        st = tile(g.pacman)
        seen, q = {st}, deque([st])
        while q:
            x, y = q.popleft()
            for d in DIRS:
                n = ((x + d[0]) % w, y + d[1])
                if 0 <= n[1] < h and n not in seen and cell(maze, *n) != "#":
                    seen.add(n)
                    q.append(n)
        dead = [(x, y) for y, r in enumerate(maze) for x, c in enumerate(r) if c != "#" and (x, y) not in seen]
        assert not dead, f"有 {len(dead)} 格不是牆卻走不到（看起來是空地但進不去），例如 {dead[:3]}"

    def c_states():
        g = M.Game(0)
        eq0 = getattr(g, "state", None)
        assert eq0 == "ready", f"一開始 state 應該是 'ready'，實際 {eq0!r}"
        p0 = g.pacman
        t = run_until(g, lambda g: g.state == "playing", 6, (1, 0))
        assert t is not None, "6 秒內沒有從 ready 變成 playing"
        assert t >= 1.0, f"READY 只停了 {t:.1f} 秒（要至少 1 秒）"

    def c_death_sequence():
        g = M.Game(3)
        lives = g.lives
        t = run_until(g, lambda g: g.lives < lives or g.state == "dying", 120)
        assert t is not None, "2 分鐘都沒有被抓"
        if g.state != "dying":
            run_until(g, lambda g: g.state == "dying", 0.2)
        assert g.state == "dying", f"被抓之後 state 應該是 'dying'，實際 {g.state!r}"
        frozen, p = 0.0, g.pacman
        while g.state == "dying" and frozen < 10:
            g.update(1 / 60, (1, 0))
            frozen += 1 / 60
            assert g.state != "dying" or math.dist(g.pacman, p) < 0.01, "死亡動畫期間小精靈還在移動"
        assert frozen >= 1.0, f"死亡動畫只有 {frozen:.1f} 秒（要至少 1 秒）"
        if not g.over:
            assert g.state == "ready", f"死亡動畫後應該先回到 'ready'，實際 {g.state!r}"

    def c_eaten_path():
        """被吃掉的鬼要沿著迷宮走回鬼屋，不能飛、不能穿牆。"""
        for seed in range(6):
            g = M.Game(seed)
            for i in range(60 * 90):
                modes = g.ghost_modes()
                if "eaten" in modes:
                    k = modes.index("eaten")
                    prev = g.ghost_positions()[k]
                    for j in range(60 * 20):
                        g.update(1 / 60, None)
                        if g.ghost_modes()[k] != "eaten":
                            return
                        cur = g.ghost_positions()[k]
                        assert not overlaps(g.maze, cur, GS * 0.6, "#"), f"被吃掉的鬼穿過牆：{cur}"
                        assert math.dist(cur, prev) <= T * 0.75 or wrapped(cur, prev, g.maze), \
                            f"被吃掉的鬼一步移動 {math.dist(cur, prev):.0f} 像素（飛走或瞬移）"
                        prev = cur
                    raise AssertionError("被吃掉的鬼 20 秒內沒有回到鬼屋復活")
                fr = [tile(p) for p, m in zip(g.ghost_positions(), modes) if m == "frightened"]
                if fr:
                    d = path_dir(g.maze, tile(g.pacman), lambda x, y: (x, y) in fr)
                else:
                    d = autopilot(g, "o") if any("o" in r for r in g.maze) else autopilot(g, ".")
                g.update(1 / 60, d)
                if g.over:
                    break
        raise AssertionError("模擬中沒能吃到鬼（無法檢查回家路線）")

    def c_sounds():
        import pygame
        pygame.mixer.init()
        snd = M.make_sounds()
        need = {"waka", "power", "eat_ghost", "death", "start"}
        assert need <= set(snd), f"缺少音效：{need - set(snd)}"
        bad = [k for k in need if not hasattr(snd[k], "play") or snd[k].get_length() <= 0.02]
        assert not bad, f"音效是空的：{bad}"

    def c_level_clear():
        g = M.Game(0)
        run_until(g, lambda g: g.state == "playing", 6)
        lv = getattr(g, "level", None)
        assert lv == 1, f"一開始 level 應該是 1，實際 {lv!r}"
        # 只留一顆離小精靈最近的豆子
        target = None
        st = tile(g.pacman)
        d0 = path_dir(g.maze, st, lambda x, y: g.maze[y][x] in ".o")
        q, seen = deque([st]), {st}
        while q and not target:
            x, y = q.popleft()
            for d in DIRS:
                n = ((x + d[0]) % len(g.maze[0]), y + d[1])
                if 0 <= n[1] < len(g.maze) and n not in seen and cell(g.maze, *n) not in "#-":
                    seen.add(n)
                    q.append(n)
                    if g.maze[n[1]][n[0]] in ".o":
                        target = n
                        break
        g.maze[:] = [("".join(" " if (c in ".o" and (x, y) != target) else c for x, c in enumerate(r)))
                     for y, r in enumerate(g.maze)]
        assert g.pellets_left() == 1, f"留一顆豆子後 pellets_left() = {g.pellets_left()}（應該直接數 maze）"
        t = run_until(g, lambda g: g.state == "level_clear", 20, lambda g: autopilot(g, ".o"))
        assert t is not None, "吃完最後一顆豆子沒有進入 'level_clear'"
        t2 = run_until(g, lambda g: g.state in ("ready", "playing"), 8)
        assert t2 is not None and t2 >= 1.0, "過關畫面要停至少 1 秒再進下一關"
        assert g.level == 2 and g.pellets_left() > 50, f"下一關：level={g.level}，豆子 {g.pellets_left()}"

    for name, fn in [("規格：沒有走不進去的空地", c_no_dead_zone), ("規格：開場 READY 狀態", c_states),
                     ("規格：死亡動畫與重新開始", c_death_sequence), ("規格：被吃的鬼沿迷宮走回家", c_eaten_path),
                     ("規格：音效", c_sounds), ("規格：過關與下一關", c_level_clear)]:
        guard(name, fn)

report()
