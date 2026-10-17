"""Real service custody and separately approved operation-specific review."""

from datetime import timedelta

import httpx
import pytest
from v6_managed_agent_fixtures import approval, g, provision

from app import provenance
from app.db.tenant_collection import TenantCollection
from app.managed_agents.models import CredentialIssue, ReviewerApproval, ServiceCreate
from app.models.common import utc_now
from app.models.content import ContentItemDocument
from app.security.policy.evaluation import PolicyDenied
from app.security.policy.models import Issue
from app.security.principal import AuthenticationError
from app.security.tenant import TenantContext, tenant_scope
from app.security.tenant_policy import policy_scope

pytestmark = pytest.mark.integration


async def service_worker(h):
    await h.agents.create_service(
        "org-a",
        "owner",
        ServiceCreate(
            service_account_id="fictional-ci",
            name="Fictional CI",
            custodian_id="member",
            team_id="alpha",
            expected_team_generation=1,
            expires_at=utc_now() + timedelta(days=2),
        ),
    )
    reviews = tuple(
        g("reviews:decide", kind, ids)
        for kind, ids in (
            ("creator_profile", ("personal-brand",)),
            ("research_project", (h.source["research_project_id"],)),
        )
    )
    command = ReviewerApproval(
        expected_revision=1,
        parent_id=h.reviewer_assignment["_id"],
        expected_parent_generation=1,
        tuples=tuple(
            x
            for x in h.domain
            if x.action in {"content:read", "content:write", "research:read", "research:approve"}
        )
        + reviews,
        expires_at=utc_now() + timedelta(days=1),
    )
    await h.agents.reviewer_approve("org-a", "reviewer", "fictional-ci", command)
    row = await h.agents.approve(
        "org-a",
        "lead",
        approval(h).model_copy(
            update={
                "subject_kind": "service_account",
                "subject_id": "fictional-ci",
                "tuples": h.domain + reviews,
            }
        ),
    )
    delivered = await h.agents.issue(
        "org-a",
        "member",
        row["client_id"],
        CredentialIssue(
            issuance_id="service-one",
            expected_client_revision=1,
            tuples=h.domain + reviews,
            expires_at=utc_now() + timedelta(hours=12),
        ),
    )
    return row, delivered


async def authored(h, principal, name):
    original = await h.db.content_items.find_one({"org_id": "org-a"})
    with (
        tenant_scope(TenantContext("org-a")),
        policy_scope(principal.managed_policy, "content:write"),
    ):
        actor = provenance.actor_from_principal(principal)
        values = {
            k: v
            for k, v in original.items()
            if k
            not in {
                "_id",
                "identity_version",
                "legacy_adoption_id",
                "content_id",
                "actor",
                "created_at",
                "updated_at",
                "revision",
                "status",
            }
        }
        item = ContentItemDocument(
            **values, content_id=name, actor=actor, identity_version=2, status="review"
        )
        collection = TenantCollection(
            h.db.content_items, "content_items", lambda: TenantContext("org-a")
        )
        await collection.insert_one(item.model_dump(by_alias=True))
        return item


async def test_normal_client_cannot_review_even_with_content_write(managed_agents_db):
    h = managed_agents_db
    _, _, delivered = await provision(h)
    principal = await h.app.state.auth.authenticate(delivered["token"])
    item = await authored(h, principal, "fictional-own-draft")
    assert item.actor.sponsor_id == "member" and item.actor.user_id is None
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=h.app),
        base_url="https://identity.example",
        headers={"Authorization": "Bearer " + delivered["token"]},
    ) as client:
        response = await client.post(
            "/api/v2/content/" + item.content_id + "/approve",
            json={"profile_id": item.profile_id, "expected_revision": 1},
        )
        assert response.status_code == 403, response.text


