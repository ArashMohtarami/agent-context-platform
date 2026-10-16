"""Concurrent approvals/revocations and single-use invitations on a real replica set."""

import asyncio
from datetime import timedelta

import pytest
from v6_management_fixtures import grant

from app.identity.crypto import AuthRejected
from app.management.access_requests import AccessRequests
from app.management.invitations import Invitations
from app.management.models import Decision, InviteCreate, RequestCreate
from app.models.common import utc_now
from app.security.policy.evaluation import PolicyDenied
from tests.integration.test_v6_membership_lifecycle import last_mail, proposal
from tests.integration.test_v6_policy_fencing import browser

pytestmark = pytest.mark.integration


async def request(h, **changes):
    anchor = await h.engine.anchor("org-a")
    return await AccessRequests(h.engine).create(
        "org-a",
        "member",
        RequestCreate(
            policy=proposal(h, **changes),
            expected_policy_revision=anchor["revision"],
            expires_at=utc_now() + timedelta(hours=1),
        ),
    )


def approve(row):
    return Decision(
        expected_revision=row["revision"],
        expected_policy_revision=row["policy_revision"],
        outcome="approved",
    )


async def test_real_exact_independent_request_approval_is_immutable_and_single_use(management_db):
    h = management_db
    row = await request(h)
    for actor in ("member", "other", "owner"):
        with pytest.raises(PolicyDenied):
            await AccessRequests(h.engine).decide("org-a", actor, row["_id"], approve(row))
    result = await AccessRequests(h.engine).decide("org-a", "lead", row["_id"], approve(row))
    assert result["assignment_id"]
    assert (await h.engine.snapshot("org-a", "member"))["tuples"]
    await h.db.policy_assignments.update_one(
        {"_id": result["assignment_id"]}, {"$set": {"expires_at": utc_now() - timedelta(seconds=1)}}
    )
    assert (await h.engine.snapshot("org-a", "member"))["tuples"] == ()
    with pytest.raises(PolicyDenied):
        await AccessRequests(h.engine).decide("org-a", "lead", row["_id"], approve(row))
    assert (
        await h.db.access_decisions.count_documents({"org_id": "org-a", "request_id": row["_id"]})
        == 1
    )
    assert (await h.db.access_requests.find_one({"_id": row["_id"]}))["revision"] == 2
    async with browser(h, "lead") as (client, headers):
        assert (
            await client.patch(
                "/api/v6/organizations/org-a/access-requests/" + row["_id"] + "/decisions",
                json={"outcome": "denied"},
                headers=headers,
            )
        ).status_code == 405


@pytest.mark.parametrize(
    "change",
    ["request_revision", "policy_revision", "expired", "resource_removed", "team_generation"],
)
async def test_real_approval_rechecks_exact_revision_expiry_and_current_resources(
    management_db, change
):
    h = management_db
    row = await request(h)
    command = approve(row)
    if change == "request_revision":
        command = command.model_copy(update={"expected_revision": 2})
    elif change == "policy_revision":
        command = command.model_copy(
            update={"expected_policy_revision": row["policy_revision"] - 1}
        )
    elif change == "expired":
        await h.db.access_requests.update_one(
            {"_id": row["_id"]}, {"$set": {"expires_at": utc_now() - timedelta(seconds=1)}}
        )
    elif change == "resource_removed":
        await h.db.projects.delete_one({"org_id": "org-a", "project_id": "payment-service"})
    else:
        await h.db.teams.update_one(
            {"org_id": "org-a", "team_id": "alpha"}, {"$inc": {"generation": 1}}
        )
    with pytest.raises(PolicyDenied):
        await AccessRequests(h.engine).decide("org-a", "lead", row["_id"], command)
    assert await h.db.access_decisions.count_documents({"request_id": row["_id"]}) == 0
    assert (await h.db.access_requests.find_one({"_id": row["_id"]}))["status"] == "pending"


