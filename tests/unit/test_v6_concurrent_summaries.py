"""In-flight aggregation sharing never persists results or authorization."""

import asyncio

import pytest

from app.security.policy.evaluation import PolicyConflict
from app.summaries.concurrent import MAX_FLIGHTS, MAX_WAITERS, ConcurrentReads


async def test_identical_overlapping_reads_share_only_work_and_never_completed_results():
    reads = ConcurrentReads()
    entered, release = asyncio.Event(), asyncio.Event()
    calls = 0

    async def operation():
        nonlocal calls
        calls += 1
        entered.set()
        await release.wait()
        return {"items": [calls]}

    leader = asyncio.create_task(reads.run(b"org-a:owner:policy1:filter1", operation))
    await entered.wait()
    follower = asyncio.create_task(reads.run(b"org-a:owner:policy1:filter1", operation))
    await asyncio.sleep(0)
    release.set()
    first, second = await asyncio.gather(leader, follower)
    assert calls == 1 and first == second == {"items": [1]}
    first["items"].append("private-change")
    assert second == {"items": [1]} and reads.pending == {}
    assert await reads.run(b"org-a:owner:policy1:filter1", operation) == {"items": [2]}


async def test_different_authorization_keys_never_share_results():
    reads = ConcurrentReads()
    release = asyncio.Event()

    async def operation(value):
        await release.wait()
        return value

    tasks = [
        asyncio.create_task(reads.run(key, lambda key=key: operation(key)))
        for key in (b"org-a:owner:policy1", b"org-b:owner:policy1", b"org-a:lead:policy2")
    ]
    await asyncio.sleep(0)
    assert len(reads.pending) == 3
    release.set()
    assert await asyncio.gather(*tasks) == [
        b"org-a:owner:policy1",
        b"org-b:owner:policy1",
        b"org-a:lead:policy2",
    ]


async def test_waiter_cancellation_does_not_cancel_database_read_and_failures_are_removed():
    reads = ConcurrentReads()
    entered, release = asyncio.Event(), asyncio.Event()

    async def operation():
        entered.set()
        await release.wait()
        raise PolicyConflict("read refused")

    leader = asyncio.create_task(reads.run(b"a", operation))
    await entered.wait()
    cancelled = asyncio.create_task(reads.run(b"a", operation))
    follower = asyncio.create_task(reads.run(b"a", operation))
    await asyncio.sleep(0)
    cancelled.cancel()
    with pytest.raises(asyncio.CancelledError):
        await cancelled
    assert not leader.done()
    release.set()
    for task in (leader, follower):
        with pytest.raises(PolicyConflict):
            await task
    assert reads.pending == {}


async def test_inflight_keys_and_waiters_are_bounded_and_leader_cancellation_releases_them():
    reads = ConcurrentReads()
    release = asyncio.Event()

    async def operation():
        await release.wait()

    leaders = [
        asyncio.create_task(reads.run(str(i).encode(), operation)) for i in range(MAX_FLIGHTS)
    ]
    await asyncio.sleep(0)
    with pytest.raises(PolicyConflict):
        await reads.run(b"over-capacity", operation)
    future, _ = reads.pending[b"0"]
    reads.pending[b"0"] = future, MAX_WAITERS
    with pytest.raises(PolicyConflict):
        await reads.run(b"0", operation)
    follower = asyncio.create_task(reads.run(b"1", operation))
    await asyncio.sleep(0)
    for task in leaders:
        task.cancel()
    for task in [*leaders, follower]:
        with pytest.raises(asyncio.CancelledError):
            await task
    assert not reads.pending