async def test_approved_service_review_custody_and_event_time_self_review(managed_agents_db):
    h = managed_agents_db
    row, delivered = await service_worker(h)
    principal = await h.app.state.auth.authenticate(delivered["token"])
    assert principal.managed_policy.reviewer
    own = await authored(h, principal, "fictional-service-own-draft")
    with (
        tenant_scope(TenantContext("org-a")),
        policy_scope(principal.managed_policy, "content:write"),
    ):
        collection = TenantCollection(
            h.db.content_items, "content_items", lambda: TenantContext("org-a")
        )
        with pytest.raises(PolicyDenied):
            await collection.update_one({"_id": own.id}, {"$set": {"status": "approved"}})
    # An independently verified human creates a new record through the same
    # fenced persistence boundary; the server records immutable author history.
    await h.governance.issue(
        "org-a",
        "owner",
        Issue(
            subject_id="other",
            parent_id=h.root,
            expected_parent_generation=3,
            tuples=h.domain,
            expires_at=utc_now() + timedelta(days=2),
            team_id="beta",
            expected_team_generation=1,
        ),
    )
    snapshot = await h.engine.snapshot("org-a", "other")
    original = await h.db.content_items.find_one({"_id": own.id})
    with (
        tenant_scope(TenantContext("org-a")),
        policy_scope(h.engine.policy(snapshot), "content:write"),
    ):
        provenance.set_human("other", "org-a", "mfa")
        values = {
            k: v
            for k, v in original.items()
            if k not in {"_id", "content_id", "actor", "created_at", "updated_at", "revision"}
        }
        item = ContentItemDocument(
            **values, content_id="fictional-independent-draft", actor=provenance.current()
        )
        await TenantCollection(
            h.db.content_items, "content_items", lambda: TenantContext("org-a")
        ).insert_one(item.model_dump(by_alias=True))
    # Exercise the ordinary REST review workflow with fresh current policy.
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=h.app),
        base_url="https://identity.example",
        headers={"Authorization": "Bearer " + delivered["token"]},
    ) as http:
        response = await http.post(
            "/api/v2/content/" + item.content_id + "/approve",
            json={"profile_id": item.profile_id, "expected_revision": 1},
        )
        assert response.status_code == 200, response.text
    assert (await h.db.content_items.find_one({"_id": item.id}))["status"] == "approved"
    authors = await h.db.review_authorship.find_one({"org_id": "org-a", "document_id": str(own.id)})
    assert authors["sponsor_ids"] == ["member"]
    await h.agents.revoke_service("org-a", "owner", "fictional-ci", 2)
    with pytest.raises(AuthenticationError):
        await h.app.state.auth.authenticate(delivered["token"])


async def test_service_custodian_cannot_self_approve_exception_or_use_other_team(managed_agents_db):
    h = managed_agents_db
    with pytest.raises(PolicyDenied):
        await h.agents.create_service(
            "org-a",
            "owner",
            ServiceCreate(
                service_account_id="bad",
                name="Fictional",
                custodian_id="member",
                team_id="beta",
                expected_team_generation=1,
                expires_at=utc_now() + timedelta(days=1),
            ),
        )
    row, _ = await service_worker(h)
    with pytest.raises(PolicyDenied):
        await h.agents.reviewer_approve(
            "org-a",
            "member",
            "fictional-ci",
            ReviewerApproval(
                expected_revision=2,
                parent_id=h.sponsor_assignment["_id"],
                expected_parent_generation=1,
                tuples=(g("reviews:decide", "creator_profile", ("personal-brand",)),),
                expires_at=utc_now() + timedelta(hours=1),
            ),
        )
    await h.governance.change("org-a", "owner", h.reviewer_assignment["_id"], 1, revoke=True)
    # Review authorization disappears with the independent reviewer's ancestry.
    creds = await h.db.managed_credentials.find_one({"client_id": row["client_id"]})
    from app.models.agent import AgentClientDocument
    from app.models.api_key import ApiKeyDocument
    from app.services.auth_service import principal_of

    key = await h.db.api_keys.find_one({"key_id": creds["key_id"]})
    client = await h.db.agent_clients.find_one({"org_id": "org-a", "client_id": row["client_id"]})
    with pytest.raises(PolicyDenied):
        await h.agents.resolve(
            principal_of(
                ApiKeyDocument.model_validate(key, context={"from_storage": True}),
                AgentClientDocument.model_validate(client, context={"from_storage": True}),
            )
        )
