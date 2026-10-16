"""文字 RPG 題的評分程式（模型看不到）：回歸測試（原本的行為不能壞）＋ 各題的新需求測試。

用法：python rpg.py <題目資料夾> [題目代號，例如 weight]
最後一行輸出 BENCH_RESULT {...}；checks 的名稱以「回歸：」或「新功能：」開頭。
"""

import json
import random
import sys
import traceback
from pathlib import Path

GAME = Path(sys.argv[1]).resolve()
TASK = sys.argv[2] if len(sys.argv) > 2 else ""
sys.path.insert(0, str(GAME))
FIX = Path(__file__).resolve().parent.parent / "seeds" / "rpg" / "tests" / "fixtures"   # 用原始的存檔，不用模型那份

checks, notes = {}, {}


def check(name):
    def deco(fn):
        try:
            fn()
            checks[name] = True
        except Exception as e:
            checks[name] = False
            notes[name] = f"{type(e).__name__}: {e}"[:300] or traceback.format_exc()[-300:]
        return fn
    return deco


def eq(a, b, what=""):
    if a != b:
        raise AssertionError(f"{what} 預期 {b!r}，實際 {a!r}")


def ok(cond, what):
    if not cond:
        raise AssertionError(what)


def raises(exc_name, fn, what):
    try:
        fn()
    except Exception as e:
        if type(e).__name__ == exc_name or any(c.__name__ == exc_name for c in type(e).__mro__):
            return
        raise AssertionError(f"{what}：預期 {exc_name}，實際 {type(e).__name__}: {e}")
    raise AssertionError(f"{what}：預期 {exc_name}，但沒有出錯")


try:
    from rpg import config
    from rpg.combat import Battle, damage, flee_chance
    from rpg.effects import Effect
    from rpg.events import EventBus
    from rpg.game import Game
    from rpg.stats import Modifier, Stats, apply_modifiers
    from rpg import save as savemod
except Exception as e:
    print("BENCH_RESULT " + json.dumps({"passed": 0, "total": 1, "checks": {"回歸：程式可以載入": False},
                                        "notes": {"錯誤": f"{type(e).__name__}: {e}"}}, ensure_ascii=False))
    sys.exit()


def G(seed=0, name="測試"):
    g = Game(seed)
    g.new_game(name)
    return g


# ======================================================================== 回歸：能力值
@check("回歸：加成先加固定值再乘百分比")
def _():
    s = apply_modifiers(Stats(20, 10, 4, 6, 1), [Modifier("atk", 5), Modifier("atk", pct=0.5), Modifier("defense", pct=-0.5),
                                                Modifier("max_hp", pct=0.25)])
    eq((s.atk, s.defense, s.max_hp, s.agi), (22, 2, 25, 6))


@check("回歸：能力值下限（max_hp 至少 1，其他至少 0）")
def _():
    s = apply_modifiers(Stats(5, 3, 3, 3, 3), [Modifier("max_hp", -50), Modifier("atk", -50), Modifier("luck", pct=-3)])
    eq((s.max_hp, s.atk, s.luck), (1, 0, 0))


@check("回歸：初始角色能力與金幣")
def _():
    g = G()
    s = g.player.stats()
    eq((s.max_hp, s.atk, s.defense, s.agi, s.luck), (40, 8, 3, 5, 2), "初始能力")
    eq((g.player.hp, g.player.inventory.gold, g.player.level), (40, 50, 1))


# ======================================================================== 回歸：背包
@check("回歸：堆疊先補滿舊格子")
def _():
    inv = G().player.inventory
    inv.add("potion", 7)
    inv.add("potion", 7)
    eq([list(s) for s in inv.slots], [["potion", 10], ["potion", 4]])


@check("回歸：背包滿時只放得下部分")
def _():
    inv = G().player.inventory
    for _ in range(19):
        inv.add("dagger")
    eq(inv.add("potion", 25), 10, "放進去的數量")
    eq(inv.count("potion"), 10)


@check("回歸：add_all 放不下就完全不放")
def _():
    inv = G().player.inventory
    for _ in range(20):
        inv.add("dagger")
    raises("InventoryError", lambda: inv.add_all("potion", 1), "add_all")
    eq(inv.count("potion"), 0)


