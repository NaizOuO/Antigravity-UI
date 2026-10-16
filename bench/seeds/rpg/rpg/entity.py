"""角色：玩家與怪物共用的部分（能力值、HP、狀態效果），玩家另外有背包、裝備、等級。"""

from . import config
from .effects import Effects
from .equipment import Equipment
from .inventory import Inventory
from .stats import Stats, apply_modifiers


class Character:
    def __init__(self, name, base):
        self.name = name
        self.base = base
        self.effects = Effects()
        self.hp = self.stats().max_hp

    def modifiers(self):
        return self.effects.modifiers()

    def stats(self):
        """目前的能力值：基礎值套用所有加成（裝備、狀態效果）。"""
        return apply_modifiers(self.base, self.modifiers())

    @property
    def max_hp(self):
        return self.stats().max_hp

    @property
    def alive(self):
        return self.hp > 0

    def clamp_hp(self):
        self.hp = max(0, min(self.hp, self.max_hp))

    def take_damage(self, amount):
        """受到攻擊：先被護盾吸收，回傳實際扣掉的 HP。"""
        amount = self.effects.absorb(max(0, int(amount)))
        return self.lose_hp(amount)

    def lose_hp(self, amount):
        before = self.hp
        self.hp = max(0, self.hp - int(amount))
        return before - self.hp

    def heal(self, amount):
        before = self.hp
        self.hp = min(self.max_hp, self.hp + int(amount))
        return self.hp - before


class Player(Character):
    def __init__(self, name, base, db):
        self.db = db
        self.inventory = Inventory(db)
        self.equipment = Equipment(db)
        self.level = 1
        self.xp = 0
        super().__init__(name, base)

    def modifiers(self):
        return self.equipment.modifiers() + self.effects.modifiers()

    def equip(self, item_id):
        old = self.equipment.equip(self.inventory, item_id)
        self.clamp_hp()                         # 換掉加 HP 的裝備時，HP 不能超過新的上限
        return old

    def unequip(self, slot):
        out = self.equipment.unequip(self.inventory, slot)
        self.clamp_hp()
        return out

    def gain_xp(self, amount):
        """加經驗，回傳升了幾級。升級時 HP 上限增加的部分也會補到目前 HP。"""
        self.xp += amount
        gained = 0
        while self.level < len(config.LEVEL_XP) and self.xp >= config.LEVEL_XP[self.level]:
            before = self.max_hp
            self.level += 1
            gained += 1
            g = config.LEVEL_UP_GAIN
            self.base = self.base + Stats(g["max_hp"], g["atk"], g["defense"], g["agi"], g["luck"])
            self.hp += self.max_hp - before
        return gained

    def use_item(self, item_id):
        """使用消耗品，回傳說明文字。"""
        from .effects import Effect
        item = self.db[item_id]
        if item.kind != "consumable":
            raise ValueError(f"{item.name} 不能使用")
        self.inventory.remove(item_id)
        if "heal" in item.use:
            return f"回復了 {self.heal(item.use['heal'])} HP"
        if "effect" in item.use:
            self.effects.apply(Effect(item.use["effect"], item.use.get("turns", 3), item.use.get("power", 0)))
            return f"獲得效果：{item.use['effect']}"
        if "cure" in item.use:
            self.effects.remove(item.use["cure"])
            return f"解除了 {item.use['cure']}"
        return "沒有效果"


class Monster(Character):
    def __init__(self, mid, name, base, xp=0, gold=0, drops=(), skills=()):
        self.id = mid
        self.xp_reward = xp
        self.gold_reward = gold
        self.drops = list(drops)          # [(item_id, 機率), ...]
        self.skills = list(skills)        # [{"effect": "poison", "turns": 3, "power": 2, "chance": 0.3}, ...]
        super().__init__(name, base)


def new_player(name, db):
    p = Player(name, Stats(max_hp=40, atk=8, defense=3, agi=5, luck=2), db)
    p.inventory.earn(config.START_GOLD)
    return p
