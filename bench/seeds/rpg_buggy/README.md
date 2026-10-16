# 文字 RPG

純文字的回合制 RPG 引擎。`python -m rpg.cli` 開始玩，`python -m unittest discover tests` 跑測試。

| 檔案 | 內容 |
|---|---|
| rpg/config.py | 平衡數值 |
| rpg/stats.py | 能力值與加成 |
| rpg/items.py | 物品與物品資料庫 |
| rpg/inventory.py | 背包與金幣 |
| rpg/equipment.py | 裝備欄 |
| rpg/effects.py | 狀態效果 |
| rpg/entity.py | 角色、玩家、怪物 |
| rpg/monsters.py | 怪物資料 |
| rpg/events.py | 事件系統 |
| rpg/combat.py | 戰鬥 |
| rpg/quests.py | 任務 |
| rpg/shop.py | 商店 |
| rpg/save.py | 存檔（有版本，舊存檔要能讀） |
| rpg/game.py | 把以上接在一起 |
| rpg/cli.py | 文字介面 |
| data/ | 物品、怪物、任務資料 |