@check("回歸：remove 從最後一格拿、空格會移除")
def _():
    inv = G().player.inventory
    inv.add("herb", 30)
    inv.add("herb", 5)
    inv.remove("herb", 6)
    eq([list(s) for s in inv.slots], [["herb", 29]])


@check("回歸：金幣不足丟 InventoryError、負數丟 ValueError")
def _():
    inv = G().player.inventory
    raises("InventoryError", lambda: inv.spend(999), "spend")
    raises("ValueError", lambda: inv.earn(-1), "earn")
    eq(inv.gold, 50)


@check("回歸：space_for 計算")
def _():
    inv = G().player.inventory
    inv.add("potion", 3)
    eq(inv.space_for("potion"), 7 + 19 * 10)


# ======================================================================== 回歸：裝備
@check("回歸：裝備加成與 HP 上限變化")
def _():
    g = G()
    p = g.player
    p.inventory.add("vitality_ring")
    p.equip("vitality_ring")
    eq(p.stats().max_hp, 48)
    eq(p.hp, 40, "裝上加 HP 的飾品不會自動補血")
    p.hp = 48
    p.unequip("accessory")
    eq(p.hp, 40, "卸下後 HP 降到新上限")


@check("回歸：背包滿時換裝備（拿出來的空位放換下的）、卸不下來要出錯")
def _():
    g = G()
    p = g.player
    p.inventory.add("wooden_sword")
    p.equip("wooden_sword")
    for _ in range(20):
        p.inventory.add("dagger")
    eq(p.equip("dagger"), "wooden_sword")
    eq((p.inventory.count("dagger"), p.inventory.count("wooden_sword")), (19, 1))
    p.inventory.add("herb")
    ok(p.inventory.free_slots() == 0, "背包應該是滿的")
    raises("InventoryError", lambda: p.unequip("weapon"), "滿了還卸裝")
    eq(p.equipment.equipped("weapon"), "dagger")


@check("回歸：不能裝備的東西丟 EquipError")
def _():
    g = G()
    g.player.inventory.add("potion")
    raises("EquipError", lambda: g.player.equip("potion"), "裝備藥水")
    raises("EquipError", lambda: g.player.equip("iron_sword"), "裝備背包沒有的")


@check("回歸：多件裝備加成疊加")
def _():
    g = G()
    p = g.player
    for i in ("great_axe", "chain_mail", "wind_boots"):
        p.inventory.add(i)
        p.equip(i)
    s = p.stats()
    eq((s.atk, s.defense, s.agi), (22, 10, int((5 - 3 - 1) * 1.3)))


# ======================================================================== 回歸：狀態效果
@check("回歸：同種效果取較長回合、較大強度")
def _():
    p = G().player
    p.effects.apply(Effect("poison", 2, 5))
    p.effects.apply(Effect("poison", 4, 2))
    e = p.effects.get("poison")
    eq((e.turns, e.power), (4, 5))


@check("回歸：護盾吸收後消失")
def _():
    p = G().player
    p.effects.apply(Effect("shield", 9, 10))
    eq(p.take_damage(6), 0)
    eq(p.take_damage(7), 3)
    ok(not p.effects.has("shield"), "護盾應該用完了")
    eq(p.hp, 37)


@check("回歸：加速與虛弱改變能力")
def _():
    p = G().player
    p.effects.apply(Effect("haste", 3, 50))
    p.effects.apply(Effect("weaken", 3, 50))
    eq((p.stats().agi, p.stats().atk), (7, 4))


@check("回歸：效果到期移除")
def _():
    p = G().player
    p.effects.apply(Effect("regen", 2, 5))
    p.hp = 20
    p.effects.tick(p)
    p.effects.tick(p)
    ok(not p.effects.has("regen"), "兩回合後應該到期")
    eq(p.hp, 30)


@check("回歸：使用消耗品")
def _():
    p = G().player
    p.inventory.add("potion", 2)
    p.inventory.add("antidote")
    p.hp = 5
    p.use_item("potion")
    eq(p.hp, 30)
    p.effects.apply(Effect("poison", 3, 2))
    p.use_item("antidote")
    ok(not p.effects.has("poison"), "解毒")
    eq(p.inventory.count("potion"), 1)


