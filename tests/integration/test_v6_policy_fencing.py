"""Real concurrent replica-set transactions; no mock establishes this gate."""

import asyncio
from contextlib import asynccontextmanager
from copy import deepcopy
from datetime import timedelta
from types import SimpleNamespace

import httpx
import pytest
import pytest_asyncio
from pymongo.errors import OperationFailure

from app.db.initializer import initialize_database
from app.db.migrations.v040_management_policy import ManagementPolicyMigration
from app.db.tenant_collection import TenantCollection
from app.identity.browser import SESSION_COOKIE
from app.identity.crypto import SeedVault, csrf
from app.identity.mail import LocalMailSink
from app.identity.providers import Registration, Registry
from app.identity.repository import IdentityStore
from app.identity.service import LocalIdentity
from app.identity.sso import SSOIdentity
from app.main import create_app, start_authentication
from app.models.common import utc_now
from app.security.policy.engine import PolicyEngine
from app.security.policy.evaluation import PolicyConflict, PolicyDenied
from app.security.policy.governance import Governance
from app.security.policy.models import Grant, Issue, Selector
from app.security.principal import AuthenticationError
from app.security.tenant import TenantContext, tenant_scope
from app.security.tenant_policy import policy_scope
from app.services.auth_service import principal_of

pytestmark = pytest.mark.integration
ORIGIN = "https://identity.example"


def grant(action, kind="project", ids=("payment-service",)):
    return Grant(action=action, resource_kind=kind, selector="include", ids=list(ids))


@pytest_asyncio.fixture
async def policy_db(isolated_database, tmp_path):
    db = isolated_database
    await initialize_database(
        db,
        tenant_foundation=True,
        tenant_isolation=True,
        local_identity=True,
        sso=True,
        management_policy=True,
        management_cutover=True,
    )
    store = IdentityStore(db)
    engine = PolicyEngine(store)
    governance = Governance(engine)
    now = utc_now()
    ceiling = (
        grant("context:read"),
        grant("context:write"),
        grant("grants:delegate", "member", ("lead", "member", "reviewer", "other")),
        grant("grants:read", "member", ("lead", "member", "reviewer", "other")),
        grant("content:read", "creator_profile", ("personal-brand",)),
        grant("content:write", "creator_profile", ("personal-brand",)),
        grant("content:read"),
        grant("content:write"),
        grant("reviews:decide", "creator_profile", ("personal-brand",)),
    )
    for name in ("owner", "lead", "member", "reviewer", "other"):
        await store.insert(
            "users",
            {
                "_id": name,
                "display_name": name,
                "status": "active",
                "auth_epoch": 1,
                "fence": 0,
                "created_at": now,
            },
        )
        await store.insert(
            "user_identities",
            {
                "_id": "identity-" + name,
                "provider": "local",
                "subject": name + "@example.test",
                "user_id": name,
                "verified_at": now,
                "created_at": now,
            },
        )
        intent = (
            None
            if name != "owner"
            else {
                "state": "pending_phase5",
                "approval_reference": "synthetic-recorded-operator-intent",
                "root_delegation": [g.model_dump() for g in ceiling],
                "bootstrap_policy": [
                    Grant(
                        action=action, resource_kind="project_directory", selector="all_in_org"
                    ).model_dump()
                    for action in ("projects:read", "projects:write")
                ],
            }
        )
        await store.insert(
            "organization_memberships",
            {
                "_id": "membership-" + name,
                "org_id": "org-a",
                "user_id": name,
                "display_name": name,
                "role": "org_owner" if name == "owner" else "contributor",
                "status": "active",
                "verified_at": now,
                "revision": 1,
                "fence": 0,
                "bootstrap_owner": name == "owner",
                "bootstrap_intent": intent,
                "created_at": now,
            },
        )
    result = await governance.bootstrap(
        "org-a",
        "owner",
        targets=Selector(mode="include", ids=("lead", "member", "reviewer", "other")),
        allowed_roles=("team_lead", "contributor", "reviewer"),
        expires_at=now + timedelta(days=10),
        approval="synthetic-activation-review",
        approve_management_template=True,
    )
    vault = SeedVault("fixture", {"fixture": b"p" * 32})
    local = LocalIdentity(store, vault, LocalMailSink(tmp_path / "mail"))
    # Approved reserved test endpoint, never contacted. Provider compatibility is
    # separately established by the real Keycloak selector; this suite uses the
    # supported local-only path with persisted verified identities and MFA time.
    registration = Registration(
        registration_id="test-provider",
        org_id="org-a",
        issuer="https://idp.example/realm",
        client_id="test-client",
        client_secret="synthetic-disposable-client-secret-for-test",
        connect_ip="203.0.113.10",
        redirect_uri=ORIGIN + "/api/v6/auth/oidc/callback",
        post_logout_redirect_uri=ORIGIN + "/api/v6/auth/oidc/logged-out",
        authorization_endpoint="https://idp.example/auth",
        token_endpoint="https://idp.example/token",
        jwks_uri="https://idp.example/keys",
        end_session_endpoint="https://idp.example/logout",
    )
    local.sso = SSOIdentity(local, Registry([registration], ORIGIN), vault)
    local.policy_engine = local.sessions.policy_engine = engine
    app = create_app()
    app.state.local_identity = local
    app.state.settings = SimpleNamespace(v6_browser_origin=ORIGIN, v6_management_policy=True)
    app.state.tenant_control_database = db
    try:
        yield SimpleNamespace(
            db=db,
            store=store,
            engine=engine,
            governance=governance,
            root=result["assignment_id"],
            local=local,
            app=app,
        )
    finally:
        local.passwords.close()


