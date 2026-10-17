"""Actual persisted aggregation, attribution, coverage and policy predicates."""

from datetime import timedelta

import pytest
from v6_summary_fixtures import params

from app.management.models import TeamMemberChange
from app.management.teams import Teams
from app.models.common import utc_now
from app.security.policy.evaluation import PolicyDenied
from app.security.tenant import TenantContext, tenant_scope
from app.summaries.coverage import CHECKPOINT_ACTION
from app.summaries.operator import checkpoint
from app.summaries.service import Summaries
from tests.integration.test_v6_policy_fencing import browser

pytestmark = pytest.mark.integration
BASE = "/api/v6/organizations/org-a"


async def test_real_counts_buckets_pages_own_team_org_and_foreign_filters(summary_db):
    h = summary_db
    for subject, scope, team, count in (
        ("owner", "org", None, 6),
        ("other", "org", None, 6),
        ("lead", "team", "alpha", 2),
        ("reviewer", "team", "beta", 1),
        ("member", "own", None, 3),
    ):
        query = h.query.model_copy(
            update={"scope": scope, "team_id": team, "timezone": "Asia/Tehran"}
        )
        async with browser(h, subject) as (client, headers):
            overview = await client.get(BASE + "/overview", params=params(query), headers=headers)
            assert overview.status_code == 200, overview.text
            body = overview.json()
            assert body["observed_count"] == count == sum(x["count"] for x in body["buckets"])
            assert count == sum(body["outcomes"].values())
            assert body["coverage"]["state"] == "partial"
            assert body["coverage"]["watermark"] and body["coverage"]["population"]
            page = await client.get(
                BASE + "/activity", params=params(query) | {"limit": 1}, headers=headers
            )
            assert page.status_code == 200, page.text
            assert page.json()["observed_count"] == count
            seen = page.json()["items"]
            cursor = page.json()["next_cursor"]
            while cursor:
                page = await client.get(
                    BASE + "/activity",
                    params=params(query) | {"limit": 1, "cursor": cursor},
                    headers=headers,
                )
                assert page.status_code == 200, page.text
                seen.extend(page.json()["items"])
                cursor = page.json()["next_cursor"]
            assert len(seen) == count
            assert all(x["event_id"] != h.event_ids["foreign-private-sentinel"] for x in seen)
            foreign = await client.get(
                BASE + "/overview",
                params=params(query) | {"project_id": "foreign-only-project"},
                headers=headers,
            )
            assert foreign.status_code == 200 and foreign.json()["observed_count"] == 0
            assert foreign.json()["buckets"] == []
            wrong = await client.get(
                "/api/v6/organizations/org-b/overview", params=params(query), headers=headers
            )
            assert wrong.status_code == 403 and "observed_count" not in wrong.text
    # Matching IDs exist in both tenants, not merely randomized fixtures.
    assert (
        await h.db.agent_activity.count_documents(
            {"client_id": "retired-agent", "project_id": "payment-service", "org_id": "org-b"}
        )
        == 1
    )


async def test_real_team_moves_do_not_join_current_membership_or_rewrite_history(summary_db):
    h = summary_db
    alpha = h.query.model_copy(update={"scope": "team", "team_id": "alpha"})
    beta = h.query.model_copy(update={"scope": "team", "team_id": "beta"})
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
    async with browser(h, "reviewer") as (client, headers):
        response = await client.get(BASE + "/activity", params=params(beta), headers=headers)
        assert response.status_code == 200, response.text
        assert response.json()["observed_count"] == 1
        assert all(item["team_id"] == "beta" for item in response.json()["items"])
        denied = await client.get(BASE + "/overview", params=params(alpha), headers=headers)
        assert denied.status_code == 403
    async with browser(h, "lead") as (client, headers):
        response = await client.get(BASE + "/activity", params=params(alpha), headers=headers)
        assert response.status_code == 200 and response.json()["observed_count"] == 2
        assert all(item["sponsor_id"] == "member" for item in response.json()["items"])
    async with browser(h, "owner") as (client, headers):
        response = await client.get(BASE + "/activity", params=params(h.query), headers=headers)
        legacy = next(x for x in response.json()["items"] if x["event_id"] == h.event_ids["legacy"])
        assert legacy["attribution"] == "legacy_unattributed"
        assert (
            legacy["actor_kind"] == "unknown"
            and legacy["actor_id"] is legacy["sponsor_id"] is legacy["team_id"] is None
        )