# ======================================================================== 回歸：戰鬥
@check("回歸：傷害公式（固定亂數）")
def _():
    class R:
        def uniform(self, a, b): return 0.0
        def random(self): return 0.99
    eq(damage(Stats(10, 20, 0, 0, 0), Stats(10, 0, 6, 0, 0), R()), (17, False))
    class C(R):
        def random(self): return 0.0
    eq(damage(Stats(10, 20, 0, 0, 0), Stats(10, 0, 6, 0, 0), C()), (25, True))


@check("回歸：行動順序依敏捷、同敏捷玩家先")
def _():
    g = G()
    b = g.start_battle("wolf", "slime", "goblin")
    eq([getattr(a, "id", "player") for a in b.turn_order()], ["wolf", "player", "goblin", "slime"])


@check("回歸：加速效果會改變下一輪順序")
def _():
    g = G()
    g.player.effects.apply(Effect("haste", 5, 100))
    b = g.start_battle("wolf")
    eq(b.turn_order()[0], g.player)


@check("回歸：逃跑機率")
def _():
    g = G()
    m = g.bestiary.spawn("wolf")
    eq(round(flee_chance(g.player.stats(), [m]), 4), round(0.4 + (5 - 7) * 0.02, 4))


@check("回歸：同一個種子戰鬥結果完全相同")
def _():
    def run():
        g = G(123)
        b = g.start_battle("goblin", "wolf")
        b.run_auto()
        return b.result, g.player.hp, b.round, g.player.inventory.items(), list(b.log)
    eq(run(), run())


@check("回歸：勝利獎勵（經驗、金幣、事件）")
def _():
    g = G(8)
    g.start_battle("slime", "slime").run_auto()
    eq(g.player.xp, 8)
    eq(g.player.inventory.gold, 56)
    eq(len(g.bus.of_type("monster_killed")), 2)
    eq(g.bus.of_type("battle_end")[-1]["result"], "win")


@check("回歸：中毒致死的怪物不會行動、也有獎勵")
def _():
    g = G(1)
    b = g.start_battle("slime")
    m = b.monsters[0]
    m.hp = 2
    m.effects.apply(Effect("poison", 3, 5))
    g.player.base = Stats(40, 0, 3, 0, 0)        # 玩家比較慢、攻擊 0：怪物先行動但先中毒死
    b.play_round(("attack", 0))
    eq(b.result, "win")
    eq(len(g.bus.of_type("attack")), 0, "死掉的怪物不應該攻擊")
    eq(g.player.xp, 4)


@check("回歸：背包滿時戰利品掉落（loot_lost）")
def _():
    g = G(0)
    for _ in range(20):
        g.player.inventory.add("dagger")
    found = False
    for s in range(40):
        g.rng.seed(s)
        g.player.hp = g.player.max_hp
        g.start_battle("slime").run_auto()
        if g.bus.of_type("loot_lost"):
            found = True
            break
    ok(found, "背包滿時應該發出 loot_lost")
    eq(g.player.inventory.count("slime_gel"), 0)


@check("回歸：升級補上增加的 HP 上限")
def _():
    p = G().player
    p.hp = 30
    eq(p.gain_xp(50), 2)
    eq((p.level, p.stats().max_hp, p.hp), (3, 56, 46))


@check("回歸：戰鬥結束移除護盾")
def _():
    g = G(2)
    g.player.effects.apply(Effect("shield", 99, 500))
    g.start_battle("slime").run_auto()
    ok(not g.player.effects.has("shield"), "護盾應該被移除")


@check("回歸：輸掉戰鬥")
def _():
    g = G(3)
    g.player.hp = 1
    g.player.base = Stats(40, 0, 0, 0, 0)
    eq(g.start_battle("ogre").run_auto(), "lose")


# ======================================================================== 回歸：事件、任務
@check("回歸：事件先進先出")
def _():
    bus, seen = EventBus(), []
    bus.subscribe("a", lambda t, d: (seen.append("a1"), bus.emit("b", n=1), bus.emit("c")))
    bus.subscribe("b", lambda t, d: (seen.append("b"), bus.emit("d")))
    bus.subscribe("a", lambda t, d: seen.append("a2"))
    bus.subscribe("c", lambda t, d: seen.append("c"))
    bus.subscribe("d", lambda t, d: seen.append("d"))
    bus.emit("a")
    eq(seen, ["a1", "a2", "b", "c", "d"])


