"""遊戲的平衡數值。改這裡會影響戰鬥、商店、升級等很多地方。"""

# 戰鬥
MIN_DAMAGE = 1
DEFENSE_FACTOR = 0.5          # 傷害 = 攻擊 × 浮動 − 防禦 × DEFENSE_FACTOR
DAMAGE_SPREAD = 0.1           # 攻擊力上下浮動 10%
CRIT_BASE = 0.05              # 基本爆擊率
CRIT_PER_LUCK = 0.01          # 每點幸運增加的爆擊率
CRIT_MULT = 1.5
FLEE_BASE = 0.4               # 逃跑基本成功率
FLEE_PER_AGI = 0.02           # 玩家比怪物敏捷每多 1 點，逃跑率增加
FLEE_MAX = 0.9

# 升級：到達第 n 級需要的累計經驗（索引 0 = 1 級）
LEVEL_XP = [0, 20, 50, 100, 170, 260, 380, 530, 720, 950]
LEVEL_UP_GAIN = {"max_hp": 8, "atk": 2, "defense": 1, "agi": 1, "luck": 0}

# 經濟
SELL_RATIO = 0.5
START_GOLD = 50

# 背包
INVENTORY_SLOTS = 20

# 存檔
SAVE_VERSION = 2
