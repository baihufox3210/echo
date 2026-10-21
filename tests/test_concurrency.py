import asyncio

from echo.core.concurrency import KeyedLock, RateLimiter


def test_rate_limiter_blocks_after_limit():
    limiter = RateLimiter(limit=2, window=60)
    assert limiter.hit(1) == 0
    assert limiter.hit(1) == 0
    assert limiter.hit(1) > 0
    assert limiter.hit(2) == 0


async def test_keyed_lock_serializes_same_key_and_parallelizes_others():
    lock = KeyedLock()
    order: list[str] = []

    async def work(key: int, name: str):
        async with lock(key):
            order.append(f"{name}-start")
            await asyncio.sleep(0.05)
            order.append(f"{name}-end")

    await asyncio.gather(work(1, "a"), work(1, "b"), work(2, "c"))

    assert order.index("a-end") < order.index("b-start")
    assert order.index("c-start") < order.index("a-end")
    assert not lock.is_busy(1) and not lock.is_busy(2)
