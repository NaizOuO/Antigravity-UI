"""存檔與讀檔。

存檔格式有版本號（config.SAVE_VERSION）。讀到舊版本的存檔要先轉換成目前的格式，舊存檔一律要能讀。
  版本 1：裝備是清單 "equipped": ["iron_sword", ...]；沒有狀態效果、沒有任務
  版本 2：裝備是 {"weapon": ..., "armor": ..., "accessory": ...}；有 effects、quests、rng_state
"""

import json
from pathlib import Path

from . import config
from .effects import Effects
from .entity import Player
from .equipment import Equipment
from .inventory import Inventory
from .stats import Stats


class SaveError(Exception):
    pass


def player_to_dict(p):
    return {
        "name": p.name, "level": p.level, "xp": p.xp, "hp": p.hp,
        "base": p.base.to_dict(),
        "inventory": p.inventory.to_dict(),
        "equipment": p.equipment.to_dict(),
        "effects": p.effects.to_list(),
    }


def player_from_dict(d, db):
    p = Player(d["name"], Stats.from_dict(d["base"]), db)
    p.level, p.xp = int(d["level"]), int(d["xp"])
    p.inventory = Inventory.from_dict(d["inventory"], db)
    p.equipment = Equipment.from_dict(d.get("equipment", {}), db)
    p.effects = Effects.from_list(d.get("effects", []))
    p.hp = int(d["hp"])
    p.clamp_hp()
    return p


def migrate(data, db):
    """把舊版本的存檔轉成目前版本。"""
    v = data.get("version", 1)
    if v > config.SAVE_VERSION:
        raise SaveError(f"存檔版本 {v} 比遊戲新，請更新遊戲")
    if v == 1:
        pl = dict(data["player"])
        eq = {}
        for item_id in pl.pop("equipped", []):
            slot = db[item_id].slot
            if slot:
                eq[slot] = item_id
        pl["equipment"] = eq
        pl.setdefault("effects", [])
        data = {"version": 2, "player": pl, "quests": {"active": {}, "done": []}, "rng_state": None}
    return data


def game_to_dict(game):
    return {"version": config.SAVE_VERSION, "player": player_to_dict(game.player),
            "quests": game.quests.to_dict(), "rng_state": _rng_dump(game.rng)}


def save_game(game, path):
    Path(path).write_text(json.dumps(game_to_dict(game), ensure_ascii=False, indent=1), encoding="utf-8")


def load_data(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise SaveError(f"讀不到存檔：{e}") from None


def _rng_dump(rng):
    st = rng.getstate()
    return [st[0], list(st[1]), st[2]]


def rng_load(rng, data):
    if data:
        rng.setstate((data[0], tuple(data[1]), data[2]))
