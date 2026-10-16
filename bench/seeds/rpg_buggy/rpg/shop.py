"""商店：買賣都是「全部成功或全部不做」，不會扣了錢卻沒拿到東西。

買價 = ceil(物品價格 × 商店倍率)；賣價 = floor(物品價格 × SELL_RATIO)。
裝備中的物品、任務物品不能賣。
"""

import math

from . import config


class ShopError(Exception):
    pass


class Shop:
    def __init__(self, db, stock, price_mult=1.0, bus=None):
        self.db = db
        self.stock = list(stock)
        self.price_mult = price_mult
        self.bus = bus

    def buy_price(self, item_id):
        return math.ceil(self.db[item_id].price * self.price_mult)

    def sell_price(self, item_id):
        return math.floor(self.db[item_id].price * config.SELL_RATIO)

    def buy(self, player, item_id, n=1):
        if item_id not in self.stock:
            raise ShopError(f"這裡沒有賣 {self.db[item_id].name}")
        if n <= 0:
            raise ShopError("數量要大於 0")
        cost = self.buy_price(item_id) * n
        inv = player.inventory
        if inv.gold < cost:
            raise ShopError(f"金幣不夠（需要 {cost}）")
        inv.spend(cost)
        if inv.space_for(item_id) < n:
            raise ShopError("背包放不下")
        inv.add(item_id, n)
        if self.bus:
            self.bus.emit("bought", item=item_id, count=n, cost=cost)
        return cost

    def sell(self, player, item_id, n=1):
        item = self.db[item_id]
        if not item.sellable:
            raise ShopError(f"{item.name} 不能賣")
        inv = player.inventory
        if inv.count(item_id) < n:
            raise ShopError(f"{item.name} 不夠")
        if n <= 0:
            raise ShopError("數量要大於 0")
        gain = self.sell_price(item_id) * n
        inv.remove(item_id, n)
        inv.earn(gain)
        if self.bus:
            self.bus.emit("sold", item=item_id, count=n, gain=gain)
        return gain