async def appoint_lead(h):
    tuples = (
        grant("context:read"),
        grant("context:write"),
        grant("grants:delegate", "member", ("member",)),
    )
    return await h.governance.issue(
        "org-a",
        "owner",
        Issue(
            subject_id="lead",
            role="team_lead",
            parent_id=h.root,
            expected_parent_generation=1,
            tuples=tuples,
            ceiling=(grant("context:read"),),
            targets=Selector(mode="include", ids=("member",)),
            allowed_roles=("contributor",),
            remaining_depth=1,
            expires_at=utc_now() + timedelta(days=3),
        ),
    )


def child(parent):
    return Issue(
        subject_id="member",
        parent_id=parent["_id"],
        expected_parent_generation=parent["generation"],
        tuples=(grant("context:read"),),
        expires_at=utc_now() + timedelta(days=1),
    )


async def test_real_bounded_snapshot_matches_transaction_and_excludes_foreign_members(policy_db):
    h = policy_db
    await appoint_lead(h)
    user = await h.store.find("users", {"_id": "member"})
    member = await h.store.find(
        "organization_memberships", {"org_id": "org-a", "user_id": "member"}
    )
    foreign_user = {**deepcopy(user), "_id": "foreign-only-b"}
    await h.store.insert("users", foreign_user)
    for subject in ("lead", "foreign-only-b"):
        await h.store.insert(
            "organization_memberships",
            {
                **deepcopy(member),
                "_id": "membership-b-" + subject,
                "org_id": "org-b",
                "user_id": subject,
            },
        )
    direct = await h.engine.snapshot("org-a", "owner")
    async with h.engine.transaction() as tx:
        transactional = await h.engine.snapshot("org-a", "owner", tx)
    for key in ("anchor", "member", "user", "assignments", "members", "users"):
        assert direct[key] == transactional[key]
    assert {g.model_dump_json() for g in direct["tuples"]} == {
        g.model_dump_json() for g in transactional["tuples"]
    }
    # A global identity alone never brings a foreign membership into the query.
    inputs = await h.engine.read_inputs("org-a", "foreign-only-b")
    assert all(row["org_id"] == "org-a" for row in inputs["member_rows"])
    assert "foreign-only-b" not in {row["_id"] for row in inputs["user_rows"]}
    assert len([row for row in inputs["member_rows"] if row["user_id"] == "lead"]) == 1
    assert (await h.engine.snapshot("org-a", "lead"))["tuples"]
    await h.store.update(
        "organization_memberships",
        {"org_id": "org-a", "user_id": "lead"},
        {
            "$set": {"status": "suspended"},
            "$inc": {"revision": 1},
        },
    )
    with pytest.raises(PolicyDenied):
        await h.engine.snapshot("org-a", "lead")


