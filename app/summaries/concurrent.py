"""Bounded in-flight reads only: no completed result or authority is cached."""

import asyncio
from collections.abc import Awaitable, Callable
from copy import deepcopy
from typing import Any, cast

from app.security.policy.evaluation import PolicyConflict

MAX_FLIGHTS = 32
MAX_WAITERS = 100


class ConcurrentReads:
    def __init__(self) -> None:
        self.pending: dict[bytes, tuple[asyncio.Future[Any], int]] = {}

    async def run[T](self, key: bytes, operation: Callable[[], Awaitable[T]]) -> T:
        existing = self.pending.get(key)
        if existing is not None:
            future, waiters = existing
            if waiters >= MAX_WAITERS:
                raise PolicyConflict("overview concurrency is at capacity")
            self.pending[key] = future, waiters + 1
            # A cancelled waiter cannot cancel another request's database read.
            return cast(T, deepcopy(await asyncio.shield(future)))
        if len(self.pending) >= MAX_FLIGHTS:
            raise PolicyConflict("overview concurrency is at capacity")
        future = asyncio.get_running_loop().create_future()
        self.pending[key] = future, 0
        try:
            result = await operation()
            future.set_result(deepcopy(result))
            return result
        except BaseException as error:
            future.set_exception(error)
            future.exception()  # also consume failures when there were no waiters
            raise
        finally:
            self.pending.pop(key, None)
