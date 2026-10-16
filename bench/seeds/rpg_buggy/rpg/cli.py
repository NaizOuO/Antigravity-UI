"""文字介面：python -m rpg.cli"""

from .game import Game


def show_status(g):
    p = g.player
    s = p.stats()
    print(f"{p.name} Lv{p.level}  HP {p.hp}/{s.max_hp}  攻 {s.atk} 防 {s.defense} 敏 {s.agi} 運 {s.luck}  金幣 {p.inventory.gold}")
    eq = "、".join(g.db[i].name for i in p.equipment.items()) or "無"
    print(f"裝備：{eq}")
    print("背包：" + ("、".join(f"{g.db[i].name}×{n}" for i, n in p.inventory.items().items()) or "空"))
    if p.effects.active:
        print("狀態：" + "、".join(f"{k}({e.turns})" for k, e in p.effects.active.items()))


def battle_loop(g, *mids):
    b = g.start_battle(*mids)
    while not b.over:
        for i, m in enumerate(b.monsters):
            if m.alive:
                print(f"  [{i}] {m.name} HP {m.hp}")
        cmd = input("a 攻擊 <編號> / i <物品> / f 逃跑 > ").split()
        if not cmd:
            continue
        act = {"a": lambda: ("attack", int(cmd[1]) if len(cmd) > 1 else 0),
               "i": lambda: ("item", cmd[1]), "f": lambda: ("flee",)}.get(cmd[0])
        if not act:
            continue
        try:
            for line in b.play_round(act()):
                print(line)
        except Exception as e:
            print("無法執行：", e)
    print("結果：", b.result)


def main():
    g = Game()
    g.new_game(input("名字：") or "勇者")
    while True:
        show_status(g)
        cmd = input("指令（fight <怪物…> / buy <物品> / sell <物品> / equip <物品> / use <物品> / quest <id> / done <id> / rest / save / load / quit）> ").split()
        if not cmd:
            continue
        try:
            c, args = cmd[0], cmd[1:]
            if c == "quit":
                break
            elif c == "fight":
                battle_loop(g, *(args or ["slime"]))
            elif c == "buy":
                g.shop.buy(g.player, args[0], int(args[1]) if len(args) > 1 else 1)
            elif c == "sell":
                g.shop.sell(g.player, args[0], int(args[1]) if len(args) > 1 else 1)
            elif c == "equip":
                g.player.equip(args[0])
            elif c == "use":
                print(g.player.use_item(args[0]))
            elif c == "quest":
                g.quests.accept(args[0])
            elif c == "done":
                g.quests.complete(args[0], g.player)
            elif c == "rest":
                g.rest()
            elif c == "save":
                g.save("save.json")
            elif c == "load":
                g.load("save.json")
        except Exception as e:
            print("無法執行：", e)


if __name__ == "__main__":
    main()