async def test_real_join_keeps_transaction_snapshot_but_revoke_fences_delivery(policy_db):
    h = policy_db
    await appoint_lead(h)
    member = await h.store.find("organization_memberships", {"org_id": "org-a", "user_id": "lead"})
    async with h.engine.transaction() as tx:
        before = await h.engine.snapshot("org-a", "lead", tx)
        assert before["tuples"]
        await h.governance.membership_status(
            "org-a", "owner", "lead", member["revision"], suspended=True
        )
        # A second joined read must use the SAME transaction's snapshot.
        # Losing session=tx would incorrectly observe the concurrent suspension.
        after = await h.engine.snapshot("org-a", "lead", tx)
        assert after["anchor"] == before["anchor"]
        assert after["member"] == before["member"]
        assert {g.model_dump_json() for g in after["tuples"]} == {
            g.model_dump_json() for g in before["tuples"]
        }
    # The snapshot cannot authorize delivery after the committed revoke.
    with pytest.raises(PolicyConflict):
        await h.engine.confirm_read(after["anchor"])
    with pytest.raises(PolicyDenied):
        await h.engine.snapshot("org-a", "lead")


async def test_real_current_control_guard_refuses_even_an_older_transaction(policy_db):
    h = policy_db
    controls = h.db["_management_policy_control"]
    removed = None
    try:
        async with h.engine.transaction() as tx:
            assert (await h.engine.snapshot("org-a", "owner", tx))["tuples"]
            removed = await controls.find_one_and_delete({"_id": "installation"})
            assert removed is not None
            with pytest.raises(PolicyDenied):
                await h.engine.snapshot("org-a", "owner")
            # An older policy snapshot cannot bypass a current installation seal.
            with pytest.raises(PolicyDenied):
                await h.engine.snapshot("org-a", "owner", tx)
    finally:
        if removed is not None:
            await controls.insert_one(removed)


@pytest.mark.parametrize("narrow", [False, True])
async def test_real_parent_revoke_or_narrow_wins_against_child_issue(
    policy_db, monkeypatch, narrow
):
    h = policy_db
    parent = await appoint_lead(h)
    ready, release = asyncio.Event(), asyncio.Event()
    original = h.engine.fence
    observed = []

    async def paused(anchor, tx, *, mutation=False):
        if asyncio.current_task().get_name() == "child-issue":
            observed.append(anchor["revision"])
            if len(observed) == 1:
                ready.set()
                await asyncio.wait_for(release.wait(), 10)
        await original(anchor, tx, mutation=mutation)

    monkeypatch.setattr(h.engine, "fence", paused)
    issuing = asyncio.create_task(
        h.governance.issue("org-a", "lead", child(parent)), name="child-issue"
    )
    await asyncio.wait_for(ready.wait(), 10)
    await h.governance.change(
        "org-a", "owner", parent["_id"], 1, tuples=() if narrow else None, revoke=not narrow
    )
    release.set()
    with pytest.raises(PolicyDenied):
        await asyncio.wait_for(issuing, 10)
    assert len(observed) == 2 and observed[1] > observed[0]  # actual write conflict, fresh retry
    assert (
        await h.db.policy_assignments.count_documents({"org_id": "org-a", "subject_id": "member"})
        == 0
    )
    assert (await h.engine.snapshot("org-a", "lead"))["tuples"] == ()


