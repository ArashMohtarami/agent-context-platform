"""Real MongoDB invitations, tenant directory and human offboarding journeys."""

import json
from datetime import timedelta

import httpx
import pyotp
import pytest
from pymongo.errors import OperationFailure
from v6_management_fixtures import grant

from app.db.migrations.v041_human_management import HumanManagementMigration
from app.identity.browser import SESSION_COOKIE
from app.identity.crypto import AuthRejected, digest
from app.management.directory import Directory
from app.management.invitations import Invitations
from app.management.memberships import Memberships
from app.management.models import InviteCreate, ReviewedPolicy, TeamChange, TeamMemberChange
from app.management.teams import Teams
from app.models.common import utc_now
from app.models.job_authority import JobAuthority
from app.security.job_authority import JobAuthorityError, MongoJobAuthorityChecker
from app.security.policy.engine import PolicyEngine
from app.security.policy.evaluation import PolicyDenied
from app.security.tenant import TenantContext, tenant_scope
from tests.integration.test_v6_policy_fencing import browser

pytestmark = pytest.mark.integration
ORIGIN = "https://identity.example"
PASSWORD = "Fictional-phase-six-password-927!"


def proposal(h, **changes):
    return ReviewedPolicy.model_validate(
        {
            "parent_id": h.lead["_id"],
            "expected_parent_generation": 1,
            "team_id": "alpha",
            "expected_team_generation": 1,
            "tuples": [grant("context:read").model_dump()],
            "expires_at": utc_now() + timedelta(days=1),
            **changes,
        }
    )


def last_mail(h, purpose, recipient):
    return next(
        json.loads(p.read_text())
        for p in sorted(
            h.local.mail.directory.glob("*.json"), key=lambda p: p.stat().st_mtime_ns, reverse=True
        )
        if json.loads(p.read_text())["purpose"] == purpose
        and json.loads(p.read_text())["recipient"] == recipient
    )


async def invite_new(h, email="fictional-new@example.test"):
    response = await Invitations(h.local).create(
        "org-a",
        "lead",
        InviteCreate(
            email=email,
            display_name="Fictional New Person",
            policy=proposal(h),
            expires_at=utc_now() + timedelta(hours=1),
        ),
    )
    return response, last_mail(h, "invite", email)["token"]


async def verify_new(h, token, email="fictional-new@example.test"):
    await h.local.accept_invite(token, PASSWORD, "fictional-browser")
    verify = last_mail(h, "verify", email)["token"]
    await h.local.verify(verify, "fictional-browser")
    identity = await h.db.user_identities.find_one({"provider": "local", "subject": email})
    return identity["user_id"]


