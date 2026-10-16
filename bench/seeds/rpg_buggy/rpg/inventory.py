"""背包：固定格數，可堆疊的物品會先補滿既有的格子再佔新格子。"""

from . import config


class InventoryError(Exception):
    pass


class Inventory:
    def __init__(self, db, slots=config.INVENTORY_SLOTS):
        self.db = db
        self.max_slots = slots
        self.slots = []          # [[item_id, count], ...]
        self.gold = 0

    # ---- 查詢
    def count(self, item_id):
        return sum(c for i, c in self.slots if i == item_id)

    def has(self, item_id, n=1):
        return self.count(item_id) >= n

    def free_slots(self):
        return self.max_slots - len(self.slots)

    def space_for(self, item_id):
        """還能放進幾個這種物品。"""
        item = self.db[item_id]
        room = sum(item.max_stack - c for i, c in self.slots if i == item_id)
        return room + self.free_slots() * item.max_stack

    def items(self):
        """{item_id: 數量}，依第一次放進背包的順序。"""
        out = {}
        for i, c in self.slots:
            out[i] = out.get(i, 0) + c
        return out

    # ---- 修改
    def add(self, item_id, n=1):
        """放進背包，回傳實際放進去的數量（背包滿了就放不下剩下的）。"""
        if n <= 0:
            return 0
        item = self.db[item_id]
        left = n
        for s in self.slots:
            if s[0] == item_id and s[1] < item.max_stack:
                put = min(item.max_stack - s[1], left)
                s[1] += put
                left -= put
                if not left:
                    return n
        while left and self.free_slots():
            put = min(item.max_stack, left)
            self.slots.append([item_id, put])
            left -= put
        return n - left

    def add_all(self, item_id, n=1):
        """全部放得下才放，否則什麼都不放並丟出 InventoryError。"""
        if self.space_for(item_id) < n:
            raise InventoryError(f"背包放不下 {n} 個 {self.db[item_id].name}")
        self.add(item_id, n)

    def remove(self, item_id, n=1):
        if n <= 0:
            return
        if self.count(item_id) < n:
            raise InventoryError(f"{self.db[item_id].name} 不夠（需要 {n}，只有 {self.count(item_id)}）")
        left = n
        for s in reversed(self.slots):          # 從最後一格開始拿，前面的格子保持滿的
            if s[0] == item_id:
                take = min(s[1], left)
                s[1] -= take
                left -= take
                if not left:
                    break
        self.slots = [s for s in self.slots if s[1] > 0]

    def spend(self, amount):
        if amount < 0:
            raise ValueError("金額不能是負的")
        if self.gold < amount:
            raise InventoryError(f"金幣不夠（需要 {amount}，只有 {self.gold}）")
        self.gold -= amount

    def earn(self, amount):
        if amount < 0:
            raise ValueError("金額不能是負的")
        self.gold += amount

    # ---- 存檔
    def to_dict(self):
        return {"slots": [list(s) for s in self.slots], "gold": self.gold, "max_slots": self.max_slots}

    @classmethod
    def from_dict(cls, d, db):
        inv = cls(db, d.get("max_slots", config.INVENTORY_SLOTS))
        inv.gold = int(d.get("gold", 0))
        for item_id, c in d.get("slots", []):
            inv.add(item_id, int(c))
        return inv