async def test_real_child_commit_before_revoke_is_immediately_ineffective(policy_db, monkeypatch):
    h = policy_db
    parent = await appoint_lead(h)
    ready, release = asyncio.Event(), asyncio.Event()
    original = h.engine.fence
    attempts = []

    async def paused(anchor, tx, *, mutation=False):
        if asyncio.current_task().get_name() == "parent-revoke":
            attempts.append(anchor["revision"])
            if len(attempts) == 1:
                ready.set()
                await asyncio.wait_for(release.wait(), 10)
        await original(anchor, tx, mutation=mutation)

    monkeypatch.setattr(h.engine, "fence", paused)
    revoking = asyncio.create_task(
        h.governance.change("org-a", "owner", parent["_id"], 1, revoke=True), name="parent-revoke"
    )
    await asyncio.wait_for(ready.wait(), 10)
    issued = await h.governance.issue("org-a", "lead", child(parent))
    release.set()
    await asyncio.wait_for(revoking, 10)
    assert len(attempts) == 2 and attempts[1] > attempts[0]
    assert await h.db.policy_assignments.find_one({"_id": issued["_id"]})
    assert (await h.engine.snapshot("org-a", "member"))["tuples"] == ()
    # A newly approved wider parent has a new ID; it cannot reattach the old child.
    await appoint_lead(h)
    assert (await h.engine.snapshot("org-a", "member"))["tuples"] == ()


@pytest.mark.parametrize("failed_collection", ["governance_events", "governance_outbox"])
async def test_real_audit_or_outbox_failure_rolls_back_assignment_member_and_anchor(
    policy_db, monkeypatch, failed_collection
):
    h = policy_db
    before = await h.engine.anchor("org-a")
    count = await h.db.governance_events.count_documents({})
    original = h.store.insert

    async def fail(name, row, session=None):
        if name == failed_collection:
            raise RuntimeError("synthetic audit failure")
        await original(name, row, session)

    monkeypatch.setattr(h.store, "insert", fail)
    with pytest.raises(RuntimeError):
        await appoint_lead(h)
    assert await h.engine.anchor("org-a") == before
    assert await h.db.policy_assignments.count_documents({}) == 1
    assert (await h.db.organization_memberships.find_one({"user_id": "lead"}))[
        "role"
    ] == "contributor"
    assert await h.db.governance_events.count_documents({}) == count
    assert await h.db.governance_outbox.count_documents({}) == count


async def test_real_suspension_reinstatement_and_old_generation_never_restore_children(policy_db):
    h = policy_db
    parent = await appoint_lead(h)
    await h.governance.issue("org-a", "lead", child(parent))
    member = await h.db.organization_memberships.find_one({"user_id": "lead"})
    await h.governance.membership_status(
        "org-a", "owner", "lead", member["revision"], suspended=True
    )
    assert (await h.engine.snapshot("org-a", "member"))["tuples"] == ()
    await h.governance.membership_status(
        "org-a", "owner", "lead", member["revision"] + 1, suspended=False
    )
    assert (await h.engine.snapshot("org-a", "lead"))["tuples"] == ()
    assert (await h.engine.snapshot("org-a", "member"))["tuples"] == ()
    with pytest.raises(PolicyDenied):
        await h.governance.membership_status("org-a", "owner", "owner", 1, suspended=True)
    with pytest.raises(PolicyDenied):
        await h.governance.issue(
            "org-a",
            "owner",
            Issue(
                subject_id="owner",
                role="org_owner",
                parent_id=h.root,
                expected_parent_generation=1,
                expires_at=utc_now() + timedelta(days=1),
            ),
        )


@asynccontextmanager
async def browser(h, subject="owner"):
    user = await h.db.users.find_one({"_id": subject})
    member = await h.db.organization_memberships.find_one({"org_id": "org-a", "user_id": subject})
    org = await h.db.organizations.find_one({"org_id": "org-a"})
    issued = await h.local.sessions.issue(
        user, member, org, ["password", "mfa"], utc_now(), utc_now()
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=h.app),
        base_url=ORIGIN,
        cookies={SESSION_COOKIE: issued.token},
    ) as client:
        yield (
            client,
            {
                "Origin": ORIGIN,
                "X-CSRF-Token": csrf(issued.token),
                "X-Session-Context": h.local.policy_context(issued.row)["context_id"],
            },
        )


