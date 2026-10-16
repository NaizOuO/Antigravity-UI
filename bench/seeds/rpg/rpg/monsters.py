"""怪物資料（data/monsters.json）。每次 spawn 都是新的一隻，HP 全滿。"""

import json
from pathlib import Path

from .entity import Monster
from .stats import Stats


class Bestiary:
    def __init__(self, defs):
        self.defs = defs

    @classmethod
    def load(cls, path):
        return cls({d["id"]: d for d in json.loads(Path(path).read_text(encoding="utf-8"))})

    def ids(self):
        return list(self.defs)

    def spawn(self, mid):
        d = self.defs[mid]
        return Monster(mid, d["name"], Stats.from_dict(d["stats"]), d.get("xp", 0), d.get("gold", 0),
                       [tuple(x) for x in d.get("drops", [])], d.get("skills", []))
