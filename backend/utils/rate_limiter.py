import asyncio
import time


class TokenBucketRateLimiter:
    """
    Limits requests to ~3/second (Notion API limit) using a token bucket.
    """

    def __init__(self, rate: float = 3.0, capacity: int = 10):
        self.rate = rate        # tokens per second
        self.capacity = capacity
        self._tokens = float(capacity)
        self._last_refill = time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        async with self._lock:
            now = time.monotonic()
            elapsed = now - self._last_refill
            self._tokens = min(
                self.capacity,
                self._tokens + elapsed * self.rate
            )
            self._last_refill = now

            if self._tokens < 1:
                wait = (1 - self._tokens) / self.rate
                await asyncio.sleep(wait)
                self._tokens = 0
            else:
                self._tokens -= 1