async def test_real_wrong_team_foreign_resources_and_lead_escalation_are_refused(management_db):
    h = management_db
    for changes in (
        {"team_id": "beta"},
        {"role": "org_admin"},
        {"tuples": [grant("context:write", ids=("foreign-project",)).model_dump()]},
    ):
        with pytest.raises(PolicyDenied):
            await request(h, **changes)
    assert await h.db.access_requests.count_documents({}) == 0
    await h.db.organization_memberships.update_one(
        {"org_id": "org-a", "user_id": "member"}, {"$set": {"role": "org_admin"}}
    )
    with pytest.raises(PolicyDenied):
        await request(h)
    with pytest.raises(PolicyDenied):
        await Invitations(h.local).create(
            "org-a",
            "lead",
            InviteCreate(
                email="member@example.test",
                display_name="Fictional Member",
                policy=proposal(h),
                expires_at=utc_now() + timedelta(hours=1),
            ),
        )
    await h.db.organization_memberships.update_one(
        {"org_id": "org-a", "user_id": "member"}, {"$set": {"role": "contributor"}}
    )
    row = await request(h)
    async with browser(h, "lead") as (client, headers):
        foreign = await client.post(
            "/api/v6/organizations/org-b/access-requests/" + row["_id"] + "/decisions",
            headers=headers,
            json=approve(row).model_dump(),
        )
        assert foreign.status_code == 403
    async with browser(h, "other") as (client, headers):
        listed = await client.get("/api/v6/organizations/org-a/access-requests", headers=headers)
        assert listed.status_code == 200 and listed.json()["items"] == []


@pytest.mark.parametrize("collection", ["access_decisions", "governance_outbox"])
async def test_real_audit_or_decision_failure_rolls_back_the_entire_approval(
    management_db, monkeypatch, collection
):
    h = management_db
    row = await request(h)
    before = await h.engine.anchor("org-a")
    count = await h.db.policy_assignments.count_documents({})
    audit_count = await h.db.governance_events.count_documents({})
    original = h.store.insert

    async def failing(name, value, session=None):
        if name == collection:
            raise RuntimeError("fictional unavailable durable storage")
        await original(name, value, session)

    monkeypatch.setattr(h.store, "insert", failing)
    with pytest.raises(RuntimeError):
        await AccessRequests(h.engine).decide("org-a", "lead", row["_id"], approve(row))
    assert await h.engine.anchor("org-a") == before
    assert await h.db.policy_assignments.count_documents({}) == count
    assert await h.db.governance_events.count_documents({}) == audit_count
    assert await h.db.access_decisions.count_documents({}) == 0
    assert (await h.db.access_requests.find_one({"_id": row["_id"]}))["status"] == "pending"


async def test_real_parent_revoke_wins_against_concurrent_approval_with_shared_anchor(
    management_db, monkeypatch
):
    h = management_db
    row = await request(h)
    ready, release = asyncio.Event(), asyncio.Event()
    original = h.engine.fence
    observed = []

    async def paused(anchor, tx, *, mutation=False):
        if asyncio.current_task().get_name() == "human-approve":
            observed.append(anchor["revision"])
            if len(observed) == 1:
                ready.set()
                await asyncio.wait_for(release.wait(), 10)
        await original(anchor, tx, mutation=mutation)

    monkeypatch.setattr(h.engine, "fence", paused)
    approving = asyncio.create_task(
        AccessRequests(h.engine).decide("org-a", "lead", row["_id"], approve(row)),
        name="human-approve",
    )
    await asyncio.wait_for(ready.wait(), 10)
    await h.governance.change("org-a", "owner", h.lead["_id"], 1, revoke=True)
    release.set()
    with pytest.raises(PolicyDenied):
        await approving
    assert len(observed) == 2 and observed[1] > observed[0]
    assert await h.db.access_decisions.count_documents({"request_id": row["_id"]}) == 0
    assert (await h.engine.snapshot("org-a", "member"))["tuples"] == ()


