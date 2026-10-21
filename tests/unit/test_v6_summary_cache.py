"""Cache bounds/failure behavior; real Mongo invalidation has an integration gate."""

import asyncio
from unittest.mock import AsyncMock, Mock

import pytest
from pymongo.errors import OperationFailure

from app.security.policy.evaluation import PolicyConflict
from app.summaries import cache as module
from app.summaries.cache import (
    MAX_BYTES,
    MAX_CHANGES,
    MAX_ENTRIES,
    MAX_ORGANIZATIONS,
    OverviewCache,
    for_source,
)


def collection():
    stream = Mock(alive=True, try_next=AsyncMock(return_value=None), close=AsyncMock())
    return Mock(watch=AsyncMock(return_value=stream)), stream


async def test_cache_reuses_only_same_key_generation_and_observation_time_until_source_change():
    selected, stream = collection()
    cache = OverviewCache()
    generation = await cache.prepare(selected, "org-a")
    operation = AsyncMock(return_value={"count": 3, "observed_at": "original-snapshot"})
    first = await cache.read(
        b"tenant-subject-policy-retention-filter", generation, operation, org="org-a"
    )
    first["count"] = 99
    assert await cache.read(
        b"tenant-subject-policy-retention-filter", generation, operation, org="org-a"
    ) == {
        "count": 3,
        "observed_at": "original-snapshot",
    }
    assert operation.await_count == 1
    stream.try_next.side_effect = [{"operationType": "update"}, None]
    current = await cache.prepare(selected, "org-a")
    assert current != generation and cache.bytes == 0 and not cache.entries
    operation.return_value = {"count": 4}
    assert await cache.read(
        b"tenant-subject-policy-retention-filter", current, operation, org="org-a"
    ) == {"count": 4}
    assert operation.await_count == 2
    await cache.read(b"foreign-tenant-or-new-policy", current, operation, org="org-a")
    assert operation.await_count == 3


@pytest.mark.parametrize("failure", ["error", "closed", "backlog"])
async def test_source_observation_failure_discards_cache_and_never_serves_stale_data(failure):
    selected, stream = collection()
    cache = OverviewCache()
    generation = await cache.prepare(selected, "org-a")
    await cache.read(b"safe", generation, AsyncMock(return_value={"count": 3}), org="org-a")
    if failure == "error":
        stream.try_next.side_effect = OperationFailure("synthetic-source-unavailable")
    elif failure == "closed":
        stream.alive = False
    else:
        stream.try_next.return_value = {"operationType": "insert"}
    with pytest.raises((OperationFailure, PolicyConflict)):
        await cache.prepare(selected, "org-a")
    assert not cache.entries and cache.bytes == 0 and cache.stream is None
    stream.close.assert_awaited_once()
    assert stream.try_next.await_count <= MAX_CHANGES + 1


async def test_generation_change_during_computation_prevents_storing_old_snapshot():
    cache = OverviewCache()
    entered, release = asyncio.Event(), asyncio.Event()

    async def operation():
        entered.set()
        await release.wait()
        return {"count": 3}

    pending = asyncio.create_task(cache.read(b"safe", cache.token("org-a"), operation, org="org-a"))
    await entered.wait()
    cache.invalidate()
    release.set()
    assert await pending == {"count": 3} and not cache.entries


async def test_cache_has_entry_byte_and_ttl_limits(monkeypatch):
    cache = OverviewCache()
    operation = AsyncMock(return_value={"count": 1})
    for index in range(MAX_ENTRIES + 3):
        await cache.read(str(index).encode(), cache.token("org-a"), operation, org="org-a")
    assert len(cache.entries) == MAX_ENTRIES and b"0" not in cache.entries
    assert cache.bytes <= MAX_BYTES
    oversized = AsyncMock(return_value={"metadata": "x" * MAX_BYTES})
    await cache.read(b"too-large", cache.token("org-a"), oversized, org="org-a")
    assert b"too-large" not in cache.entries
    monkeypatch.setattr(module, "monotonic", lambda: float("inf"))
    await cache.read(b"fresh", cache.token("org-a"), operation, org="org-a")
    assert list(cache.entries) == [b"fresh"]
    assert cache.bytes <= MAX_BYTES


def test_cache_is_private_to_engine_and_declared_source():
    first, second = Mock(), Mock()
    assert for_source(first, "gateway_activity") is for_source(first, "gateway_activity")
    assert for_source(first, "gateway_activity") is not for_source(second, "gateway_activity")
    assert for_source(first, "gateway_activity") is not for_source(first, "governance")
    with pytest.raises(ValueError):
        for_source(first, "private_identity")


async def test_queued_observers_share_only_a_poll_started_after_their_arrival():
    selected, stream = collection()
    cache = OverviewCache()
    entered, release = asyncio.Event(), asyncio.Event()
    polls = 0

    async def poll():
        nonlocal polls
        polls += 1
        if polls == 1:
            entered.set()
            await release.wait()
        return None

    stream.try_next.side_effect = poll
    first = asyncio.create_task(cache.prepare(selected, "org-a"))
    await entered.wait()
    queued = [asyncio.create_task(cache.prepare(selected, "org-a")) for _ in range(20)]
    await asyncio.sleep(0)
    release.set()
    assert len(set(await asyncio.gather(first, *queued))) == 1
    # The original poll began before the waiters arrived. They require a NEW
    # poll; all may share that second observation without twenty serial polls.
    assert polls == 2
    await cache.prepare(selected, "org-a")
    assert polls == 3  # no completed source-observation TTL bypass


async def test_foreign_inserts_do_not_invalidate_or_supply_another_organizations_result():
    selected, stream = collection()
    cache = OverviewCache()
    a = await cache.prepare(selected, "org-a")
    b = await cache.prepare(selected, "org-b")
    first = AsyncMock(return_value={"count": 7})
    second = AsyncMock(return_value={"count": 13})
    await cache.read(b"a", a, first, org="org-a")
    await cache.read(b"b", b, second, org="org-b")
    stream.try_next.side_effect = [
        {"operationType": "insert", "fullDocument": {"org_id": "org-b"}},
        None,
    ]
    assert await cache.prepare(selected, "org-a") == a
    assert await cache.read(b"a", a, first, org="org-a") == {"count": 7}
    assert first.await_count == 1 and b"b" not in cache.entries
    # Even an accidental equal opaque key cannot replay another org's value.
    assert await cache.read(b"a", cache.token("org-b"), second, org="org-b") == {"count": 13}
    assert second.await_count == 2


def test_source_organization_generation_registry_is_bounded():
    cache = OverviewCache()
    cache.register("org-a")
    old = cache.token("org-a")
    for number in range(MAX_ORGANIZATIONS + 2):
        cache.register(f"org-{number}")
    assert len(cache.organizations) <= MAX_ORGANIZATIONS
    assert cache.token("org-a") != old
