"""Transaction boundaries, bounded retry, and complete storage/write-hook coverage."""

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, Mock

import pytest
from pymongo.errors import OperationFailure

from app.db.migrations.runner import MIGRATIONS
from app.db.tenant_collection import TenantCollection
from app.identity.repository import COLLECTION_OWNERSHIP, IdentityStore
from app.security.policy.engine import PolicyEngine
from app.security.policy.evaluation import PolicyConflict
from app.security.policy.governance import Governance
from app.security.policy.inventory import COLLECTION_OWNERSHIP as POLICY_OWNERSHIP
from app.security.policy.schema import NEW_VALIDATORS


def test_new_storage_is_classified_strict_and_migration_allocated_from_actual_head():
    assert set(POLICY_OWNERSHIP) == set(NEW_VALIDATORS) <= set(COLLECTION_OWNERSHIP)
    assert [m.version for m in MIGRATIONS][-4:] == [39, 40, 41, 42]
    for validation in NEW_VALIDATORS.values():
        assert validation["$jsonSchema"]["additionalProperties"] is False
    with pytest.raises(ValueError, match="unclassified"):
        IdentityStore(Mock())._collection("unclassified_authority")
    for name in (
        "insert_one",
        "insert_many",
        "update_one",
        "find_one_and_update",
        "delete_one",
        "delete_many",
    ):
        assert hasattr(getattr(TenantCollection, name), "__wrapped__"), name


async def test_governance_audit_and_outbox_use_the_same_transaction_and_fail_closed():
    store = Mock(insert=AsyncMock())
    engine = PolicyEngine(store)
    tx = object()
    await engine.audit("org-a", "issuer", "assignment_issued", "grant", tx)
    calls = store.insert.await_args_list
    assert [call.args[0] for call in calls] == ["governance_events", "governance_outbox"]
    assert calls[0].args[1]["_id"] == calls[1].args[1]["event_id"]
    assert all(call.args[2] is tx for call in calls)
    store.insert.side_effect = RuntimeError("audit unavailable")
    with pytest.raises(RuntimeError):
        await engine.audit("org-a", "issuer", "assignment_issued", "grant", tx)


async def test_anchor_cas_changes_a_field_and_checks_evaluated_org_revision():
    store = Mock(update=AsyncMock(return_value={"fence": 2}))
    engine = PolicyEngine(store)
    anchor = {"_id": "org-a", "org_id": "org-a", "revision": 9}
    await engine.fence(anchor, "tx", mutation=True)
    args = store.update.await_args.args
    assert args[1] == {"_id": "org-a", "org_id": "org-a", "revision": 9, "status": "active"}
    assert args[2] == {"$inc": {"fence": 1, "revision": 1}}
    store.update.return_value = None
    with pytest.raises(PolicyConflict):
        await engine.fence(anchor, "tx")


async def test_transient_governance_retry_rereads_policy_not_a_saved_snapshot():
    engine = Mock(
        store=Mock(),
        snapshot=AsyncMock(side_effect=[{"anchor": {"revision": 1}}, {"anchor": {"revision": 2}}]),
        fence=AsyncMock(),
    )
    exits = []

    @asynccontextmanager
    async def transaction():
        try:
            yield "tx"
        except Exception:
            exits.append("abort")
            raise
        else:
            exits.append("commit")

    engine.transaction = transaction
    error = OperationFailure(
        "test conflict", details={"errorLabels": ["TransientTransactionError"]}
    )
    operation = AsyncMock(side_effect=[error, {"ok": True}])
    assert await Governance(engine).mutate("org-a", "issuer", operation) == {"ok": True}
    assert exits == ["abort", "commit"]
    assert engine.snapshot.await_count == 2
    assert operation.await_args_list[1].args[0]["anchor"]["revision"] == 2


async def test_api_startup_cannot_silently_perform_the_stopped_writer_cutover():
    from app.db.migrations.runner import run_migrations

    class History:
        async def create_index(self, *args, **kwargs):
            return None

        def find(self, *args, **kwargs):
            async def rows():
                for version in range(1, 40):
                    yield {"version": version}

            return rows()

    with pytest.raises(RuntimeError, match="stopped-writer"):
        await run_migrations(
            {"_schema_migrations": History()},
            tenant_foundation=True,
            tenant_isolation=True,
            local_identity=True,
            sso=True,
            management_policy=True,
        )