async def test_real_coverage_zero_missing_stale_partial_absent_and_retention(summary_db):
    h = summary_db
    query = h.query.model_copy(
        update={
            "source": "governance",
            "start": utc_now(),
            "end": utc_now() + timedelta(milliseconds=1),
        }
    )
    # Establish a fully empty interval before the declaration, without pretending
    # that a best-effort gateway stream can prove all usage was collected.
    query = query.model_copy(update={"end": utc_now()})
    with tenant_scope(TenantContext("org-a", b"s" * 32)):
        service = Summaries(h.engine)
        missing = await service.read("org-a", "owner", query, overview=True)
        assert missing["observed_count"] == 0 and missing["coverage"]["state"] == "missing"
        declaration = h.declaration.model_copy(update={"source": "governance"})
        await checkpoint(h.engine, "org-a", declaration)
        zero = await service.read("org-a", "owner", query, overview=True)
        assert zero["observed_count"] == 0 and zero["observation_state"] == "no_recorded_events"
        assert zero["coverage"]["state"] == "complete"
        await h.db.governance_events.update_many(
            {"org_id": "org-a", "action": CHECKPOINT_ACTION, "target_id": "governance"},
            {"$set": {"change.through": utc_now() - timedelta(minutes=20)}},
        )
        stale = await service.read("org-a", "owner", query, overview=True)
        assert stale["coverage"]["state"] == "stale"
        await checkpoint(h.engine, "org-a", declaration.model_copy(update={"available": False}))
        unavailable = await service.read("org-a", "owner", query, overview=True)
        assert unavailable["coverage"]["state"] == "unavailable"
        outage_start = utc_now()
        await checkpoint(h.engine, "org-a", declaration.model_copy(update={"available": False}))
        await checkpoint(h.engine, "org-a", declaration)
        outage = query.model_copy(update={"start": outage_start, "end": utc_now()})
        partial = await service.read("org-a", "owner", outage, overview=True)
        assert partial["coverage"]["state"] == "partial" and partial["coverage"]["gaps"]
        floor = h.begin + timedelta(minutes=3)
        await checkpoint(
            h.engine, "org-a", h.declaration.model_copy(update={"retained_from": floor})
        )
        retained = await service.read("org-a", "owner", h.query, overview=True)
        assert retained["observed_count"] == 4
        old = h.query.model_copy(update={"end": floor})
        absent = await service.read("org-a", "owner", old, overview=True)
        assert absent["coverage"]["state"] == "absent_history" and absent["observed_count"] == 0
        assert await h.db.agent_activity.count_documents({"org_id": "org-a"}) == 6
        with pytest.raises(PolicyDenied, match="resurrect"):
            await checkpoint(h.engine, "org-a", h.declaration)


async def test_real_cursor_filter_subject_policy_and_org_binding_and_bounded_inputs(summary_db):
    h = summary_db
    async with browser(h, "owner") as (client, headers):
        response = await client.get(
            BASE + "/activity", params=params(h.query) | {"limit": 1}, headers=headers
        )
        assert response.status_code == 200, response.text
        cursor = response.json()["next_cursor"]
        wrong = await client.get(
            BASE + "/activity",
            params=params(h.query) | {"cursor": cursor, "outcome": "failed"},
            headers=headers,
        )
        assert wrong.status_code == 409
        for changes in (
            {"limit": 101},
            {"timezone": "Fake/Zone"},
            {"start": "2020-01-01T00:00:00Z"},
            {"cursor": "x" * 2049},
        ):
            invalid = await client.get(
                BASE + "/activity", params=params(h.query) | changes, headers=headers
            )
            assert invalid.status_code == 422
    async with browser(h, "other") as (client, headers):
        wrong = await client.get(
            BASE + "/activity", params=params(h.query) | {"cursor": cursor}, headers=headers
        )
        assert wrong.status_code == 409
    with tenant_scope(TenantContext("org-a")):
        await checkpoint(
            h.engine,
            "org-a",
            h.declaration.model_copy(update={"retained_from": h.begin + timedelta(minutes=3)}),
        )
    async with browser(h, "owner") as (client, headers):
        wrong = await client.get(
            BASE + "/activity", params=params(h.query) | {"cursor": cursor}, headers=headers
        )
        assert wrong.status_code == 409


async def test_real_governance_projection_attribution_and_private_body_exclusion(summary_db):
    h = summary_db
    async with h.engine.transaction() as tx:
        await h.engine.audit(
            "org-a",
            "member",
            "team_changed",
            "private-target",
            tx,
            change={
                "prompt": "PRIVATE_PROMPT_SENTINEL",
                "email": "private@example.invalid",
                "response": "PRIVATE_RESPONSE_SENTINEL",
            },
        )
    event = await h.db.governance_events.find_one({"target_id": "private-target"})
    assert event["change"]["_event_v1"] == {
        "verified": True,
        "actor_kind": "human",
        "team_id": "alpha",
    }
    query = h.query.model_copy(update={"source": "governance", "end": utc_now()})
    await h.db.governance_events.insert_one(
        {
            "_id": "legacy-member-event",
            "org_id": "org-a",
            "actor_id": "member",
            "action": "team_changed",
            "target_id": "legacy-target",
            "approval_reference": None,
            "change": {},
            "created_at": h.begin,
        }
    )
    async with browser(h, "owner") as (client, headers):
        page = await client.get(BASE + "/activity", params=params(query), headers=headers)
        assert page.status_code == 200, page.text
        body = page.json()
        assert body["observed_count"] == await h.db.governance_events.count_documents(
            {
                "org_id": "org-a",
                "created_at": {"$gte": query.start, "$lt": query.end},
                "action": {"$not": {"$regex": "^summary\\."}},
            }
        )
        projected = next(x for x in body["items"] if x["event_id"] == event["_id"])
        assert projected["actor_id"] == "member" and projected["team_id"] == "alpha"
        legacy = next(x for x in body["items"] if x["event_id"] == "legacy-member-event")
        assert (
            legacy["attribution"] == "legacy_unattributed"
            and legacy["actor_id"] is legacy["team_id"] is None
        )
        assert all(
            x not in page.text
            for x in (
                "PRIVATE_PROMPT_SENTINEL",
                "PRIVATE_RESPONSE_SENTINEL",
                "private@example.invalid",
                "private-target",
                "approval_reference",
            )
        )
    async with browser(h, "other") as (client, headers):
        response = await client.get(BASE + "/overview", params=params(query), headers=headers)
        assert response.status_code == 403  # aggregate metadata is not audit authority