@check("回歸：接任務前打的怪不算")
def _():
    g = G(4)
    g.start_battle("slime").run_auto()
    g.quests.accept("slime_cleanup")
    eq(g.quests.progress("slime_cleanup"), {"slime": 0})


@check("回歸：任務前置條件")
def _():
    g = G()
    raises("QuestError", lambda: g.quests.accept("wolf_hunt"), "還沒解鎖")
    g.quests.done.add("slime_cleanup")
    g.quests.accept("wolf_hunt")
    eq(g.quests.status("wolf_hunt"), "active")


@check("回歸：收集任務交出物品、拿到獎勵")
def _():
    g = G()
    p = g.player
    g.quests.accept("herbalist")
    p.inventory.add("herb", 7)
    g.quests.complete("herbalist", p)
    eq((p.inventory.count("herb"), p.inventory.count("regen_herb"), p.inventory.gold), (2, 3, 70))


@check("回歸：獎勵放不下就不能交任務（狀態不變）")
def _():
    g = G()
    p = g.player
    g.quests.done.add("slime_cleanup")
    g.quests.accept("wolf_hunt")
    g.quests.active["wolf_hunt"]["wolf"] = 2
    p.inventory.add("wolf_pelt", 2)
    for _ in range(19):
        p.inventory.add("dagger")
    # 狼皮佔 1 格，交出後空出 1 格放皮甲：放得下
    g.quests.complete("wolf_hunt", p)
    eq(p.inventory.count("leather_armor"), 1)
    g2 = G()
    p2 = g2.player
    g2.quests.accept("herbalist")
    p2.inventory.add("herb", 30)
    p2.inventory.add("herb", 6)
    for _ in range(18):
        p2.inventory.add("dagger")
    raises("QuestError", lambda: g2.quests.complete("herbalist", p2), "放不下獎勵")
    eq(p2.inventory.count("herb"), 36)
    eq(g2.quests.status("herbalist"), "active")


@check("回歸：完整任務鏈（打怪事件 → 進度 → 完成）")
def _():
    g = G(11)
    g.quests.accept("slime_cleanup")
    n = 0
    while g.quests.progress("slime_cleanup")["slime"] < 3 and n < 20:
        g.rest() if g.player.inventory.gold >= 10 else g.player.inventory.earn(10)
        g.start_battle("slime").run_auto()
        n += 1
    g.quests.complete("slime_cleanup", g.player)
    eq(g.quests.status("wolf_hunt"), "available")
    eq(len(g.bus.of_type("quest_progress")), 3)


# ======================================================================== 回歸：商店
@check("回歸：買賣價格")
def _():
    g = G()
    eq((g.shop.buy_price("iron_sword"), g.shop.sell_price("iron_sword"), g.shop.sell_price("herb")), (80, 40, 1))
    g.shop.price_mult = 1.15
    eq(g.shop.buy_price("potion"), 12)


@check("回歸：買東西失敗時金幣、背包不變")
def _():
    g = G()
    p = g.player
    for _ in range(20):
        p.inventory.add("dagger")
    raises("ShopError", lambda: g.shop.buy(p, "potion"), "背包滿")
    eq(p.inventory.gold, 50)
    raises("ShopError", lambda: g.shop.buy(p, "great_axe"), "沒賣")


@check("回歸：任務物品不能賣、賣東西拿到錢")
def _():
    g = G()
    p = g.player
    p.inventory.add("old_letter")
    raises("ShopError", lambda: g.shop.sell(p, "old_letter"), "賣任務物品")
    p.inventory.add("wolf_pelt", 3)
    eq(g.shop.sell(p, "wolf_pelt", 3), 18)
    eq(p.inventory.gold, 68)


@check("回歸：買東西發出事件")
def _():
    g = G()
    g.shop.buy(g.player, "potion", 3)
    eq(g.bus.of_type("bought")[-1], {"item": "potion", "count": 3, "cost": 30})


