"""任務：靠事件更新進度（打倒怪物），收集類任務在交任務時才檢查背包。

任務狀態：available（可接）→ active（進行中）→ done（已完成）。
只有接了任務之後打倒的怪物才算數。
"""

import json
from pathlib import Path


class QuestError(Exception):
    pass


class QuestDef:
    def __init__(self, d):
        self.id = d["id"]
        self.name = d["name"]
        self.kill = dict(d.get("kill", {}))          # {monster_id: 數量}
        self.collect = dict(d.get("collect", {}))    # {item_id: 數量}，交任務時會拿走
        self.reward = dict(d.get("reward", {}))      # {"xp": 0, "gold": 0, "items": {item_id: 數量}}
        self.requires = list(d.get("requires", []))  # 要先完成的任務


def load_quests(path):
    return {q["id"]: QuestDef(q) for q in json.loads(Path(path).read_text(encoding="utf-8"))}


class QuestLog:
    def __init__(self, defs, bus):
        self.defs = defs
        self.bus = bus
        self.active = {}          # quest_id -> {monster_id: 已打倒數量}
        self.done = set()
        bus.subscribe("monster_killed", self._on_kill)

    def status(self, qid):
        if qid in self.done:
            return "done"
        if qid in self.active:
            return "active"
        return "available" if all(r in self.done for r in self.defs[qid].requires) else "locked"

    def accept(self, qid):
        if qid not in self.defs:
            raise QuestError(f"沒有這個任務：{qid}")
        st = self.status(qid)
        if st != "available":
            raise QuestError(f"任務 {self.defs[qid].name} 目前不能接（{st}）")
        self.active[qid] = {m: 0 for m in self.defs[qid].kill}
        self.bus.emit("quest_accepted", quest=qid)

    def _on_kill(self, _t, d):
        for qid, prog in self.active.items():
            need = self.defs[qid].kill
            m = d["monster"]
            if m in need and prog[m] < need[m]:
                prog[m] += 1
                self.bus.emit("quest_progress", quest=qid, monster=m, count=prog[m])

    def progress(self, qid):
        return dict(self.active.get(qid, {}))

    def can_complete(self, qid, player):
        if qid not in self.active:
            return False
        q = self.defs[qid]
        kills_ok = all(self.active[qid][m] >= n for m, n in q.kill.items())
        items_ok = all(player.inventory.has(i, n) for i, n in q.collect.items())
        return kills_ok and items_ok

    def complete(self, qid, player):
        if not self.can_complete(qid, player):
            raise QuestError(f"任務 {self.defs[qid].name} 還沒完成")
        q = self.defs[qid]
        reward_items = q.reward.get("items", {})
        # 先確認獎勵放得下（收集的物品拿走後會空出位置，所以用模擬的方式算）
        sim = player.inventory.to_dict()
        from .inventory import Inventory
        test = Inventory.from_dict(sim, player.inventory.db)
        for i, n in q.collect.items():
            test.remove(i, n)
        for i, n in reward_items.items():
            if test.add(i, n) != n:
                raise QuestError("背包放不下任務獎勵")
        for i, n in q.collect.items():
            player.inventory.remove(i, n)
        for i, n in reward_items.items():
            player.inventory.add(i, n)
        player.inventory.earn(q.reward.get("gold", 0))
        levels = player.gain_xp(q.reward.get("xp", 0))
        del self.active[qid]
        self.done.add(qid)
        self.bus.emit("quest_done", quest=qid)
        if levels:
            self.bus.emit("level_up", level=player.level)

    def to_dict(self):
        return {"active": {q: dict(p) for q, p in self.active.items()}, "done": sorted(self.done)}

    def load(self, d):
        self.active = {q: dict(p) for q, p in d.get("active", {}).items() if q in self.defs}
        self.done = {q for q in d.get("done", []) if q in self.defs}
