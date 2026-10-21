import asyncio
import time
from collections import deque
from collections.abc import AsyncIterator, Hashable
from contextlib import asynccontextmanager


class KeyedLock:
    """依 key 取得獨立的鎖；同一 key 依序執行，不同 key 可並行。"""

    def __init__(self) -> None:
        self._locks: dict[Hashable, asyncio.Lock] = {}
        self._waiters: dict[Hashable, int] = {}

    @asynccontextmanager
    async def __call__(self, key: Hashable) -> AsyncIterator[None]:
        lock = self._locks.setdefault(key, asyncio.Lock())
        self._waiters[key] = self._waiters.get(key, 0) + 1
        try:
            async with lock:
                yield
        finally:
            self._waiters[key] -= 1
            if self._waiters[key] == 0:
                del self._waiters[key]
                del self._locks[key]

    def is_busy(self, key: Hashable) -> bool:
        return key in self._waiters


class RateLimiter:
    """滑動視窗限流器。"""

    def __init__(self, limit: int, window: float) -> None:
        self.limit = limit
        self.window = window
        self._hits: dict[Hashable, deque[float]] = {}
        self._last_sweep = time.monotonic()

    def hit(self, key: Hashable) -> float:
        """記錄一次請求；允許時回傳 0，否則回傳需等待的秒數。"""
        now = time.monotonic()
        self._sweep(now)

        hits = self._hits.setdefault(key, deque())
        while hits and now - hits[0] >= self.window:
            hits.popleft()

        if len(hits) >= self.limit:
            return self.window - (now - hits[0])

        hits.append(now)
        return 0.0

    def _sweep(self, now: float) -> None:
        if now - self._last_sweep < self.window:
            return

        self._last_sweep = now
        expired = [
            key for key, hits in self._hits.items()
            if not hits or now - hits[-1] >= self.window
        ]
        for key in expired:
            del self._hits[key]