# ======================================================================== 回歸：存檔
@check("回歸：讀版本 1 存檔")
def _():
    g = Game()
    g.load(FIX / "save_v1.json")
    p = g.player
    eq((p.name, p.level, p.hp, p.inventory.gold), ("老玩家", 3, 41, 132))
    eq(p.equipment.to_dict(), {"weapon": "iron_sword", "armor": "leather_armor", "accessory": None})
    eq(p.stats().atk, 19)
    eq(p.inventory.items(), {"potion": 4, "wolf_pelt": 3, "herb": 2})


@check("回歸：讀版本 2 存檔")
def _():
    g = Game()
    g.load(FIX / "save_v2.json")
    p = g.player
    eq((p.name, p.xp, p.equipment.equipped("weapon"), p.inventory.count("potion")), ("存檔測試", 4, "dagger", 3))
    eq(p.effects.get("regen").power, 4)
    eq(g.quests.progress("slime_cleanup"), {"slime": 1})


@check("回歸：存檔再讀回來完全一樣（含亂數狀態）")
def _():
    g = G(21)
    p = g.player
    p.inventory.add("dagger")
    p.equip("dagger")
    p.inventory.add("potion", 4)
    g.quests.accept("slime_cleanup")
    g.start_battle("slime").run_auto()
    p.effects.apply(Effect("haste", 2, 50))
    d = savemod.game_to_dict(g)
    g2 = Game(999)
    g2.load_dict(json.loads(json.dumps(d)))
    eq(savemod.game_to_dict(g2), d)
    eq(g2.rng.random(), g.rng.random(), "亂數狀態")


@check("回歸：讀檔後打怪任務進度照常增加、商店事件照常記錄")
def _():
    g = G(31)
    g.quests.accept("slime_cleanup")
    d = savemod.game_to_dict(g)
    g.load_dict(json.loads(json.dumps(d)))
    for _ in range(2):
        g.player.hp = g.player.max_hp
        g.start_battle("slime").run_auto()
    eq(g.quests.progress("slime_cleanup"), {"slime": 2})
    g.shop.buy(g.player, "potion")
    eq(g.bus.of_type("bought")[-1]["item"], "potion")


@check("回歸：比遊戲新的存檔要拒絕")
def _():
    raises("SaveError", lambda: Game().load_dict({"version": 99, "player": {}}), "新版本存檔")


@check("回歸：旅店休息")
def _():
    g = G()
    g.player.hp = 3
    g.player.effects.apply(Effect("poison", 3, 2))
    g.rest()
    eq((g.player.hp, g.player.inventory.gold, g.player.effects.active), (40, 40, {}))