async def test_real_session_governance_csrf_context_none_and_transactional_transfer(policy_db):
    h = policy_db
    async with browser(h) as (client, headers):
        response = await client.get("/api/v6/me/effective-access")
        assert response.status_code == 200
        assert not response.json()["role_has_corpus_bypass"]
        assert all(
            t["action"] not in {"context:read", "context:write"} for t in response.json()["tuples"]
        )
        body = child({"_id": h.root, "generation": 1}).model_dump(mode="json")
        assert (
            await client.post(
                "/api/v6/organizations/org-a/assignments",
                json=body,
                headers={"Origin": ORIGIN, "X-Session-Context": headers["X-Session-Context"]},
            )
        ).status_code == 403
        assert (
            await client.post("/api/v6/organizations/org-b/assignments", json=body, headers=headers)
        ).status_code == 403
        assert (
            await client.post("/api/v6/organizations/org-a/assignments", json=body, headers=headers)
        ).status_code == 201
        # Independently approved operator recovery is use-only, and ownership
        # transfer must not turn it into the next owner's corpus bypass.
        with tenant_scope(TenantContext("org-a")):
            await h.governance.operator_recover_corpus(
                "org-a",
                "owner",
                (grant("context:read"),),
                utc_now() + timedelta(hours=1),
                "synthetic-independent-recovery",
            )
        assert any(
            g.action == "context:read"
            for g in (await h.engine.snapshot("org-a", "owner"))["tuples"]
        )
        offer = await client.post(
            "/api/v6/organizations/org-a/ownership-transfer",
            json={"target_id": "other"},
            headers=headers,
        )
        assert offer.status_code == 201
    async with browser(h, "other") as (client, headers):
        transfer_id = offer.json()["_id"]
        result = await client.post(
            f"/api/v6/organizations/org-a/ownership-transfer/{transfer_id}/accept",
            json={},
            headers=headers,
        )
        assert result.status_code == 200 and result.json()["reauthentication_required"]
        assert (await client.get("/api/v6/me")).status_code == 401
    assert (
        await h.db.organization_memberships.count_documents(
            {"org_id": "org-a", "role": "org_owner", "status": "active"}
        )
        == 1
    )
    assert (await h.db.organization_memberships.find_one({"user_id": "owner"}))[
        "role"
    ] == "contributor"
    assert (await h.engine.snapshot("org-a", "member"))["tuples"] == ()  # old lineage revoked
    assert not any(
        g.action == "context:read" for g in (await h.engine.snapshot("org-a", "other"))["tuples"]
    )


async def test_real_managed_domain_writes_share_the_anchor_and_stop_after_revoke(policy_db):
    h = policy_db
    parent = await appoint_lead(h)
    snapshot = await h.engine.snapshot("org-a", "lead")
    p = h.engine.policy(snapshot)
    with tenant_scope(TenantContext("org-a")), policy_scope(p, "context:write"):
        collection = TenantCollection(
            h.db.project_context, "project_context", lambda: TenantContext("org-a")
        )
        row = await collection.find_one({})
        before = await h.engine.anchor("org-a")
        result = await collection.update_one(
            {"_id": row["_id"]}, {"$set": {"content": "private managed update"}}
        )
        assert result.modified_count == 1
        assert (await h.engine.anchor("org-a"))["fence"] > before["fence"]
    await h.governance.change("org-a", "owner", parent["_id"], 1, revoke=True)
    with (
        tenant_scope(TenantContext("org-a")),
        policy_scope(p, "context:write"),
        pytest.raises(PolicyDenied),
    ):
        await collection.update_one({"_id": row["_id"]}, {"$set": {"content": "forbidden"}})
    assert (await h.db.project_context.find_one({"_id": row["_id"]}))[
        "content"
    ] == "private managed update"