async def test_real_invite_local_enrollment_request_approval_and_offboard_journey(management_db):
    from app.management.access_requests import AccessRequests
    from app.management.models import Decision, RequestCreate

    h = management_db
    async with browser(h, "lead") as (client, headers):
        response = await client.post(
            "/api/v6/organizations/org-a/invitations",
            headers=headers,
            json=InviteCreate(
                email="fictional-new@example.test",
                display_name="Fictional New Person",
                policy=proposal(h),
                expires_at=utc_now() + timedelta(hours=1),
            ).model_dump(mode="json"),
        )
        assert response.status_code == 201, response.text
        assert "token" not in response.text and "email" not in response.text
    token = last_mail(h, "invite", "fictional-new@example.test")["token"]
    invitation = await h.db.management_invitations.find_one(
        {"_id": response.json()["invitation_id"]}
    )
    assert invitation["token_hash"] == digest("challenge", token)
    user_id = await verify_new(h, token)
    assert (await h.engine.snapshot("org-a", user_id))["tuples"]
    with pytest.raises(AuthRejected):
        await h.local.accept_invite(token, PASSWORD, "fictional-browser")
    login, needed = await h.local.login(
        "fictional-new@example.test", PASSWORD, "org-a", "fictional-login"
    )
    assert not needed
    issued = await h.local.finish_login(login, "fictional-login", "")
    seed, _ = await h.local.begin_mfa(issued.row)
    issued, _ = await h.local.confirm_mfa(issued.row, pyotp.TOTP(seed).now())
    assert await h.local.sessions.authenticate(issued.token)
    from app.identity.crypto import csrf

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=h.app),
        base_url=ORIGIN,
        cookies={SESSION_COOKIE: issued.token},
    ) as client:
        headers = {
            "Origin": ORIGIN,
            "X-CSRF-Token": csrf(issued.token),
            "X-Session-Context": h.local.policy_context(issued.row)["context_id"],
        }
        explanation = await client.get("/api/v6/me/effective-access", headers=headers)
        assert explanation.status_code == 200, explanation.text
        parent = explanation.json()["assignments"][0]["lineage"][1]
        command = RequestCreate(
            policy=proposal(
                h,
                parent_id=parent["id"],
                expected_parent_generation=parent["generation"],
                tuples=[grant("context:write").model_dump()],
            ),
            expected_policy_revision=explanation.json()["policy_revision"],
            expires_at=utc_now() + timedelta(hours=1),
        )
        submitted = await client.post(
            "/api/v6/organizations/org-a/access-requests",
            headers=headers,
            json=command.model_dump(mode="json"),
        )
        assert submitted.status_code == 201, submitted.text
        request = submitted.json()
    decision = await AccessRequests(h.engine).decide(
        "org-a",
        "lead",
        request["_id"],
        Decision(
            expected_revision=1,
            expected_policy_revision=request["policy_revision"],
            outcome="approved",
        ),
    )
    assert decision["assignment_id"]
    member = await h.db.organization_memberships.find_one({"org_id": "org-a", "user_id": user_id})
    preserved = await h.db.project_contexts.count_documents({"org_id": "org-a"})
    await Memberships(h.engine).change_status(
        "org-a", "owner", user_id, member["revision"], "revoked"
    )
    with pytest.raises(AuthRejected):
        await h.local.sessions.authenticate(issued.token)
    await Memberships(h.engine).change_status(
        "org-a", "owner", user_id, member["revision"] + 1, "active"
    )
    assert (await h.engine.snapshot("org-a", user_id))["tuples"] == ()
    assert await h.db.project_contexts.count_documents({"org_id": "org-a"}) == preserved
    assert await h.db.access_decisions.count_documents({"request_id": request["_id"]}) == 1


async def test_real_team_directory_pagination_dtos_and_foreign_context_fail_closed(management_db):
    h = management_db
    async with browser(h, "lead") as (client, headers):
        response = await client.get("/api/v6/organizations/org-a/members?limit=1", headers=headers)
        assert response.status_code == 200
        page = response.json()
        assert page["next_cursor"] and {i["user_id"] for i in page["items"]} <= {"lead", "member"}
        text = response.text
        assert all(
            k not in text for k in ("@example", "bootstrap_intent", "auth_epoch", "verified_at")
        )
        next_page = await client.get(
            "/api/v6/organizations/org-a/members",
            params={"limit": 1, "cursor": page["next_cursor"]},
            headers=headers,
        )
        assert next_page.status_code == 200
        assert page["items"][0]["user_id"] != next_page.json()["items"][0]["user_id"]
        assert (
            await client.get("/api/v6/organizations/org-b/members", headers=headers)
        ).status_code == 403
        assert (
            await client.get("/api/v6/organizations/org-a/teams/beta/members", headers=headers)
        ).status_code == 403
        teams = await client.get("/api/v6/organizations/org-a/teams", headers=headers)
        assert [t["team_id"] for t in teams.json()["items"]] == ["alpha"]
        await Teams(h.engine).change_team(
            "org-a",
            "owner",
            "alpha",
            TeamChange(expected_revision=1, name="Fictional renamed team"),
        )
        stale = await client.get(
            "/api/v6/organizations/org-a/members",
            params={"cursor": page["next_cursor"]},
            headers=headers,
        )
        assert stale.status_code == 409
    with tenant_scope(TenantContext("org-a", b"d" * 32)):
        result = await Directory(h.engine).members("org-a", "lead", prefix="other")
        assert result["items"] == []