# ======================================================================== 新功能：負重（weight）
if TASK == "weight":
    W = {"weapon": 6, "armor": 10, "accessory": 1, "consumable": 0.5, "material": 1, "quest": 0}

    def wt(item):
        return 18 if item.id == "plate_armor" else W[item.kind]

    @check("新功能：每個物品都有正確的重量")
    def _():
        g = G()
        bad = [i.id for i in g.db.all() if abs(float(i.weight) - wt(i)) > 1e-9]
        ok(not bad, f"重量不對：{bad[:5]}")

    @check("新功能：負重上限 = 20 + 目前攻擊力")
    def _():
        g = G()
        p = g.player
        eq(p.carry_capacity(), 28)
        p.inventory.add("iron_sword")
        p.equip("iron_sword")
        eq(p.carry_capacity(), 35, "裝備加攻擊")
        p.effects.apply(Effect("weaken", 3, 50))
        eq(p.carry_capacity(), 20 + int((8 + 7) * 0.5), "虛弱效果")
        p.gain_xp(20)
        eq(p.carry_capacity(), 20 + int((10 + 7) * 0.5), "升級")

    @check("新功能：目前重量含背包與裝備")
    def _():
        g = G()
        p = g.player
        p.inventory.add("potion", 4)
        p.inventory.add("chain_mail")
        p.inventory.add("herb", 3)
        eq(float(p.carried_weight()), 2 + 10 + 3)
        p.equip("chain_mail")
        eq(float(p.carried_weight()), 15, "裝備後重量不變")

    @check("新功能：超重時敏捷減半（無條件捨去）、不超重不影響")
    def _():
        g = G()
        p = g.player
        p.inventory.add("chain_mail", 2)
        p.inventory.add("iron_sword")
        eq(p.overweight, False, "26 / 28 不算超重")
        eq(p.stats().agi, 5)
        p.inventory.add("potion", 5)
        eq(p.overweight, True, "28.5 / 28")
        eq(p.stats().agi, 2)
        eq(p.carry_capacity(), 28, "超重不影響負重上限")
        p.inventory.add("wind_boots")
        p.equip("wind_boots")
        eq(p.stats().agi, int(int(5 * 1.3) / 2), "先套其他加成再減半")
        ok(p.overweight, "疾風靴 1kg")

    @check("新功能：剛好等於上限不算超重")
    def _():
        g = G()
        p = g.player
        p.inventory.add("chain_mail", 2)
        p.inventory.add("iron_sword")
        p.inventory.add("herb", 2)
        eq(float(p.carried_weight()), 28)
        eq(p.overweight, False)

    @check("新功能：超重影響戰鬥順序與逃跑")
    def _():
        g = G()
        p = g.player
        p.inventory.add("plate_armor")
        p.inventory.add("chain_mail")
        p.inventory.add("herb", 1)
        ok(p.overweight, "29 / 28")
        b = g.start_battle("goblin")
        eq(b.turn_order()[0].name, "哥布林", "超重後敏捷 2 < 哥布林 5")
        m = b.monsters[0]
        eq(round(flee_chance(p.stats(), [m]), 4), round(0.4 + (2 - 5) * 0.02, 4))

    @check("新功能：買了會超重就不能買（金幣、背包不變）")
    def _():
        g = G()
        p = g.player
        p.inventory.earn(500)
        p.inventory.add("chain_mail", 2)
        raises("ShopError", lambda: g.shop.buy(p, "chain_mail"), "買了會超重（30 > 28）")
        eq((p.inventory.gold, p.inventory.count("chain_mail")), (550, 2))
        g.shop.buy(p, "potion", 4)
        eq(p.inventory.count("potion"), 4, "買了剛好 22 還沒超重")

    @check("新功能：戰利品與任務獎勵可以超重")
    def _():
        g = G()
        p = g.player
        p.inventory.add("chain_mail", 2)
        p.inventory.add("iron_sword")
        p.inventory.add("herb", 2)
        g.quests.accept("herbalist")
        p.inventory.add("herb", 5)
        g.quests.complete("herbalist", p)
        eq(p.inventory.count("regen_herb"), 3)
        ok(p.overweight, "28 − 5 + 5 + 1.5 = 29.5 > 28")

    @check("新功能：存檔版本 3，舊存檔可讀")
    def _():
        eq(config.SAVE_VERSION, 3)
        g = Game()
        g.load(FIX / "save_v1.json")
        eq(float(g.player.carried_weight()), 2 + 3 + 2 + 6 + 10)
        g.load(FIX / "save_v2.json")
        eq(float(g.player.carried_weight()), 1.5 + 6)
        d = savemod.game_to_dict(g)
        eq(d["version"], 3)
        g2 = Game()
        g2.load_dict(d)
        eq(savemod.game_to_dict(g2), d)

    @check("新功能：狀態畫面顯示負重")
    def _():
        import contextlib
        import io
        from rpg import cli
        g = G()
        g.player.inventory.add("iron_sword")
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            cli.show_status(g)
        ok("6/28" in buf.getvalue().replace(" ", "").replace(".0", ""), f"沒看到 6/28：{buf.getvalue()[:200]}")


