"""事件系統：各模組不直接互相呼叫，而是發出事件，由訂閱的模組處理（例如任務進度）。

處理中的事件如果又發出新事件，新事件會排在佇列後面，等目前這個事件的所有處理者都跑完才處理，
所以處理順序永遠是事件發出的順序（先進先出）。
"""

from collections import defaultdict, deque


class EventBus:
    def __init__(self):
        self.handlers = defaultdict(list)
        self.queue = deque()
        self.history = []          # [(type, data)]，除錯和測試用
        self._busy = False

    def subscribe(self, etype, handler):
        self.handlers[etype].append(handler)

    def unsubscribe(self, etype, handler):
        if handler in self.handlers[etype]:
            self.handlers[etype].remove(handler)

    def emit(self, etype, **data):
        self.queue.append((etype, data))
        if self._busy:
            return
        self._busy = True
        try:
            while self.queue:
                t, d = self.queue.popleft()
                self.history.append((t, d))
                for h in list(self.handlers[t]) + list(self.handlers["*"]):
                    h(t, d)
        finally:
            self._busy = False

    def of_type(self, etype):
        return [d for t, d in self.history if t == etype]