@pytest.mark.parametrize(
    "failure", ["revoked_parent", "expired_invitation", "suspended_team", "generation_changed"]
)
async def test_real_pending_invitation_cannot_outlive_issuer_or_team_authority(
    management_db, failure
):
    h = management_db
    receipt, token = await invite_new(h)
    await h.local.accept_invite(token, PASSWORD, "fictional-browser")
    verify = last_mail(h, "verify", "fictional-new@example.test")["token"]
    if failure == "revoked_parent":
        await h.governance.change("org-a", "owner", h.lead["_id"], 1, revoke=True)
    elif failure == "expired_invitation":
        await h.db.management_invitations.update_one(
            {"_id": receipt["invitation_id"]},
            {"$set": {"expires_at": utc_now() - timedelta(seconds=1)}},
        )
    elif failure == "suspended_team":
        await Teams(h.engine).change_team(
            "org-a", "owner", "alpha", TeamChange(expected_revision=1, status="suspended")
        )
    else:
        await h.db.policy_assignments.update_one(
            {"_id": h.lead["_id"]}, {"$inc": {"generation": 1}}
        )
    with pytest.raises(PolicyDenied):
        await h.local.verify(verify, "fictional-browser")
    row = await h.db.management_invitations.find_one({"_id": receipt["invitation_id"]})
    assert row["status"] == "pending"
    member = await h.db.organization_memberships.find_one({"_id": row["membership_id"]})
    assert member["status"] == "verification_pending"
    assert await h.db.policy_assignments.count_documents({"subject_id": member["user_id"]}) == 0
    assert (await h.db.auth_challenges.find_one({"_id": digest("challenge", verify)}))[
        "used_at"
    ] is None


async def test_real_wrong_person_overbroad_and_wrong_team_invites_do_not_create_authority(
    management_db,
):
    h = management_db
    before = await h.db.users.count_documents({})
    for changes in (
        {"team_id": "beta"},
        {"tuples": [grant("context:write", ids=("foreign-project",)).model_dump()]},
        {"role": "org_admin"},
    ):
        with pytest.raises(PolicyDenied):
            await Invitations(h.local).create(
                "org-a",
                "lead",
                InviteCreate(
                    email="fictional-wrong@example.test",
                    display_name="Fictional Wrong",
                    policy=proposal(h, **changes),
                    expires_at=utc_now() + timedelta(hours=1),
                ),
            )
    assert await h.db.users.count_documents({}) == before
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
    async with browser(h, "other") as (client, headers):
        response = await client.post(
            "/api/v6/me/invitations/accept", json={"token": token}, headers=headers
        )
        assert response.status_code == 403
    assert (await h.db.management_invitations.find_one({"_id": receipt["invitation_id"]}))[
        "status"
    ] == "pending"
    assert (await h.db.auth_challenges.find_one({"_id": digest("challenge", token)}))[
        "used_at"
    ] is None
    async with browser(h, "member") as (client, headers):
        assert (
            await client.post(
                "/api/v6/me/invitations/accept", json={"token": token}, headers=headers
            )
        ).status_code == 200
        assert (
            await client.post(
                "/api/v6/me/invitations/accept", json={"token": token}, headers=headers
            )
        ).status_code in {401, 403}


async def test_real_membership_suspend_is_org_local_and_owner_handover_remains_explicit(
    management_db,
):
    h = management_db
    member = await h.db.organization_memberships.find_one({"org_id": "org-a", "user_id": "member"})
    await h.db.organization_memberships.insert_one(
        {**member, "_id": "member-other-org", "org_id": "org-b"}
    )
    user = await h.db.users.find_one({"_id": "member"})
    org_b = await h.db.organizations.find_one({"org_id": "org-b"})
    session_b = await h.local.sessions.issue(
        user,
        {**member, "_id": "member-other-org", "org_id": "org-b"},
        org_b,
        ["password", "mfa"],
        utc_now(),
        utc_now(),
    )
    async with browser(h, "member") as (client, headers):
        await Memberships(h.engine).change_status(
            "org-a", "owner", "member", member["revision"], "suspended"
        )
        assert (await client.get("/api/v6/me")).status_code == 401
        assert await h.local.sessions.authenticate(session_b.token)
    with pytest.raises(PolicyDenied):
        await Memberships(h.engine).change_status("org-a", "owner", "owner", 1, "revoked")
    async with browser(h, "owner") as (client, headers):
        offer = await client.post(
            "/api/v6/organizations/org-a/ownership-transfer",
            json={"target_id": "other"},
            headers=headers,
        )
        assert offer.status_code == 201
    async with browser(h, "other") as (client, headers):
        accepted = await client.post(
            "/api/v6/organizations/org-a/ownership-transfer/" + offer.json()["_id"] + "/accept",
            headers=headers,
        )
        assert accepted.status_code == 200, accepted.text
        assert (await client.get("/api/v6/me")).status_code == 401
    assert (await h.db.organization_memberships.find_one({"org_id": "org-a", "user_id": "other"}))[
        "role"
    ] == "org_owner"
    assert (await h.db.organization_memberships.find_one({"org_id": "org-a", "user_id": "owner"}))[
        "role"
    ] == "contributor"