async def test_real_two_concurrent_approvals_create_exactly_one_assignment_and_decision(
    management_db,
):
    h = management_db
    row = await request(h)
    before = await h.db.policy_assignments.count_documents({})
    results = await asyncio.gather(
        *(
            AccessRequests(h.engine).decide("org-a", "lead", row["_id"], approve(row))
            for _ in range(2)
        ),
        return_exceptions=True,
    )
    assert sum(isinstance(r, dict) for r in results) == 1
    assert sum(isinstance(r, PolicyDenied) for r in results) == 1
    assert await h.db.policy_assignments.count_documents({}) == before + 1
    assert await h.db.access_decisions.count_documents({"request_id": row["_id"]}) == 1


async def test_real_concurrent_invite_acceptance_consumes_one_challenge_atomically(management_db):
    h = management_db
    receipt = await Invitations(h.local).create(
        "org-a",
        "lead",
        InviteCreate(
            email="member@example.test",
            display_name="Fictional Member",
            policy=proposal(h),
            expires_at=utc_now() + timedelta(hours=1),
        ),
    )
    token = last_mail(h, "management_invite", "member@example.test")["token"]
    user = await h.db.users.find_one({"_id": "member"})
    member = await h.db.organization_memberships.find_one({"org_id": "org-a", "user_id": "member"})
    org = await h.db.organizations.find_one({"org_id": "org-a"})
    issued = await h.local.sessions.issue(
        user, member, org, ["password", "mfa"], utc_now(), utc_now()
    )
    results = await asyncio.gather(
        *(Invitations(h.local).accept(issued.row, token) for _ in range(2)), return_exceptions=True
    )
    assert sum(isinstance(r, dict) for r in results) == 1
    assert all(isinstance(r, dict | PolicyDenied | AuthRejected) for r in results)
    row = await h.db.management_invitations.find_one({"_id": receipt["invitation_id"]})
    assert row["status"] == "accepted" and row["revision"] == 2
    assert await h.db.policy_assignments.count_documents({"_id": row["assignment_id"]}) == 1


async def test_real_parent_revoke_wins_against_invite_claim_without_consuming_secret(
    management_db, monkeypatch
):
    from app.identity.crypto import digest

    h = management_db
    receipt = await Invitations(h.local).create(
        "org-a",
        "lead",
        InviteCreate(
            email="member@example.test",
            display_name="Fictional Member",
            policy=proposal(h),
            expires_at=utc_now() + timedelta(hours=1),
        ),
    )
    token = last_mail(h, "management_invite", "member@example.test")["token"]
    user = await h.db.users.find_one({"_id": "member"})
    member = await h.db.organization_memberships.find_one({"org_id": "org-a", "user_id": "member"})
    org = await h.db.organizations.find_one({"org_id": "org-a"})
    session = await h.local.sessions.issue(
        user, member, org, ["password", "mfa"], utc_now(), utc_now()
    )
    ready, release = asyncio.Event(), asyncio.Event()
    original = h.engine.fence

    async def paused(anchor, tx, *, mutation=False):
        if asyncio.current_task().get_name() == "human-invite-claim" and not ready.is_set():
            ready.set()
            await asyncio.wait_for(release.wait(), 10)
        await original(anchor, tx, mutation=mutation)

    monkeypatch.setattr(h.engine, "fence", paused)
    claiming = asyncio.create_task(
        Invitations(h.local).accept(session.row, token), name="human-invite-claim"
    )
    await asyncio.wait_for(ready.wait(), 10)
    await h.governance.change("org-a", "owner", h.lead["_id"], 1, revoke=True)
    release.set()
    with pytest.raises(PolicyDenied):
        await claiming
    assert (await h.db.management_invitations.find_one({"_id": receipt["invitation_id"]}))[
        "status"
    ] == "pending"
    assert (await h.db.auth_challenges.find_one({"_id": digest("challenge", token)}))[
        "used_at"
    ] is None
    assert await h.db.policy_assignments.count_documents({"subject_id": "member"}) == 0