async def test_real_strict_schema_migration_resume_and_unverified_machines_are_sealed(policy_db):
    h = policy_db
    await ManagementPolicyMigration().up(h.db)
    with pytest.raises(OperationFailure):
        await h.db.policy_assignments.update_one(
            {"_id": h.root}, {"$set": {"subject_kind": "machine"}}
        )
    with pytest.raises(OperationFailure):
        await h.db.policy_assignments.update_one({"_id": h.root}, {"$set": {"org_id": None}})
    from app.models.api_key import ApiKeyDocument

    with tenant_scope(TenantContext("org-a")):
        raw = await h.db.api_keys.find_one({"org_id": "org-a"})
        principal = principal_of(
            ApiKeyDocument.model_validate(raw, context={"from_storage": True}), None
        )
        with pytest.raises(PolicyDenied, match="sponsorship"):
            await h.engine.credential(principal)
    # An old worker's stage check and startup ledger both refuse the new schema.
    with pytest.raises(RuntimeError, match="policy-aware"):
        await initialize_database(
            h.db, tenant_foundation=True, tenant_isolation=True, local_identity=True, sso=True
        )
    auth = start_authentication(
        SimpleNamespace(api_auth_enabled=True, v6_management_policy=False), h.db
    )
    with pytest.raises(AuthenticationError):
        await auth.authenticate("acp_" + "a" * 16 + "_" + "a" * 40)


async def test_real_review_operation_and_immutable_authorship_prevent_self_review(policy_db):
    from app.models.content import ContentItemDocument

    h = policy_db
    base = (
        grant("content:read", "creator_profile", ("personal-brand",)),
        grant("content:write", "creator_profile", ("personal-brand",)),
        grant("content:read"),
        grant("content:write"),
    )
    for name, role, tuples in (
        ("member", "contributor", base),
        (
            "reviewer",
            "reviewer",
            (*base, grant("reviews:decide", "creator_profile", ("personal-brand",))),
        ),
    ):
        await h.governance.issue(
            "org-a",
            "owner",
            Issue(
                subject_id=name,
                role=role,
                parent_id=h.root,
                expected_parent_generation=1,
                tuples=tuples,
                expires_at=utc_now() + timedelta(days=1),
            ),
        )
    old = await h.db.content_items.find_one({"org_id": "org-a"})
    original = {
        k: v for k, v in old.items() if k not in {"_id", "identity_version", "legacy_adoption_id"}
    }
    original.update(
        status="review",
        brief_id=None,
        source_refs=[],
        actor={
            "actor_type": "human",
            "user_id": "member",
            "org_id": "org-a",
            "auth_method": "local",
            "assurance": "mfa",
        },
    )
    author = h.engine.policy(await h.engine.snapshot("org-a", "member"))
    reviewer = h.engine.policy(await h.engine.snapshot("org-a", "reviewer"))
    with tenant_scope(TenantContext("org-a")), policy_scope(author, "content:write"):
        collection = TenantCollection(
            h.db.content_items, "content_items", lambda: TenantContext("org-a")
        )
        documents = [
            ContentItemDocument.model_validate({**original, "content_id": identifier}).model_dump(
                by_alias=True, mode="python"
            )
            for identifier in ("independent-review", "edited-by-reviewer")
        ]
        await collection.insert_many(documents)
        with pytest.raises(PolicyDenied):
            await collection.update_one(
                {"_id": documents[0]["_id"]}, {"$set": {"status": "approved"}}
            )
    with tenant_scope(TenantContext("org-a")), policy_scope(reviewer, "content:write"):
        # Pure content:write is insufficient; reviewer must also hold operation
        # and source-read tuples. A machine actor never acquires this exception.
        await collection.update_one(
            {"_id": documents[1]["_id"]}, {"$set": {"body": "edited by the reviewer"}}
        )
        with pytest.raises(PolicyDenied, match="authorship"):
            await collection.update_one(
                {"_id": documents[1]["_id"]}, {"$set": {"status": "approved"}}
            )
        approved = await collection.update_one(
            {"_id": documents[0]["_id"]}, {"$set": {"status": "approved"}}
        )
        assert approved.modified_count == 1
        # An adopted row has no trusted sponsorship ledger. Editing it cannot
        # manufacture proof of independent authorship.
        with pytest.raises(PolicyDenied):
            await collection.update_one({"_id": old["_id"]}, {"$set": {"status": "approved"}})
    assert (await h.db.content_items.find_one({"_id": documents[0]["_id"]}))["status"] == "approved"