async def test_real_team_suspend_reinstate_never_resurrects_old_grants_or_sessions(management_db):
    h = management_db
    async with browser(h, "lead") as (client, headers):
        await Teams(h.engine).change_team(
            "org-a", "owner", "alpha", TeamChange(expected_revision=1, status="suspended")
        )
        assert (await client.get("/api/v6/me")).status_code == 401
    assert (await h.engine.snapshot("org-a", "lead"))["tuples"] == ()
    await Teams(h.engine).change_team(
        "org-a", "owner", "alpha", TeamChange(expected_revision=2, status="active")
    )
    assert (await h.engine.snapshot("org-a", "lead"))["tuples"] == ()
    assert (await h.engine.snapshot("org-a", "other"))["tuples"]
    with pytest.raises(PolicyDenied):
        await Teams(h.engine).change_member(
            "org-a",
            "lead",
            "beta",
            "reviewer",
            TeamMemberChange(expected_revision=1, expected_team_revision=1, status="suspended"),
        )


async def test_real_human_pending_job_and_unknown_machine_sponsorship_fail_closed(management_db):
    h = management_db
    from app.security.policy.models import Issue

    source = await h.db.research_sources.find_one({"org_id": "org-a"})
    source_project = source["research_project_id"]
    research_grants = (
        grant("research:write", "research_project", (source_project,)),
        grant("research:write", ids=tuple(source["authorization"]["project_ids"])),
    )
    await h.governance.issue(
        "org-a",
        "owner",
        Issue(
            subject_id="member",
            parent_id=h.root,
            expected_parent_generation=2,
            tuples=research_grants,
            team_id="alpha",
            expected_team_generation=1,
            expires_at=utc_now() + timedelta(days=1),
        ),
    )
    user = await h.db.users.find_one({"_id": "member"})
    member = await h.db.organization_memberships.find_one({"org_id": "org-a", "user_id": "member"})
    org = await h.db.organizations.find_one({"org_id": "org-a"})
    issued = await h.local.sessions.issue(
        user, member, org, ["password", "mfa"], utc_now(), utc_now()
    )
    authority = JobAuthority(
        org_id="org-a",
        key_id=None,
        client_id=None,
        project_ids=tuple(source["authorization"]["project_ids"]),
        profile_ids=(),
        research_project_id=source_project,
        source_id=source["source_id"],
        worker_schema_version=41,
        subject_kind="human",
        user_id="member",
        membership_revision=member["revision"],
        session_id=issued.row["public_id"],
    )
    checker = MongoJobAuthorityChecker(
        h.db, worker_schema_version=41, human_sessions=h.local.sessions
    )
    with tenant_scope(TenantContext("org-a")):
        await checker.check(authority)
        await Memberships(h.engine).change_status(
            "org-a", "owner", "member", member["revision"], "suspended"
        )
        with pytest.raises(JobAuthorityError):
            await checker.check(authority)
        await Memberships(h.engine).change_status(
            "org-a", "owner", "member", member["revision"] + 1, "active"
        )
        with pytest.raises(JobAuthorityError):
            await checker.check(authority)
    key = await h.db.api_keys.find_one({"org_id": "org-a"})
    from app.models.api_key import ApiKeyDocument
    from app.services.auth_service import principal_of

    record = ApiKeyDocument.model_validate(key, context={"from_storage": True})
    with pytest.raises(PolicyDenied, match="sponsorship"):
        await h.engine.credential(principal_of(record, None))


async def test_real_schema41_validators_resume_and_old_policy_workers_are_refused(management_db):
    h = management_db
    await HumanManagementMigration().up(h.db)
    with pytest.raises(PolicyDenied):
        await PolicyEngine(h.store).enabled()
    with pytest.raises(OperationFailure):
        await h.db.teams.insert_one({"_id": "bad", "org_id": "org-a", "team_id": "bad"})
    async with browser(h, "owner") as (client, headers):
        h.app.state.settings.v6_human_management = False
        assert (await client.get("/api/v6/me")).status_code == 503
    assert (await h.db["_tenant_control"].find_one({"_id": "installation"}))[
        "worker_schema_versions"
    ] == [41]


