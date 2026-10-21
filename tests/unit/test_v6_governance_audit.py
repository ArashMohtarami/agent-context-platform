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
from app.security.tenant import TenantContext, tenant_scope
from app.security.tenant_policy import TenantPolicy, policy_scope


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


@pytest.mark.parametrize("tx", [None, object()], ids=["current-read", "transaction-snapshot"])
async def test_policy_input_join_filters_org_and_private_users_before_selection(tx):
    collection = Mock(aggregate=AsyncMock())
    cursor = Mock(to_list=AsyncMock(side_effect=[[{"_id": "org-a"}], []]))
    collection.aggregate.return_value = cursor
    store = Mock(_collection=Mock(return_value=collection))
    engine = PolicyEngine(store, human_management=True)
    assert await engine.read_inputs("org-a", "$literal-person", tx) == {"_id": "org-a"}
    from app.security.policy.evaluation import PolicyDenied

    with pytest.raises(PolicyDenied):
        await engine.read_inputs("org-a", "$literal-person", tx)
    assert collection.aggregate.await_count == 2  # No authority cache across reads.
    pipeline = collection.aggregate.await_args.args[0]
    assert pipeline[0] == {
        "$match": {
            "_id": "org-a",
            "org_id": "org-a",
            "status": "active",
        }
    }
    joins = {stage["$lookup"]["from"]: stage["$lookup"] for stage in pipeline if "$lookup" in stage}
    domain_joins = {
        "organizations",
        "policy_assignments",
        "teams",
        "team_memberships",
        "organization_memberships",
        "users",
    }
    control_joins = (
        set() if tx is not None else {"_management_policy_control", "_human_management_control"}
    )
    assert set(joins) == domain_joins | control_joins
    for name, join in joins.items():
        if name in control_joins:
            assert join["pipeline"] == [
                {"$match": {"schema_version": 41, "_id": "installation"}},
                {"$limit": 1},
                {"$project": {"_id": 1}},
            ]
            position = next(
                i
                for i, stage in enumerate(pipeline)
                if stage.get("$lookup", {}).get("from") == name
            )
            assert pipeline[position + 1] == {"$match": {join["as"] + ".0._id": "installation"}}
            assert position < next(
                i
                for i, stage in enumerate(pipeline)
                if stage.get("$lookup", {}).get("from") == "users"
            )
            continue
        assert join["pipeline"][-1] == {"$limit": 1001}
        if name != "users":
            assert join["pipeline"][0]["$match"]["org_id"] == "org-a"
    assert joins["users"]["localField"] == "member_rows.user_id"
    assert joins["users"]["foreignField"] == "_id"
    assert joins["organization_memberships"]["localField"] == "policy_subject_ids"
    expression = next(stage["$set"] for stage in pipeline if "$set" in stage)
    assert expression["policy_subject_ids"]["$setUnion"][0] == {
        "$literal": ["$literal-person"],
    }
    assert collection.aggregate.await_args.kwargs == {
        "session": tx,
        "maxTimeMS": 3000,
        "allowDiskUse": False,
    }


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


@pytest.mark.parametrize(
    ("collection", "labels", "outer", "expected"),
    [
        ("research_chunks", ["TransientTransactionError"], None, 2),
        ("research_chunks", ["UnknownTransactionCommitResult"], None, 1),
        (
            "research_chunks",
            ["TransientTransactionError", "UnknownTransactionCommitResult"],
            None,
            1,
        ),
        ("research_chunks", ["TransientTransactionError"], "outer-transaction", 1),
        ("content_items", ["TransientTransactionError"], None, 1),
        ("research_claims", ["TransientTransactionError"], None, 1),
    ],
)
async def test_only_aborted_internal_ingestion_writes_retry_with_new_fences(
    collection, labels, outer, expected
):
    from app.security.policy.hooks import fenced

    sessions = []

    @asynccontextmanager
    async def boundary(policy, session):
        sessions.append(session if session else object())
        yield sessions[-1]

    engine = Mock(domain_fence=boundary)
    policy = TenantPolicy("org-a", "person", (), managed=True, engine=engine)
    write = AsyncMock(
        side_effect=[
            OperationFailure("synthetic abort", details={"errorLabels": labels}),
            "committed",
        ]
    )

    @fenced
    async def mutation(self, **kwargs):
        return await write(kwargs["session"])

    with tenant_scope(TenantContext("org-a")), policy_scope(policy, "research:write"):
        if expected == 2:
            assert await mutation(Mock(collection=collection), session=outer) == "committed"
            assert sessions[0] is not sessions[1]
        else:
            with pytest.raises(OperationFailure):
                await mutation(Mock(collection=collection), session=outer)
    assert write.await_count == expected and len(sessions) == expected


async def test_ingestion_retry_stops_when_fresh_authority_is_denied():
    from app.security.policy.evaluation import PolicyDenied
    from app.security.policy.hooks import fenced

    entered = 0

    @asynccontextmanager
    async def boundary(policy, session):
        nonlocal entered
        entered += 1
        if entered == 2:
            raise PolicyDenied("sponsor was revoked")
        yield object()

    engine = Mock(domain_fence=boundary)
    policy = TenantPolicy("org-a", "person", (), managed=True, engine=engine)
    write = AsyncMock(
        side_effect=OperationFailure(
            "synthetic abort", details={"errorLabels": ["TransientTransactionError"]}
        )
    )

    @fenced
    async def mutation(self, **kwargs):
        return await write(kwargs["session"])

    with (
        tenant_scope(TenantContext("org-a")),
        policy_scope(policy, "research:write"),
        pytest.raises(PolicyDenied),
    ):
        await mutation(Mock(collection="research_chunks"))
    assert entered == 2 and write.await_count == 1


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
