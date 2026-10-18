"""Browser fixtures use Phase8 preparation and independently aggregate raw events."""

from datetime import UTC, timedelta
from zoneinfo import ZoneInfo

from app.models.common import utc_now
from app.security.policy.models import Grant
from app.security.tenant import TenantContext, tenant_scope
from app.summaries.models import HistoryGap
from app.summaries.operator import checkpoint
from tests.v6_summary_fixtures import prepare_summary


async def prepare_monitoring(h):
    import copy

    if not hasattr(h, "monitoring_initial_context"):
        h.monitoring_initial_context = {
            k: copy.deepcopy(getattr(h, k))
            for k in ("lead", "sponsor_assignment", "root_generation")
        }
    for k, v in h.monitoring_initial_context.items():
        setattr(h, k, copy.deepcopy(v))
    await prepare_summary(h)
    narrowed = tuple(
        Grant.model_validate(g)
        for g in h.sponsor_assignment["tuples"]
        if g["action"] != "content:write"
    )
    await h.governance.change("org-a", "owner", h.sponsor_assignment["_id"], 1, tuples=narrowed)
    event = await h.db.governance_events.find_one(
        {
            "org_id": "org-a",
            "action": "assignment_narrowed",
            "target_id": h.sponsor_assignment["_id"],
        }
    )
    await h.db.governance_events.update_one(
        {"_id": event["_id"]},
        {
            "$set": {
                "change.prompt": "PRIVATE_MONITOR_PROMPT",
                "change.after.name": "PRIVATE_MONITOR_NAME",
                "change.approval": "PRIVATE_MONITOR_APPROVAL",
            }
        },
    )
    await h.db.governance_events.insert_one(
        {
            "_id": '=HYPERLINK("https://invalid.example")',
            "org_id": "org-a",
            "actor_id": "unknown",
            "action": "team_changed",
            "target_id": "PRIVATE_TARGET",
            "approval_reference": "PRIVATE_APPROVAL",
            "change": {"response_body": "PRIVATE_MONITOR_BODY"},
            "created_at": utc_now() - timedelta(milliseconds=10),
        }
    )
    import asyncio

    await asyncio.sleep(0.002)  # BSON millisecond boundary before exclusive end.
    h.query = h.query.model_copy(update={"end": utc_now()})
    with tenant_scope(TenantContext("org-a")):
        await checkpoint(
            h.engine, "org-a", h.declaration.model_copy(update={"source": "governance"})
        )
    h.monitoring_diff_id = event["_id"]
    h.monitoring_query = h.query
    governance_filter = {
        "org_id": "org-a",
        "created_at": {"$gte": h.query.start, "$lt": h.query.end},
        "action": {"$not": {"$regex": "^summary\\."}},
    }
    governance_count = await h.db.governance_events.count_documents(governance_filter)
    access_decisions = await h.db.governance_events.count_documents(
        {**governance_filter, "action": "access_request_decided"}
    )
    return {
        "org_b_count": await h.db.agent_activity.count_documents(
            {"org_id": "org-b", "created_at": {"$gte": h.query.start, "$lt": h.query.end}}
        ),
        "governance_count": governance_count,
        "access_decisions": access_decisions,
        "query": h.query.model_dump(mode="json"),
        "diff_id": event["_id"],
        "legacy_id": '=HYPERLINK("https://invalid.example")',
        "foreign_event_id": h.event_ids["foreign-private-sentinel"],
        "expected": await raw_expectations(h),
    }


async def raw_expectations(h):
    result = {}
    for scope, extra in (
        ("org", {}),
        (
            "own",
            {
                "sponsor_id": "member",
                "sponsorship_revision": {"$gte": 1},
                "project_id": "payment-service",
            },
        ),
        (
            "alpha",
            {
                "sponsor_team_id": "alpha",
                "sponsorship_revision": {"$gte": 1},
                "project_id": "payment-service",
            },
        ),
        (
            "beta",
            {
                "sponsor_team_id": "beta",
                "sponsorship_revision": {"$gte": 1},
                "project_id": "payment-service",
            },
        ),
    ):
        rows = await h.db.agent_activity.find(
            {"org_id": "org-a", "created_at": {"$gte": h.query.start, "$lt": h.query.end}, **extra}
        ).to_list(None)
        outcomes = {}
        for r in rows:
            outcomes[r["status"]] = outcomes.get(r["status"], 0) + 1
        latency = [r["latency_ms"] for r in rows if r.get("latency_ms") is not None]
        buckets = {}
        for zone in ("UTC", "Asia/Tehran", "America/New_York"):
            grouped = {}
            for row in rows:
                start = (
                    row["created_at"]
                    .astimezone(ZoneInfo(zone))
                    .replace(hour=0, minute=0, second=0, microsecond=0)
                )
                key = int(start.astimezone(UTC).timestamp() * 1000)
                grouped.setdefault(
                    key,
                    {
                        "start_ms": key,
                        "end_ms": int(
                            (start + timedelta(days=1)).astimezone(UTC).timestamp() * 1000
                        ),
                        "count": 0,
                    },
                )["count"] += 1
            buckets[zone] = sorted(grouped.values(), key=lambda x: x["start_ms"])
        result[scope] = {
            "buckets": buckets,
            "count": len(rows),
            "outcomes": outcomes,
            "ids": sorted(str(r["_id"]) for r in rows),
            "samples": len(latency),
            "latency": sum(latency) / len(latency) if latency else None,
        }
    return result