async def test_real_shared_person_effective_sources_and_matching_foreign_team_stay_private(
    management_db,
):
    from app.security.policy.models import Issue

    h = management_db
    now = utc_now()
    await h.db.teams.insert_one(
        {
            "_id": "foreign-alpha",
            "org_id": "org-b",
            "team_id": "alpha",
            "name": "PRIVATE_FOREIGN_TEAM",
            "status": "active",
            "revision": 1,
            "generation": 1,
            "created_at": now,
        }
    )
    await h.db.users.insert_one(
        {
            "_id": "foreign-person",
            "display_name": "PRIVATE_GLOBAL_PERSON",
            "status": "active",
            "auth_epoch": 1,
            "fence": 0,
            "created_at": now,
        }
    )
    original = await h.db.organization_memberships.find_one(
        {"org_id": "org-a", "user_id": "member"}
    )
    await h.db.organization_memberships.insert_one(
        {
            **original,
            "_id": "foreign-member",
            "org_id": "org-b",
            "user_id": "foreign-person",
            "display_name": "PRIVATE_FOREIGN_PERSON",
        }
    )
    with pytest.raises(PolicyDenied):
        await Teams(h.engine).change_member(
            "org-a",
            "owner",
            "alpha",
            "foreign-person",
            TeamMemberChange(expected_revision=0, expected_team_revision=1, status="active"),
        )
    await Teams(h.engine).change_member(
        "org-a",
        "owner",
        "beta",
        "member",
        TeamMemberChange(expected_revision=0, expected_team_revision=1, status="active"),
    )
    alpha = await h.governance.issue("org-a", "lead", proposal(h).issue("member"))
    beta = await h.governance.issue(
        "org-a",
        "owner",
        Issue(
            subject_id="member",
            parent_id=h.root,
            expected_parent_generation=2,
            tuples=(grant("context:write"),),
            expires_at=now + timedelta(days=1),
            team_id="beta",
            expected_team_generation=1,
        ),
    )
    async with browser(h, "lead") as (client, headers):
        listed = await client.get("/api/v6/organizations/org-a/members", headers=headers)
        assert "PRIVATE_" not in listed.text
        response = await client.get(
            "/api/v6/organizations/org-a/members/member/effective-access", headers=headers
        )
        assert response.status_code == 200
        assert beta["_id"] not in response.text and "context:write" not in response.text
        assert alpha["_id"] in response.text and "lineage" not in response.text
        assignments = await client.get(
            "/api/v6/organizations/org-a/assignments?subject_id=member", headers=headers
        )
        assert assignments.status_code == 200 and beta["_id"] not in assignments.text
        # An organization-wide suspension would reach beta outside this lead's
        # envelope. The scoped team edge remains independently manageable.
        denied = await client.post(
            "/api/v6/organizations/org-a/members/member/suspend",
            json={"expected_revision": 1},
            headers=headers,
        )
        assert denied.status_code == 403
        changed = await client.post(
            "/api/v6/organizations/org-a/teams/alpha/members/member",
            json={"expected_revision": 1, "expected_team_revision": 1, "status": "suspended"},
            headers=headers,
        )
        assert changed.status_code == 200
        restored = await client.post(
            "/api/v6/organizations/org-a/teams/alpha/members/member",
            json={"expected_revision": 2, "expected_team_revision": 1, "status": "active"},
            headers=headers,
        )
        assert restored.status_code == 200
    effective = await h.engine.snapshot("org-a", "member")
    assert [e["assignment_id"] for e in effective["explanations"] if e["effective"]] == [
        beta["_id"]
    ]