# ======================================================================== 新功能：負重（模糊版，只檢查合理行為，數值由模型決定）
if TASK == "weight_vague":
    def heavy_load(p, extra=0.0):
        """一直放鐵劍直到超重（最多 40 把），回傳是否成功超重。"""
        for _ in range(40):
            if p.overweight:
                return True
            p.inventory.add("iron_sword")
        return p.overweight

    @check("新功能：物品有重量（任務物品以外都 > 0，裝備比藥水重）")
    def _():
        g = G()
        bad = [i.id for i in g.db.all() if i.kind != "quest" and not float(i.weight) > 0]
        ok(not bad, f"沒有重量：{bad[:5]}")
        ok(g.db["plate_armor"].weight > g.db["potion"].weight, "板甲應該比藥水重")

    @check("新功能：目前重量 = 背包 + 裝備（裝上、卸下不變）")
    def _():
        g = G()
        p, db = g.player, g.db
        p.inventory.add("potion", 3)
        p.inventory.add("chain_mail")
        exp = 3 * db["potion"].weight + db["chain_mail"].weight
        ok(abs(float(p.carried_weight()) - exp) < 1e-6, f"{p.carried_weight()} ≠ {exp}")
        p.equip("chain_mail")
        ok(abs(float(p.carried_weight()) - exp) < 1e-6, "裝備後重量變了")

    @check("新功能：初始角色不超重，帶太多東西會超重")
    def _():
        g = G()
        ok(not g.player.overweight, "什麼都沒帶就超重")
        ok(heavy_load(g.player), "放了 40 把鐵劍還不超重")

    @check("新功能：超重時變慢（敏捷下降，戰鬥順序跟著變）")
    def _():
        g = G()
        p = g.player
        agi = p.stats().agi
        heavy_load(p)
        ok(p.stats().agi < agi, f"超重後敏捷沒有下降（{agi} → {p.stats().agi}）")
        b = g.start_battle("goblin")                # 哥布林敏捷 5 = 玩家，平常玩家先
        eq(b.turn_order()[0].name, "哥布林", "超重後應該比哥布林慢")

    @check("新功能：商店不會讓人買到超重（失敗時錢和背包不變）")
    def _():
        g = G()
        p = g.player
        p.inventory.earn(5000)
        while not p.overweight:
            before = (p.inventory.gold, p.inventory.items())
            try:
                g.shop.buy(p, "chain_mail")
            except Exception as e:
                ok(type(e).__name__ == "ShopError", f"應該丟 ShopError，實際 {type(e).__name__}")
                eq((p.inventory.gold, p.inventory.items()), before, "失敗時狀態要不變")
                break
        ok(not p.overweight, "透過商店買到了超重")
        g.shop.buy(p, "potion") if p.carried_weight() + g.db["potion"].weight <= p.carry_capacity() else None

    @check("新功能：存檔讀檔保留一切，舊存檔還能讀")
    def _():
        g = G(3)
        heavy_load(g.player)
        d = savemod.game_to_dict(g)
        g2 = Game()
        g2.load_dict(json.loads(json.dumps(d)))
        eq(savemod.game_to_dict(g2), d)
        eq(g2.player.overweight, True)
        g3 = Game()
        g3.load(FIX / "save_v1.json")
        ok(g3.player.carried_weight() > 0, "舊存檔的重量")

    @check("新功能：狀態畫面顯示負重")
    def _():
        import contextlib
        import io
        from rpg import cli
        g = G()
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            cli.show_status(g)
        out = buf.getvalue()
        ok(str(int(g.player.carry_capacity())) in out and ("負重" in out or "重量" in out), f"沒看到負重：{out[:200]}")


# ======================================================================== 修 bug 題：埋的 5 個 bug 對應的檢查改名為「新功能」
if TASK == "bugs":
    for old, new in {"回歸：讀版本 1 存檔": "新功能：舊存檔裝備不見（save 轉換）",
                     "回歸：裝備加成與 HP 上限變化": "新功能：卸下裝備 HP 超過上限",
                     "回歸：加速效果會改變下一輪順序": "新功能：加速、裝備不影響行動順序",
                     "回歸：讀檔後打怪任務進度照常增加、商店事件照常記錄": "新功能：讀檔後任務進度不動（事件系統）",
                     "回歸：買東西失敗時金幣、背包不變": "新功能：背包滿時買東西被扣錢"}.items():
        checks[new] = checks.pop(old)
        if old in notes:
            notes[new] = notes.pop(old)

reg = [k for k in checks if k.startswith("回歸")]
new = [k for k in checks if k.startswith("新功能")]
print("BENCH_RESULT " + json.dumps({
    "passed": sum(checks.values()), "total": len(checks),
    "regression": f"{sum(checks[k] for k in reg)}/{len(reg)}", "feature": f"{sum(checks[k] for k in new)}/{len(new)}",
    "checks": checks, "notes": notes}, ensure_ascii=False))
