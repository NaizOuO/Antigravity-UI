"""狀態效果：中毒、回復、加速、虛弱、護盾。

同一種效果重複施加時不會疊兩層：持續回合取較長的，強度取較大的。
每個效果在角色「自己的回合開始時」作用一次，然後回合數減 1，歸零就移除。
"""

from dataclasses import dataclass

from .stats import Modifier

TYPES = {
    "poison": "中毒",     # 每回合損失 power 點 HP（無視防禦，不會因此死亡以外的其他效果）
    "regen": "回復",      # 每回合回復 power 點 HP
    "haste": "加速",      # 敏捷 +power%
    "weaken": "虛弱",     # 攻擊 −power%
    "shield": "護盾",     # 吸收 power 點傷害，吸收完就消失
}


@dataclass
class Effect:
    kind: str
    turns: int
    power: int

    def __post_init__(self):
        if self.kind not in TYPES:
            raise ValueError(f"沒有這種效果：{self.kind}")

    def modifiers(self):
        if self.kind == "haste":
            return [Modifier("agi", pct=self.power / 100, source="haste")]
        if self.kind == "weaken":
            return [Modifier("atk", pct=-self.power / 100, source="weaken")]
        return []

    def to_dict(self):
        return {"kind": self.kind, "turns": self.turns, "power": self.power}

    @classmethod
    def from_dict(cls, d):
        return cls(d["kind"], int(d["turns"]), int(d["power"]))


class Effects:
    def __init__(self):
        self.active = {}        # kind -> Effect

    def apply(self, effect):
        cur = self.active.get(effect.kind)
        if cur:
            cur.turns = max(cur.turns, effect.turns)
            cur.power = max(cur.power, effect.power)
        else:
            self.active[effect.kind] = Effect(effect.kind, effect.turns, effect.power)

    def has(self, kind):
        return kind in self.active

    def get(self, kind):
        return self.active.get(kind)

    def remove(self, kind):
        self.active.pop(kind, None)

    def clear(self):
        self.active.clear()

    def modifiers(self):
        return [m for e in self.active.values() for m in e.modifiers()]

    def absorb(self, damage):
        """護盾吸收傷害，回傳剩下要扣的傷害。"""
        sh = self.active.get("shield")
        if not sh or damage <= 0:
            return damage
        used = min(sh.power, damage)
        sh.power -= used
        if sh.power <= 0:
            self.remove("shield")
        return damage - used

    def tick(self, character):
        """回合開始時作用一次，回傳發生的事 [(kind, 數值)]。"""
        log = []
        for kind in list(self.active):
            e = self.active[kind]
            if kind == "poison":
                log.append(("poison", character.lose_hp(e.power)))
            elif kind == "regen":
                log.append(("regen", character.heal(e.power)))
            e.turns -= 1
            if e.turns <= 0:
                self.remove(kind)
                log.append(("expired", kind))
        return log

    def to_list(self):
        return [e.to_dict() for e in self.active.values()]

    @classmethod
    def from_list(cls, data):
        fx = cls()
        for d in data:
            fx.apply(Effect.from_dict(d))
        return fx
