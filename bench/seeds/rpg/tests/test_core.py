"""專案自己的測試：python -m unittest discover tests"""

import random
import unittest
from pathlib import Path

from rpg.combat import Battle, damage
from rpg.effects import Effect
from rpg.events import EventBus
from rpg.game import Game
from rpg.inventory import InventoryError
from rpg.shop import ShopError
from rpg.stats import Modifier, Stats, apply_modifiers

FIX = Path(__file__).parent / "fixtures"


def game(seed=0):
    g = Game(seed)
    g.new_game("測試")
    return g


class TestStats(unittest.TestCase):
    def test_flat_then_pct(self):
        s = apply_modifiers(Stats(10, 10, 0, 0, 0), [Modifier("atk", 5), Modifier("atk", pct=0.2), Modifier("atk", pct=-0.1)])
        self.assertEqual(s.atk, 16)

    def test_floor_at_zero(self):
        s = apply_modifiers(Stats(10, 2, 0, 1, 0), [Modifier("agi", -5)])
        self.assertEqual(s.agi, 0)


class TestInventory(unittest.TestCase):
    def test_stacking(self):
        g = game()
        inv = g.player.inventory
        self.assertEqual(inv.add("potion", 15), 15)
        self.assertEqual(len(inv.slots), 2)
        self.assertEqual(inv.count("potion"), 15)

    def test_full(self):
        g = game()
        inv = g.player.inventory
        for _ in range(inv.max_slots):
            inv.add("iron_sword")
        self.assertEqual(inv.add("wooden_sword"), 0)

    def test_remove_too_many(self):
        g = game()
        g.player.inventory.add("herb", 2)
        with self.assertRaises(InventoryError):
            g.player.inventory.remove("herb", 3)


class TestEquipment(unittest.TestCase):
    def test_equip_bonus(self):
        g = game()
        g.player.inventory.add("iron_sword")
        atk = g.player.stats().atk
        g.player.equip("iron_sword")
        self.assertEqual(g.player.stats().atk, atk + 7)
        self.assertEqual(g.player.inventory.count("iron_sword"), 0)

    def test_swap_returns_old(self):
        g = game()
        g.player.inventory.add("iron_sword")
        g.player.inventory.add("wooden_sword")
        g.player.equip("wooden_sword")
        self.assertEqual(g.player.equip("iron_sword"), "wooden_sword")
        self.assertEqual(g.player.inventory.count("wooden_sword"), 1)


class TestCombat(unittest.TestCase):
    def test_min_damage(self):
        d, _ = damage(Stats(10, 1, 0, 0, 0), Stats(10, 0, 50, 0, 0), random.Random(1))
        self.assertGreaterEqual(d, 1)

    def test_win_gives_rewards(self):
        g = game(3)
        gold = g.player.inventory.gold
        self.assertEqual(g.start_battle("slime").run_auto(), "win")
        self.assertEqual(g.player.inventory.gold, gold + 3)
        self.assertEqual(g.player.xp, 4)

    def test_poison_ticks(self):
        g = game(5)
        g.player.effects.apply(Effect("poison", 2, 3))
        hp = g.player.hp
        g.start_battle("slime").play_round(("attack", 0))
        self.assertLessEqual(g.player.hp, hp - 3)


class TestQuests(unittest.TestCase):
    def test_kill_quest(self):
        g = game(2)
        g.quests.accept("slime_cleanup")
        for _ in range(3):
            g.start_battle("slime").run_auto()
            g.rest()
        self.assertTrue(g.quests.can_complete("slime_cleanup", g.player))
        g.quests.complete("slime_cleanup", g.player)
        self.assertEqual(g.quests.status("slime_cleanup"), "done")

    def test_locked(self):
        g = game()
        self.assertEqual(g.quests.status("wolf_hunt"), "locked")


class TestShop(unittest.TestCase):
    def test_buy_sell(self):
        g = game()
        g.shop.buy(g.player, "potion", 2)
        self.assertEqual(g.player.inventory.gold, 30)
        self.assertEqual(g.shop.sell(g.player, "potion", 1), 5)

    def test_not_enough_gold(self):
        g = game()
        with self.assertRaises(ShopError):
            g.shop.buy(g.player, "chain_mail")


class TestSave(unittest.TestCase):
    def test_roundtrip(self):
        g = game(4)
        g.player.inventory.add("potion", 3)
        g.start_battle("wolf").run_auto()
        data = __import__("rpg.save", fromlist=["x"]).game_to_dict(g)
        g2 = Game(99)
        g2.load_dict(data)
        self.assertEqual(g2.player.inventory.items(), g.player.inventory.items())
        self.assertEqual(g2.player.hp, g.player.hp)

    def test_load_v1(self):
        g = Game()
        g.load(FIX / "save_v1.json")
        self.assertEqual(g.player.equipment.equipped("weapon"), "iron_sword")


class TestEvents(unittest.TestCase):
    def test_fifo(self):
        bus, seen = EventBus(), []
        bus.subscribe("a", lambda t, d: (seen.append("a"), bus.emit("b")))
        bus.subscribe("b", lambda t, d: seen.append("b"))
        bus.subscribe("a", lambda t, d: seen.append("a2"))
        bus.emit("a")
        self.assertEqual(seen, ["a", "a2", "b"])


if __name__ == "__main__":
    unittest.main()
