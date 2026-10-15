"""Local password work, purpose-bound challenges and pending bootstrap contracts."""

import asyncio
import threading
from unittest.mock import AsyncMock, Mock

import pytest
from argon2 import Type, extract_parameters
from pydantic import ValidationError

from app.identity.crypto import (
    AuthBusy,
    AuthRejected,
    PasswordWork,
    SeedVault,
    digest,
    random_token,
)
from app.identity.models import MANAGEMENT_ACTIONS, LoginInput, RootTuple
from app.identity.service import LocalIdentity, local_subject, member_dto


async def test_passwords_are_argon2id_with_explicit_parameters_and_random_salts():
    work = PasswordWork()
    try:
        one = await work.hash("test-only-long-passphrase")
        two = await work.hash("test-only-long-passphrase")
        parameters = extract_parameters(one)
        assert parameters.type is Type.ID
        assert (parameters.memory_cost, parameters.time_cost, parameters.parallelism) == (
            65536,
            3,
            4,
        )
        assert (parameters.hash_len, parameters.salt_len) == (32, 16)
        assert one != two and "test-only-long-passphrase" not in one
        assert await work.verify(one, "test-only-long-passphrase")
        assert not await work.verify(one, "wrong-password")
        assert not await work.verify(None, "test-only-long-passphrase")
        with pytest.raises(AuthRejected):
            await work.hash("short")
        with pytest.raises(AuthRejected):
            await work.hash("x" * 1025)
    finally:
        work.close()


async def test_hash_concurrency_is_fail_fast_and_cancellation_keeps_a_live_slot():
    work = PasswordWork()
    release = threading.Event()

    def bounded_job():
        release.wait(timeout=5)
        return True

    jobs = [asyncio.create_task(work._work(bounded_job)) for _ in range(4)]
    try:
        await asyncio.sleep(0.05)
        with pytest.raises(AuthBusy):
            await work._work(bounded_job)
        jobs[0].cancel()
        await asyncio.sleep(0)
        with pytest.raises(AuthBusy):
            await work._work(bounded_job)
    finally:
        release.set()
        await asyncio.gather(*jobs, return_exceptions=True)
        await asyncio.sleep(0.05)
        work.close()
    assert work._active == 0


async def test_challenge_consume_is_purpose_browser_expiry_and_single_use_bound():
    store = Mock()
    store.update = AsyncMock(return_value=None)
    service = LocalIdentity(store, SeedVault("test", {"test": b"x" * 32}), Mock())
    token = random_token()
    try:
        with pytest.raises(AuthRejected):
            await service.consume(token, "verify", Mock(), browser_id="browser-hash")
        name, query, change, _ = store.update.call_args.args
        assert name == "auth_challenges"
        assert query["_id"] == digest("challenge", token)
        assert query["purpose"] == "verify" and query["used_at"] is None
        assert query["browser_id"] == "browser-hash"
        assert "$gt" in query["expires_at"] and change["$set"]["used_at"]
        assert token not in repr(store.update.call_args)
        assert digest("session", token) != digest("challenge", token)
    finally:
        service.passwords.close()


def test_private_login_identity_is_distinct_from_minimal_member_dto():
    assert local_subject(" Alice@Example.TEST ") == "alice@example.test"
    with pytest.raises(AuthRejected):
        local_subject("missing-domain")
    member = {
        "org_id": "org-a",
        "display_name": "Alice",
        "role": "contributor",
        "status": "active",
        "user_id": "private-user",
        "email": "private@example.test",
        "factors": ["private"],
        "other_orgs": ["org-b"],
    }
    assert set(member_dto(member)) == {"org_id", "display_name", "role", "status"}
    assert "private" not in repr(member_dto(member))
    payload = LoginInput(
        email="alice@example.test", password="private-long-password", org_id="org-a"
    )
    assert "private-long-password" not in repr(payload)


def test_pending_root_tuples_are_explicit_and_do_not_create_effective_grants():
    assert len(MANAGEMENT_ACTIONS) == 28
    tuple_ = RootTuple(
        action="projects:read", resource_kind="project", selector="include", ids=["same-project"]
    )
    assert tuple_.model_dump()["ids"] == ["same-project"]
    for change in (
        {"ids": []},
        {"ids": ["x", "x"]},
        {"action": "*"},
        {"selector": "all_in_org"},
        {"conditions": {"script": "allow"}},
    ):
        with pytest.raises(ValidationError):
            RootTuple.model_validate({**tuple_.model_dump(), **change})


def test_private_collection_inventory_and_schema_are_complete():
    from app.db.migrations.runner import MIGRATIONS
    from app.identity.repository import COLLECTION_OWNERSHIP, IdentityStore
    from app.identity.schema import VALIDATORS

    assert set(COLLECTION_OWNERSHIP) == set(VALIDATORS)
    assert MIGRATIONS[-1].version == 38
    assert not {"permission_assignments", "delegations", "teams"} & set(VALIDATORS)
    with pytest.raises(ValueError, match="unclassified"):
        IdentityStore(Mock())._collection("permission_assignments")
