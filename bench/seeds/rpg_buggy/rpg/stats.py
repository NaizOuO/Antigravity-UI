"""角色能力值與加成。

加成（Modifier）的套用順序：先把所有固定值（flat）加總，再乘上所有百分比（pct）的總和。
例如 atk 10，+5 與 +20%、-10% → (10 + 5) × (1 + 0.2 − 0.1) = 16.5 → 取整數 16。
"""

from dataclasses import dataclass, asdict, fields

STAT_NAMES = ("max_hp", "atk", "defense", "agi", "luck")


@dataclass
class Stats:
    max_hp: int = 1
    atk: int = 0
    defense: int = 0
    agi: int = 0
    luck: int = 0

    def __add__(self, other):
        return Stats(**{f: getattr(self, f) + getattr(other, f) for f in STAT_NAMES})

    def copy(self):
        return Stats(**asdict(self))

    def to_dict(self):
        return asdict(self)

    @classmethod
    def from_dict(cls, d):
        known = {f.name for f in fields(cls)}
        return cls(**{k: int(v) for k, v in d.items() if k in known})


@dataclass(frozen=True)
class Modifier:
    stat: str
    flat: int = 0
    pct: float = 0.0
    source: str = ""

    def __post_init__(self):
        if self.stat not in STAT_NAMES:
            raise ValueError(f"沒有這個能力值：{self.stat}")

    def to_dict(self):
        return {"stat": self.stat, "flat": self.flat, "pct": self.pct, "source": self.source}

    @classmethod
    def from_dict(cls, d):
        return cls(d["stat"], int(d.get("flat", 0)), float(d.get("pct", 0.0)), d.get("source", ""))


def apply_modifiers(base, modifiers):
    """回傳套用加成後的新 Stats；能力值不會低於 0，max_hp 不會低於 1。"""
    out = {}
    for name in STAT_NAMES:
        flat = sum(m.flat for m in modifiers if m.stat == name)
        pct = sum(m.pct for m in modifiers if m.stat == name)
        v = int((getattr(base, name) + flat) * (1 + pct))
        out[name] = max(1 if name == "max_hp" else 0, v)
    return Stats(**out)
