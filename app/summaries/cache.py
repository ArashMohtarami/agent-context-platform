"""Bounded aggregate data cache; MongoDB changes invalidate before every reuse.

Authority is never cached. Callers must bind exact visibility/retention/policy
and perform the final current-policy fence independently. Cold results retain
their original observation time. No cache, cursor or resume token is persisted.
"""

import asyncio
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from copy import deepcopy
from time import monotonic
from typing import Any, cast
from weakref import WeakKeyDictionary

from bson import BSON
from pymongo.errors import PyMongoError

from app.security.policy.evaluation import PolicyConflict
from app.summaries.concurrent import ConcurrentReads

MAX_ENTRIES = 128
MAX_BYTES = 4 * 1024 * 1024
TTL_SECONDS = 30
MAX_CHANGES = 2048
MAX_ORGANIZATIONS = 512
type Generation = tuple[int, int]


class OverviewCache:
    def __init__(self) -> None:
        self.entries: OrderedDict[bytes, tuple[float, int, str, Any]] = OrderedDict()
        self.bytes = 0
        self.generation = 0
        self.stream: Any = None
        self.lock = asyncio.Lock()
        self.flights = ConcurrentReads()
        self.observed_since = float("-inf")
        self.organizations: dict[str, int] = {}

    def invalidate(self) -> None:
        self.entries.clear()
        self.bytes = 0
        self.generation += 1
        self.observed_since = float("-inf")
        self.organizations.clear()

    def token(self, org: str) -> Generation:
        return self.generation, self.organizations.get(org, 0)

    def register(self, org: str) -> None:
        if org not in self.organizations and len(self.organizations) >= MAX_ORGANIZATIONS:
            self.invalidate()
        self.organizations.setdefault(org, 0)

    def invalidate_org(self, org: str) -> None:
        if org in self.organizations:
            self.organizations[org] += 1
        for key in [k for k, entry in self.entries.items() if entry[2] == org]:
            self.bytes -= self.entries.pop(key)[1]

    async def prepare(self, collection: Any, org: str) -> Generation:
        # Establish the watch BEFORE the raw snapshot. In particular, creating
        # it after aggregation would miss concurrent/backdated inserts.
        requested_at = monotonic()
        async with self.lock:
            try:
                # A queued caller may share an observation that STARTED after
                # it requested one. A poll merely completing after its arrival
                # is insufficient: its server snapshot could precede arrival.
                if self.stream is not None and self.observed_since >= requested_at:
                    self.register(org)
                    return self.token(org)
                if self.stream is None:
                    self.invalidate()
                    self.stream = await collection.watch(
                        [{"$project": {"_id": 1, "operationType": 1, "fullDocument.org_id": 1}}],
                        max_await_time_ms=1,
                        batch_size=256,
                    )
                for _ in range(MAX_CHANGES):
                    poll_started = monotonic()
                    change = await self.stream.try_next()
                    if change is None:
                        if not self.stream.alive:
                            raise PolicyConflict("overview source observation was interrupted")
                        self.observed_since = poll_started
                        self.register(org)
                        return self.token(org)
                    # Inserts, updates, deletes and collection invalidation all
                    # invalidate. No event body, identity or query is retained.
                    changed_org = (change.get("fullDocument") or {}).get("org_id")
                    if change.get("operationType") == "insert" and isinstance(changed_org, str):
                        self.invalidate_org(changed_org)
                    else:
                        # Updates/deletes without a trustworthy owner and
                        # collection invalidation conservatively clear all.
                        self.invalidate()
                raise PolicyConflict("overview source changes exceed bounded observation")
            except (PyMongoError, PolicyConflict, asyncio.CancelledError):
                self.invalidate()
                stream, self.stream = self.stream, None
                if stream is not None:
                    await stream.close()
                raise

    async def read[T](
        self, key: bytes, generation: Generation, operation: Callable[[], Awaitable[T]], *, org: str
    ) -> T:
        now = monotonic()
        for expired in [k for k, v in self.entries.items() if v[0] <= now]:
            self.bytes -= self.entries.pop(expired)[1]
        cached = self.entries.get(key) if generation == self.token(org) else None
        if cached is not None and cached[2] == org:
            self.entries.move_to_end(key)
            return cast(T, deepcopy(cached[3]))

        async def populate() -> T:
            result = await operation()
            size = len(BSON.encode({"result": result}))
            if generation == self.token(org) and size <= MAX_BYTES:
                while self.entries and (
                    len(self.entries) >= MAX_ENTRIES or self.bytes + size > MAX_BYTES
                ):
                    _, (_, removed, _, _) = self.entries.popitem(last=False)
                    self.bytes -= removed
                previous = self.entries.pop(key, None)
                if previous is not None:
                    self.bytes -= previous[1]
                self.entries[key] = monotonic() + TTL_SECONDS, size, org, deepcopy(result)
                self.bytes += size
            return result

        return await self.flights.run(
            org.encode() + b"\0" + str(generation).encode() + b":" + key, populate
        )


_CACHES: WeakKeyDictionary[Any, dict[str, OverviewCache]] = WeakKeyDictionary()


def for_source(engine: Any, source: str) -> OverviewCache:
    if source not in {"gateway_activity", "governance"}:
        raise ValueError("unclassified overview source")
    if engine not in _CACHES:
        _CACHES[engine] = {}
    return _CACHES[engine].setdefault(source, OverviewCache())
