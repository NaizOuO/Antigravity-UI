"""物品定義與物品資料庫（從 data/items.json 讀取）。"""

import json
from dataclasses import dataclass, field
from pathlib import Path

from .stats import Modifier

KINDS = ("weapon", "armor", "accessory", "consumable", "material", "quest")
EQUIP_SLOT = {"weapon": "weapon", "armor": "armor", "accessory": "accessory"}


@dataclass(frozen=True)
class Item:
    id: str
    name: str
    kind: str
    price: int = 0
    max_stack: int = 1
    modifiers: tuple = ()
    use: dict = field(default_factory=dict, hash=False, compare=False)   # 消耗品效果：{"heal": 30} 或 {"effect": "regen", "turns": 3, "power": 5}

    @property
    def stackable(self):
        return self.max_stack > 1

    @property
    def slot(self):
        return EQUIP_SLOT.get(self.kind)

    @property
    def sellable(self):
        return self.kind != "quest" and self.price > 0


class ItemDB:
    def __init__(self, items):
        self._items = {i.id: i for i in items}

    def __getitem__(self, item_id):
        try:
            return self._items[item_id]
        except KeyError:
            raise KeyError(f"沒有這個物品：{item_id}") from None

    def __contains__(self, item_id):
        return item_id in self._items

    def all(self):
        return list(self._items.values())

    @classmethod
    def load(cls, path):
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        items = []
        for d in raw:
            if d["kind"] not in KINDS:
                raise ValueError(f"{d['id']} 的種類不正確：{d['kind']}")
            mods = tuple(Modifier(m["stat"], m.get("flat", 0), m.get("pct", 0.0), d["id"]) for m in d.get("modifiers", []))
            items.append(Item(d["id"], d["name"], d["kind"], d.get("price", 0), d.get("max_stack", 1), mods, d.get("use", {})))
        return cls(items)