async def test_real_invitation_audit_failure_rolls_back_pending_identity_and_delivery(
    management_db, monkeypatch
):
    h = management_db
    counts = {
        name: await h.db[name].count_documents({})
        for name in (
            "users",
            "user_identities",
            "organization_memberships",
            "auth_challenges",
            "management_invitations",
            "governance_events",
        )
    }
    before = await h.engine.anchor("org-a")
    original = h.store.insert

    async def failing(name, row, session=None):
        if name == "governance_outbox":
            raise RuntimeError("fictional unavailable outbox")
        await original(name, row, session)

    monkeypatch.setattr(h.store, "insert", failing)
    with pytest.raises(RuntimeError):
        await invite_new(h)
    assert await h.engine.anchor("org-a") == before
    after = {name: await h.db[name].count_documents({}) for name in counts}
    assert after == counts
    assert list(h.local.mail.directory.glob("*.json")) == []


async def test_real_renewed_pending_invite_invalidates_old_verification_and_preserves_history(
    management_db,
):
    h = management_db
    first, token = await invite_new(h)
    await h.local.accept_invite(token, PASSWORD, "fictional-browser")
    old_verify = last_mail(h, "verify", "fictional-new@example.test")["token"]
    await h.db.management_invitations.update_one(
        {"_id": first["invitation_id"]}, {"$set": {"expires_at": utc_now() - timedelta(seconds=1)}}
    )
    second, new_token = await invite_new(h)
    assert second["invitation_id"] != first["invitation_id"]
    with pytest.raises(AuthRejected):
        await h.local.verify(old_verify, "fictional-browser")
    user_id = await verify_new(h, new_token)
    assert (await h.engine.snapshot("org-a", user_id))["tuples"]
    first_record = await h.db.management_invitations.find_one({"_id": first["invitation_id"]})
    second_record = await h.db.management_invitations.find_one({"_id": second["invitation_id"]})
    assert first_record["status"] == "revoked" and second_record["status"] == "accepted"
    assert first_record["membership_id"] == second_record["membership_id"]
    assert await h.db.users.count_documents({"_id": user_id}) == 1


async def test_real_cutover_fences_old_queued_legacy_workers_and_preserves_review(policy_db):
    from app.db.initializer import initialize_database
    from app.models.agent import AgentClientDocument
    from app.models.api_key import ApiKeyDocument
    from app.services.auth_service import principal_of
    from tests.integration.test_v6_policy_fencing import appoint_lead

    h = policy_db  # actual schema 40 before this test's cutover
    old_lead = await appoint_lead(h)
    old_lead = await h.db.policy_assignments.find_one({"_id": old_lead["_id"]})
    assert (await h.engine.snapshot("org-a", "lead"))["tuples"]
    raw = await h.db.api_keys.find_one({"org_id": "org-a"})
    record = ApiKeyDocument.model_validate(raw, context={"from_storage": True})
    client = None
    if record.client_id:
        client = AgentClientDocument.model_validate(
            await h.db.agent_clients.find_one({"org_id": "org-a", "client_id": record.client_id}),
            context={"from_storage": True},
        )
    principal = principal_of(record, client)
    await h.engine.approve_legacy(
        principal, "fictional-reviewed-adopted-adapter", utc_now() + timedelta(hours=1)
    )
    before = await h.db.policy_credentials.find_one({"org_id": "org-a", "key_id": record.key_id})
    assert await h.engine.credential(principal)
    await initialize_database(
        h.db,
        tenant_foundation=True,
        tenant_isolation=True,
        local_identity=True,
        sso=True,
        management_policy=True,
        human_management=True,
        management_cutover=True,
    )
    after = await h.db.policy_credentials.find_one({"org_id": "org-a", "key_id": record.key_id})
    assert after["mode"] == "legacy_adoption_v41" and before["mode"] == "legacy_adoption"
    assert after["revision"] == before["revision"] + 1
    for field in ("key_id", "envelope_hash", "expires_at", "approval_reference", "status"):
        assert before[field] == after[field]
    # This is the exact compatibility branch used by the old queued resolver:
    # credential() deliberately does not call enabled(); its mode check refuses.
    with pytest.raises(PolicyDenied, match="sponsorship"):
        await h.engine.credential(principal)
    assert await PolicyEngine(h.store, human_management=True).credential(principal)
    assert (await PolicyEngine(h.store, human_management=True).snapshot("org-a", "lead"))[
        "tuples"
    ] == ()
    assert await h.db.policy_assignments.find_one({"_id": old_lead["_id"]}) == old_lead
    await HumanManagementMigration().up(h.db)
    assert await h.db.policy_credentials.find_one({"_id": after["_id"]}) == after
