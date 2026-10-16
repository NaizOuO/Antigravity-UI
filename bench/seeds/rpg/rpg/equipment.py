"""裝備欄：武器、防具、飾品各一格。裝備會從背包拿出來，卸下會放回背包。"""

from .inventory import InventoryError

SLOTS = ("weapon", "armor", "accessory")


class EquipError(Exception):
    pass


class Equipment:
    def __init__(self, db):
        self.db = db
        self.slots = {s: None for s in SLOTS}

    def equipped(self, slot):
        return self.slots.get(slot)

    def is_equipped(self, item_id):
        return item_id in self.slots.values()

    def items(self):
        return [i for i in self.slots.values() if i]

    def modifiers(self):
        return [m for i in self.items() for m in self.db[i].modifiers]

    def equip(self, inventory, item_id):
        """從背包裝備；原本那格有東西就放回背包（放不回去就整個取消）。回傳被換下來的物品或 None。"""
        item = self.db[item_id]
        if not item.slot:
            raise EquipError(f"{item.name} 不能裝備")
        if not inventory.has(item_id):
            raise EquipError(f"背包裡沒有 {item.name}")
        old = self.slots[item.slot]
        inventory.remove(item_id)
        if old:
            if inventory.add(old) != 1:
                inventory.add(item_id)          # 還原
                raise EquipError(f"背包滿了，{self.db[old].name} 卸不下來")
        self.slots[item.slot] = item_id
        return old

    def unequip(self, inventory, slot):
        item_id = self.slots.get(slot)
        if not item_id:
            return None
        if inventory.add(item_id) != 1:
            raise InventoryError(f"背包滿了，{self.db[item_id].name} 卸不下來")
        self.slots[slot] = None
        return item_id

    def to_dict(self):
        return dict(self.slots)

    @classmethod
    def from_dict(cls, d, db):
        eq = cls(db)
        for s in SLOTS:
            if d.get(s):
                eq.slots[s] = d[s]
        return eq
