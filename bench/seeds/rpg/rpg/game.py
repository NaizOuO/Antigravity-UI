"""遊戲本體：把各模組接在一起。介面（cli.py）和測試都透過這裡操作。"""

import random
from pathlib import Path

from . import save as savemod
from .combat import Battle
from .entity import new_player
from .events import EventBus
from .items import ItemDB
from .monsters import Bestiary
from .quests import QuestLog, load_quests
from .shop import Shop

DATA = Path(__file__).resolve().parent.parent / "data"

SHOP_STOCK = ["potion", "hi_potion", "antidote", "haste_tonic", "wooden_sword", "iron_sword",
              "leather_armor", "chain_mail", "lucky_charm"]


class Game:
    def __init__(self, seed=0, data_dir=DATA):
        self.data_dir = Path(data_dir)
        self.db = ItemDB.load(self.data_dir / "items.json")
        self.bestiary = Bestiary.load(self.data_dir / "monsters.json")
        self.quest_defs = load_quests(self.data_dir / "quests.json")
        self.rng = random.Random(seed)
        self.bus = EventBus()
        self.player = None
        self.quests = QuestLog(self.quest_defs, self.bus)
        self.shop = Shop(self.db, SHOP_STOCK, 1.0, self.bus)
        self.battle = None

    def new_game(self, name="勇者"):
        self.player = new_player(name, self.db)
        return self.player

    def start_battle(self, *monster_ids):
        if self.battle and not self.battle.over:
            raise RuntimeError("戰鬥還沒結束")
        self.battle = Battle(self.player, [self.bestiary.spawn(m) for m in monster_ids], self.rng, self.bus)
        return self.battle

    def rest(self):
        """在旅店休息：HP 回滿、解除所有狀態效果，花 10 金幣。"""
        self.player.inventory.spend(10)
        self.player.effects.clear()
        self.player.hp = self.player.max_hp

    # ---- 存檔
    def save(self, path):
        savemod.save_game(self, path)

    def load(self, path):
        self.load_dict(savemod.load_data(path))

    def load_dict(self, data):
        data = savemod.migrate(data, self.db)
        self.player = savemod.player_from_dict(data["player"], self.db)
        self.quests.load(data.get("quests", {}))
        savemod.rng_load(self.rng, data.get("rng_state"))
        self.battle = None
