import math
import random
import sys

import pygame

TILE = 32
PLAYER_SIZE = 24
ENEMY_SIZE = 24
W, H = 40, 28
PLAYER_SPEED = 160
ENEMY_SPEED = 90


def generate_level(seed):
    rnd = random.Random(seed)
    grid = [["#"] * W for _ in range(H)]
    rooms = []
    for _ in range(40):
        w, h = rnd.randint(4, 8), rnd.randint(4, 7)
        x, y = rnd.randint(1, W - w - 2), rnd.randint(1, H - h - 2)
        if any(x < rx + rw + 1 and rx < x + w + 1 and y < ry + rh + 1 and ry < y + h + 1 for rx, ry, rw, rh in rooms):
            continue
        rooms.append((x, y, w, h))
        for yy in range(y, y + h):
            for xx in range(x, x + w):
                grid[yy][xx] = "."
        if len(rooms) >= 7:
            break
    centers = [(x + w // 2, y + h // 2) for x, y, w, h in rooms]
    for i in range(1, len(centers)):
        if rnd.random() < 0.8:
            (x1, y1), (x2, y2) = centers[i - 1], centers[i]
            for xx in range(min(x1, x2), max(x1, x2) + 1):
                grid[y1][xx] = "."
            for yy in range(min(y1, y2), max(y1, y2) + 1):
                grid[yy][x2] = "."
    # 出口：最後一個房間右邊蓋一個小房間，只有一道門
    ex, ey, ew, eh = rooms[-1]
    cx, cy = ex + ew // 2, ey + eh // 2
    for yy in range(cy - 1, cy + 2):
        for xx in range(cx - 1, cx + 2):
            grid[yy][xx] = "#"
    grid[cy][cx] = "E"
    grid[cy + 1][cx] = "D"
    grid[cy - 1][cx + 1] = "."
    kx, ky = centers[len(centers) // 2]
    start = centers[0]
    enemies = [c for c in centers[1:-1]][:4]
    return {"grid": ["".join(r) for r in grid], "start": start, "key": (kx, ky), "enemies": enemies}


def tile_at(level, px, py):
    gx, gy = int(px // TILE), int(py // TILE)
    if 0 <= gy < len(level["grid"]) and 0 <= gx < len(level["grid"][gy]):
        return level["grid"][gy][gx]
    return "#"


def step_player(level, pos, direction, dt, has_key):
    nx = pos[0] + direction[0] * PLAYER_SPEED * dt
    ny = pos[1] + direction[1] * PLAYER_SPEED * dt
    t = tile_at(level, nx, ny)
    if t == "#" or (t == "D" and not has_key):
        return pos
    return (nx, ny)


def step_enemy(level, pos, player_pos, dt):
    dx, dy = player_pos[0] - pos[0], player_pos[1] - pos[1]
    d = math.hypot(dx, dy) or 1
    return (pos[0] + dx / d * ENEMY_SPEED * dt, pos[1] + dy / d * ENEMY_SPEED * dt)


def main():
    pygame.init()
    screen = pygame.display.set_mode((W * TILE // 2 + 200, H * TILE // 2))
    pygame.display.set_caption("地牢")
    clock = pygame.time.Clock()
    level = generate_level(random.randrange(10 ** 6))
    to_px = lambda c: ((c[0] + .5) * TILE, (c[1] + .5) * TILE)
    player, key, has_key = to_px(level["start"]), to_px(level["key"]), False
    enemies = [to_px(e) for e in level["enemies"]]
    hp, font = 100, pygame.font.Font(None, 28)
    while True:
        dt = clock.tick(60) / 1000
        for ev in pygame.event.get():
            if ev.type == pygame.QUIT:
                pygame.quit(); sys.exit()
        k = pygame.key.get_pressed()
        d = (k[pygame.K_RIGHT] - k[pygame.K_LEFT], k[pygame.K_DOWN] - k[pygame.K_UP])
        player = step_player(level, player, d, dt, has_key)
        enemies = [step_enemy(level, e, player, dt) for e in enemies]
        if not has_key and math.dist(player, key) < TILE * 0.6:
            has_key = True
        for e in enemies:
            if math.dist(e, player) < (PLAYER_SIZE + ENEMY_SIZE) / 2:
                hp -= 30 * dt
        if tile_at(level, *player) == "E" or hp <= 0:
            pygame.quit(); sys.exit()
        cam = (player[0] - screen.get_width() / 2, player[1] - screen.get_height() / 2)
        screen.fill((15, 15, 20))
        colors = {"#": (70, 70, 90), ".": (35, 35, 45), "D": (160, 110, 40), "E": (60, 200, 120)}
        for y, row in enumerate(level["grid"]):
            for x, c in enumerate(row):
                screen.fill(colors.get(c, (35, 35, 45)), (x * TILE - cam[0], y * TILE - cam[1], TILE, TILE))
        if not has_key:
            pygame.draw.circle(screen, (240, 210, 60), (key[0] - cam[0], key[1] - cam[1]), 8)
        for e in enemies:
            pygame.draw.rect(screen, (210, 60, 60), (e[0] - ENEMY_SIZE / 2 - cam[0], e[1] - ENEMY_SIZE / 2 - cam[1], ENEMY_SIZE, ENEMY_SIZE))
        pygame.draw.rect(screen, (90, 160, 255), (player[0] - PLAYER_SIZE / 2 - cam[0], player[1] - PLAYER_SIZE / 2 - cam[1], PLAYER_SIZE, PLAYER_SIZE))
        screen.blit(font.render(f"HP {int(hp)}  key {'yes' if has_key else 'no'}", True, (230, 230, 230)), (10, 10))
        pygame.display.flip()


if __name__ == "__main__":
    main()