async def monitoring_state(h, state, *, configure_sso=True):
    from app.summaries.coverage import CHECKPOINT_ACTION

    if state == "missing":
        await h.db.governance_events.delete_many(
            {"org_id": "org-a", "action": CHECKPOINT_ACTION, "target_id": "gateway_activity"}
        )
    elif state == "stale":
        await h.db.governance_events.update_many(
            {"org_id": "org-a", "action": CHECKPOINT_ACTION, "target_id": "gateway_activity"},
            {"$set": {"change.through": utc_now() - timedelta(minutes=20)}},
        )
    elif state == "gaps":
        with tenant_scope(TenantContext("org-a")):
            await checkpoint(
                h.engine,
                "org-a",
                h.declaration.model_copy(
                    update={
                        "gaps": (
                            HistoryGap(
                                start=h.query.start, end=h.query.start + timedelta(minutes=1)
                            ),
                        )
                    }
                ),
            )
    elif state == "org-b":
        from app.identity.models import MANAGEMENT_ACTIONS
        from app.management.memberships import Memberships
        from app.management.models import RootApproval
        from app.security.policy.models import Selector

        now = utc_now()
        caps = tuple(
            Grant(action=a, resource_kind="organization", selector="include", ids=["org-b"])
            for a in ("activity:read:org", "audit:read:org", "reports:export")
        )
        bootstrap = [
            Grant(action=a, resource_kind="project_directory", selector="all_in_org").model_dump()
            for a in ("projects:read", "projects:write")
        ]
        await h.db.organization_memberships.update_one(
            {"org_id": "org-b", "user_id": "member"},
            {
                "$set": {
                    "role": "org_owner",
                    "bootstrap_owner": True,
                    "bootstrap_intent": {
                        "state": "pending_phase5",
                        "approval_reference": "fictional-explicit-operator-org-b",
                        "root_delegation": [g.model_dump() for g in caps],
                        "bootstrap_policy": bootstrap,
                    },
                }
            },
        )
        with tenant_scope(TenantContext("org-b")):
            # Phase7's fixture already installed this anchor to retire legacy
            # keys. Preserve it and those revocations. Prepare an empty explicit
            # host-reviewed test root; actual metadata grants are issued by the
            # existing transactional operator approval below, never a role bypass.
            member = await h.db.organization_memberships.find_one(
                {"org_id": "org-b", "user_id": "member"}
            )
            assert await h.db.policy_assignments.count_documents({"org_id": "org-b"}) == 0
            row = h.governance.assignment(
                "fixture-b-metadata-root",
                "org-b",
                member,
                "installation_operator",
                (),
                caps,
                Selector(),
                ("executive", "auditor"),
                1,
                now + timedelta(days=1),
            )
            async with h.engine.transaction() as tx:
                anchor = await h.engine.anchor("org-b", tx)
                await h.engine.fence(anchor, tx, mutation=True)
                await h.store.insert("policy_assignments", row, tx)
                await h.engine.audit(
                    "org-b",
                    "installation_operator",
                    "human_management_root_approved",
                    row["_id"],
                    tx,
                    approval="fictional-empty-root-preparation",
                    change={"after": row},
                )
            await Memberships(h.engine).operator_approve_root(
                "org-b",
                "member",
                RootApproval(
                    tuples=tuple(
                        Grant.model_validate(g)
                        for g in row["tuples"]
                        if g["action"] in MANAGEMENT_ACTIONS
                    )
                    + caps,
                    ceiling=caps,
                    targets=Selector(),
                    allowed_roles=("executive", "auditor"),
                    expires_at=now + timedelta(days=1),
                ),
                "fictional-independent-org-b-monitoring-approval",
            )
            await checkpoint(
                h.engine,
                "org-b",
                h.declaration.model_copy(update={"approval_reference": "fictional-org-b-stream"}),
            )
        if configure_sso:
            await h.local.sso.configure(
                "org-b", "provider-b", "sso_preferred", "fictional-org-b-local-mfa-fixture"
            )
    elif state == "auditor":
        from app.security.policy.models import Issue
        from tests.v6_summary_fixtures import grant

        h.monitoring_auditor = await h.governance.issue(
            "org-a",
            "owner",
            Issue(
                subject_id="other",
                role="auditor",
                parent_id=h.root,
                expected_parent_generation=4,
                tuples=(
                    grant("activity:read:org"),
                    grant("audit:read:org"),
                    grant("reports:export"),
                ),
                expires_at=utc_now() + timedelta(days=1),
            ),
        )
    elif state == "revoke-auditor":
        await h.governance.change("org-a", "owner", h.monitoring_auditor["_id"], 1, revoke=True)
    elif state == "revoke":
        await h.governance.change("org-a", "owner", h.executive["_id"], 1, revoke=True)
    elif state == "expire-report":
        from app.summaries.coverage import REPORT_ACTION

        await h.db.governance_events.update_many(
            {"org_id": "org-a", "action": REPORT_ACTION},
            {"$set": {"change.expires_at": utc_now() - timedelta(seconds=1)}},
        )
    elif state == "move":
        from app.management.models import TeamMemberChange
        from app.management.teams import Teams

        await Teams(h.engine).change_member(
            "org-a",
            "owner",
            "alpha",
            "member",
            TeamMemberChange(expected_revision=1, expected_team_revision=1, status="revoked"),
        )
        await Teams(h.engine).change_member(
            "org-a",
            "owner",
            "beta",
            "member",
            TeamMemberChange(expected_revision=0, expected_team_revision=1, status="active"),
        )
    else:
        raise ValueError("unknown bounded monitoring fixture operation")
