"""仅发送管理状态失效通知；断线后页面重新读取权威 REST 状态。"""
import asyncio
from contextlib import asynccontextmanager
from time import time


class EventBus:
    def __init__(self):
        self._subscribers: set[asyncio.Queue] = set()
        self._sequence = 0
        self._kinds: dict[str, int] = {}
        self._scopes: dict[tuple[str, str | None], int] = {}

    def publish(self, kind: str, *, instance_ids: tuple[str, ...] | None = None):
        self._sequence += 1
        self._kinds[kind] = self._kinds.get(kind, 0) + 1
        # 通知仍刷新所有管理页；依赖标记区分共享资料、私有资料和仅页面变化。
        # None保守标记共享变化，空元组只刷新UI，不改变任何业务缓存依赖。
        for instance_id in dict.fromkeys((None,) if instance_ids is None else instance_ids):
            key = (kind, instance_id)
            self._scopes[key] = self._scopes.get(key, 0) + 1
        event = {"id": self._sequence, "kind": kind, "at": time()}
        for queue in tuple(self._subscribers):
            if queue.full():
                queue.get_nowait()
            queue.put_nowait(event)

    @asynccontextmanager
    async def subscribe(self):
        if len(self._subscribers) >= 32:
            raise ValueError("管理端事件连接过多，请关闭重复打开的页面。")
        queue = asyncio.Queue(maxsize=32)
        self._subscribers.add(queue)
        try:
            yield queue
        finally:
            self._subscribers.discard(queue)

    @property
    def connections(self):
        return len(self._subscribers)

    def revisions(self, kinds, *, instance_id: str | None = None):
        """本进程资料变更标记；不持久化，也不替代数据库业务版本。"""
        if instance_id is not None:
            return tuple((self._scopes.get((kind, None), 0), self._scopes.get((kind, instance_id), 0)) for kind in kinds)
        return tuple(self._kinds.get(kind, 0) for kind in kinds)