async def test_real_reviewed_adoption_adapter_rest_http_mcp_and_operator_revoke(
    policy_db, monkeypatch
):
    from v6_isolation_fixtures import token

    from app.mcp.auth import Authorization
    from app.mcp.remote_http import RemoteMcpGateway, add_remote_mcp_route
    from app.models.api_key import ApiKeyDocument
    from tests.integration.test_v6_mcp_isolation import application

    h = policy_db
    app = application(h.db)
    from app.identity.http_errors import policy_denied

    app.add_exception_handler(PolicyDenied, policy_denied)
    app.state.settings = app.state.settings.model_copy(update={"v6_management_policy": True})
    app.state.auth = start_authentication(
        SimpleNamespace(api_auth_enabled=True, v6_management_policy=True), h.db
    )
    remote = RemoteMcpGateway()
    add_remote_mcp_route(app, remote)
    async with remote.run(app, authorization=Authorization(), server_name="agent-context-platform"):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url=ORIGIN
        ) as http:
            headers = {
                "Authorization": "Bearer " + token("a"),
                "Accept": "application/json, text/event-stream",
            }
            assert (await http.get("/api/v1/projects", headers=headers)).status_code == 401
            initialize = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {},
                    "clientInfo": {"name": "phase5", "version": "1"},
                },
            }
            assert (await http.post("/mcp", headers=headers, json=initialize)).status_code == 401
            with tenant_scope(TenantContext("org-a")):
                raw = await h.db.api_keys.find_one({"org_id": "org-a"})
                principal = principal_of(
                    ApiKeyDocument.model_validate(raw, context={"from_storage": True}), None
                )
                await h.engine.approve_legacy(
                    principal, "synthetic-explicit-adoption-review", utc_now() + timedelta(hours=1)
                )
            assert (await http.get("/api/v1/projects", headers=headers)).status_code == 200
            content = await h.db.content_items.find_one({"org_id": "org-a"})
            denied_review = await http.post(
                "/api/v2/content/" + content["content_id"] + "/approve",
                headers=headers,
                json={
                    "profile_id": content["profile_id"],
                    "expected_revision": content["revision"],
                },
            )
            assert denied_review.status_code == 403
            result = await http.post("/mcp", headers=headers, json=initialize)
            assert result.status_code == 200
            headers.update(
                {
                    "Mcp-Session-Id": result.headers["mcp-session-id"],
                    "Mcp-Protocol-Version": "2025-06-18",
                }
            )
            original_audit = PolicyEngine.audit

            async def unavailable(*args, **kwargs):
                raise RuntimeError("synthetic operator governance audit failure")

            with tenant_scope(TenantContext("org-a")):
                collection = TenantCollection(
                    h.db.api_keys, "api_keys", lambda: TenantContext("org-a")
                )
                before = await h.engine.anchor("org-a")
                monkeypatch.setattr(PolicyEngine, "audit", unavailable)
                with pytest.raises(RuntimeError):
                    await collection.update_one(
                        {"key_id": raw["key_id"]},
                        {"$set": {"status": "revoked", "revoked_at": utc_now()}},
                    )
                assert (await h.db.api_keys.find_one({"_id": raw["_id"]}))["status"] == "active"
                assert await h.engine.anchor("org-a") == before
                monkeypatch.setattr(PolicyEngine, "audit", original_audit)
                await collection.update_one(
                    {"key_id": raw["key_id"]},
                    {"$set": {"status": "revoked", "revoked_at": utc_now()}},
                )
            assert (await http.get("/api/v1/projects", headers=headers)).status_code == 401
            call = {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {"name": "get_skill", "arguments": {"skill_id": "retry-skill"}},
            }
            assert (await http.post("/mcp", headers=headers, json=call)).status_code == 401


