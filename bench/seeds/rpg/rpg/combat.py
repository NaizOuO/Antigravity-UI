"""回合制戰鬥。

一輪（round）裡每個活著的角色依敏捷由高到低各行動一次；敏捷相同時玩家先，怪物之間照出場順序。
敏捷在每一輪開始時重新計算（加速效果、換裝備都會影響下一輪的順序）。
每個角色行動前先作用自己的狀態效果；因此中毒死亡的角色就不會行動。

傷害公式（damage）：
    攻擊力 × 浮動(1 ± DAMAGE_SPREAD) − 對方防禦 × DEFENSE_FACTOR，至少 MIN_DAMAGE；爆擊再 × CRIT_MULT
爆擊率 = CRIT_BASE + 幸運 × CRIT_PER_LUCK

發出的事件：battle_start、attack、effect_tick、monster_killed、loot、loot_lost、level_up、battle_end
"""

import math

from . import config
from .effects import Effect


class BattleError(Exception):
    pass


def damage(attacker_stats, defender_stats, rng):
    roll = 1 + rng.uniform(-config.DAMAGE_SPREAD, config.DAMAGE_SPREAD)
    raw = attacker_stats.atk * roll - defender_stats.defense * config.DEFENSE_FACTOR
    dmg = max(config.MIN_DAMAGE, int(round(raw)))
    crit = rng.random() < config.CRIT_BASE + attacker_stats.luck * config.CRIT_PER_LUCK
    if crit:
        dmg = int(dmg * config.CRIT_MULT)
    return dmg, crit


def flee_chance(player_stats, monsters):
    fastest = max(m.stats().agi for m in monsters)
    return max(0.0, min(config.FLEE_MAX, config.FLEE_BASE + (player_stats.agi - fastest) * config.FLEE_PER_AGI))


class Battle:
    def __init__(self, player, monsters, rng, bus):
        if not monsters:
            raise BattleError("沒有對手")
        self.player = player
        self.monsters = list(monsters)
        self.rng = rng
        self.bus = bus
        self.round = 0
        self.result = None           # "win" / "lose" / "fled"
        self.log = []
        bus.emit("battle_start", monsters=[m.id for m in self.monsters])

    # ---- 查詢
    def alive_monsters(self):
        return [m for m in self.monsters if m.alive]

    def turn_order(self):
        actors = [self.player] + self.monsters
        alive = [a for a in actors if a.alive]
        # sorted 是穩定排序：敏捷相同時維持「玩家、怪物出場順序」
        return sorted(alive, key=lambda a: -a.stats().agi)

    @property
    def over(self):
        return self.result is not None

    # ---- 一輪
    def play_round(self, action):
        """action：("attack", 怪物索引) / ("item", item_id) / ("flee",)。回傳這一輪的紀錄。"""
        if self.over:
            raise BattleError("戰鬥已經結束")
        self.round += 1
        start = len(self.log)
        for actor in self.turn_order():
            if self.over:
                break
            if not actor.alive:
                continue
            for kind, value in actor.effects.tick(actor):
                self.bus.emit("effect_tick", who=actor.name, kind=kind, value=value)
                self.log.append(f"{actor.name}：{kind} {value}")
            if not actor.alive:
                self._check_deaths()
                continue
            if actor is self.player:
                self._player_act(action)
            else:
                self._monster_act(actor)
            self._check_deaths()
        return self.log[start:]

    def _player_act(self, action):
        kind = action[0]
        if kind == "attack":
            alive = self.alive_monsters()
            idx = action[1] if len(action) > 1 else 0
            target = self.monsters[idx] if 0 <= idx < len(self.monsters) and self.monsters[idx].alive else alive[0]
            self._attack(self.player, target)
        elif kind == "item":
            self.log.append(f"{self.player.name} 使用 {self.player.db[action[1]].name}：{self.player.use_item(action[1])}")
        elif kind == "flee":
            if self.rng.random() < flee_chance(self.player.stats(), self.alive_monsters()):
                self.log.append(f"{self.player.name} 逃跑成功")
                self._end("fled")
            else:
                self.log.append(f"{self.player.name} 逃跑失敗")
        else:
            raise BattleError(f"不認得的行動：{kind}")

    def _monster_act(self, monster):
        self._attack(monster, self.player)
        for sk in monster.skills:
            if self.player.alive and self.rng.random() < sk.get("chance", 0):
                self.player.effects.apply(Effect(sk["effect"], sk.get("turns", 3), sk.get("power", 1)))
                self.log.append(f"{monster.name} 使 {self.player.name} {sk['effect']}")

    def _attack(self, attacker, defender):
        dmg, crit = damage(attacker.stats(), defender.stats(), self.rng)
        dealt = defender.take_damage(dmg)
        self.bus.emit("attack", attacker=attacker.name, defender=defender.name, damage=dealt, crit=crit)
        self.log.append(f"{attacker.name} 攻擊 {defender.name}，造成 {dealt} 傷害" + ("（爆擊）" if crit else ""))

    def _check_deaths(self):
        for m in self.monsters:
            if not m.alive and not getattr(m, "_counted", False):
                m._counted = True
                self._reward(m)
        if not self.player.alive:
            self._end("lose")
        elif not self.alive_monsters():
            self._end("win")

    def _reward(self, m):
        p = self.player
        self.bus.emit("monster_killed", monster=m.id)
        p.inventory.earn(m.gold_reward)
        for item_id, chance in m.drops:
            if self.rng.random() < chance:
                if p.inventory.add(item_id) == 1:
                    self.bus.emit("loot", item=item_id)
                else:
                    self.bus.emit("loot_lost", item=item_id)
        levels = p.gain_xp(m.xp_reward)
        if levels:
            self.bus.emit("level_up", level=p.level)

    def _end(self, result):
        if self.result is None:
            self.result = result
            self.player.effects.remove("shield")     # 護盾只在戰鬥中有效
            self.bus.emit("battle_end", result=result, rounds=self.round)

    def run_auto(self, max_rounds=200):
        """自動戰鬥：玩家一直攻擊第一隻活著的怪物（測試、模擬平衡用）。"""
        while not self.over and self.round < max_rounds:
            self.play_round(("attack", self.monsters.index(self.alive_monsters()[0])))
        return self.result


def expected_damage(attacker_stats, defender_stats):
    """不含浮動與爆擊的平均傷害（給介面顯示用）。"""
    return max(config.MIN_DAMAGE, int(round(attacker_stats.atk - defender_stats.defense * config.DEFENSE_FACTOR)))


def rounds_to_kill(attacker_stats, defender):
    return math.ceil(defender.hp / expected_damage(attacker_stats, defender.stats()))