async def test_real_managed_search_intersects_action_resources_before_mongot_selection(policy_db):
    from app.db.context_indexes import context_text_definition, context_vector_definition
    from app.db.tenant_inventory import MODEL_BY_COLLECTION
    from app.security.tenant_policy import ResourceGrant, TenantPolicy

    h = policy_db
    original_project = await h.db.projects.find_one({"org_id": "org-a"})
    original_context = await h.db.project_context.find_one({"org_id": "org-a"})
    # Both tenants already reuse the readable project/context identifiers. Add a
    # same-tenant write-only project with matching search terms and vectors.
    with tenant_scope(TenantContext("org-a")):
        for name, source in (("projects", original_project), ("project_context", original_context)):
            values = {
                k: v
                for k, v in source.items()
                if k not in {"_id", "identity_version", "legacy_adoption_id"}
            }
            row = MODEL_BY_COLLECTION[name].model_validate(
                {**values, "project_id": "write-only-project"}
            )
            await h.db[name].insert_one(row.model_dump(by_alias=True, mode="python"))
    await h.db.project_context.update_many(
        {},
        {
            "$set": {
                "embedding": [1.0, 0.0, 0.0],
                "embedding_dimensions": 3,
                "embedding_model": "deterministic-integration-fixture",
                "embedding_generated_at": utc_now(),
            }
        },
    )
    await h.db.project_context.update_one(
        {"org_id": "org-a", "project_id": "write-only-project"},
        {"$set": {"content": "WRITE_ONLY_SENTINEL isolation"}},
    )
    await h.db.project_context.create_search_index(
        {"name": "policy_text", "definition": context_text_definition()}
    )
    await h.db.project_context.create_search_index(
        {
            "name": "policy_vector",
            "type": "vectorSearch",
            "definition": context_vector_definition(3),
        }
    )
    for _ in range(120):
        indexes = await (await h.db.project_context.list_search_indexes()).to_list(None)
        if len(indexes) == 2 and all(index.get("queryable") for index in indexes):
            break
        await asyncio.sleep(0.25)
    else:
        pytest.fail("disposable managed-policy search indexes did not become queryable")
    # Trusted policy fixture isolates candidate filtering from grant issuance,
    # which the other real transaction cases exercise against stored policy.
    policy = TenantPolicy(
        "org-a",
        "member",
        (
            ResourceGrant("search:read", "project", "all_in_org"),
            ResourceGrant("context:read", "project", "include", ("payment-service",)),
            ResourceGrant("context:write", "project", "include", ("write-only-project",)),
        ),
        managed=True,
        subject_kind="human",
    )
    with tenant_scope(TenantContext("org-a")), policy_scope(policy, "search:read"):
        collection = TenantCollection(
            h.db.project_context, "project_context", lambda: TenantContext("org-a")
        )
        for pipeline in (
            [
                {
                    "$search": {
                        "index": "policy_text",
                        "compound": {"must": [{"text": {"path": "content", "query": "isolation"}}]},
                    }
                },
                {"$limit": 1},
            ],
            [
                {
                    "$vectorSearch": {
                        "index": "policy_vector",
                        "path": "embedding",
                        "queryVector": [1.0, 0.0, 0.0],
                        "numCandidates": 10,
                        "limit": 1,
                    }
                }
            ],
        ):
            for _ in range(60):
                rows = await (await collection.aggregate(pipeline)).to_list(10)
                if rows:
                    break
                await asyncio.sleep(0.25)
            assert len(rows) == 1 and rows[0]["_id"] == original_context["_id"]
            assert "PRIVATE_SENTINEL_A" in rows[0]["content"]
        assert (
            await (
                await collection.aggregate(
                    [
                        {
                            "$search": {
                                "index": "policy_text",
                                "compound": {
                                    "must": [
                                        {
                                            "text": {
                                                "path": "content",
                                                "query": "WRITE_ONLY_SENTINEL",
                                            }
                                        }
                                    ]
                                },
                            }
                        }
                    ]
                )
            ).to_list(10)
            == []
        )
