"""Real creator services, transitive links, immutable versions and manual receipts."""

from datetime import timedelta

import pytest
import pytest_asyncio

from app.models.common import utc_now
from app.security.policy.models import Grant, Issue
from tests.integration.test_v6_policy_fencing import browser
from tests.v6_creator_fixtures import PROFILE, prepare_creator

pytestmark = pytest.mark.integration
B = "/api/v6/organizations/org-a/creator"
P = B + "/profiles/" + PROFILE


@pytest_asyncio.fixture
async def creator_db(managed_agents_db):
    await prepare_creator(managed_agents_db)
    return managed_agents_db


async def create_draft(http, headers, name="studio-draft"):
    response = await http.post(
        B + "/items",
        headers=headers,
        json={
            "profile_id": PROFILE,
            "content_id": name,
            "platform": "linkedin",
            "content_type": "post",
            "title": "Fictional public retries",
            "body": "Human-authored public guidance about bounded retries.",
            "project_ids": ["payment-service"],
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


async def test_draft_revision_independent_approval_export_and_manual_receipt(creator_db):
    h = creator_db
    async with browser(h, "member") as (http, headers):
        draft = await create_draft(http, headers)
        changed = await http.post(
            B + "/items/studio-draft/versions",
            headers=headers,
            json={
                "profile_id": PROFILE,
                "expected_revision": 1,
                "body": "Revised human-authored public guidance.",
                "change_reason": "Clarify the bounded procedure",
            },
        )
        assert changed.status_code == 200 and changed.json()["current_version"] == 2, changed.text
        history = await http.get(P + "/items/studio-draft/versions", headers=headers)
        assert history.status_code == 200 and {r["version"] for r in history.json()["items"]} == {
            1,
            2,
        }, history.text
        assert (
            next(v for v in history.json()["items"] if v["version"] == 1)["body"] == draft["body"]
        )
        reviewed = await http.patch(
            B + "/items/studio-draft",
            headers=headers,
            json={"profile_id": PROFILE, "expected_revision": 2, "status": "review"},
        )
        assert reviewed.status_code == 200, reviewed.text
        approval = {"profile_id": PROFILE, "expected_revision": 3}
        assert (
            await http.post(B + "/items/studio-draft/approve", headers=headers, json=approval)
        ).status_code == 403
        assert (
            await http.post(B + "/items/studio-draft/export", headers=headers, json=approval)
        ).status_code == 409
    async with browser(h, "reviewer") as (http, headers):
        stale = await http.post(
            B + "/items/studio-draft/approve",
            headers=headers,
            json={**approval, "expected_revision": 2},
        )
        assert stale.status_code == 409, stale.text
        approved = await http.post(
            B + "/items/studio-draft/approve", headers=headers, json=approval
        )
        assert approved.status_code == 200 and approved.json()["content"]["status"] == "approved", (
            approved.text
        )
        assert approved.json()["content"]["actor"]["user_id"] == "reviewer"
        assert (
            await http.post(
                B + "/items/studio-draft/receipt",
                headers=headers,
                json={
                    "profile_id": PROFILE,
                    "expected_revision": 4,
                    "confirmed_manual_publication": True,
                },
            )
        ).status_code == 403
    async with browser(h, "owner") as (http, headers):
        exported = await http.post(
            B + "/items/studio-draft/export",
            headers=headers,
            json={"profile_id": PROFILE, "expected_revision": 4},
        )
        assert exported.status_code == 200 and "Revised human-authored" in exported.text, (
            exported.text
        )
        assert exported.headers["cache-control"] == "no-store"
        receipt = {
            "profile_id": PROFILE,
            "expected_revision": 4,
            "confirmed_manual_publication": True,
        }
        published = await http.post(
            B + "/items/studio-draft/receipt", headers=headers, json=receipt
        )
        assert published.status_code == 200 and published.json()["provider"] == "manual", (
            published.text
        )
        assert published.json()["external_url"] is None and published.json()["external_id"] is None
        assert published.json()["content"]["status"] == "published"
    async with browser(h, "member") as (http, headers):
        latest = published.json()["content"]
        version = await http.post(
            B + "/items/studio-draft/versions",
            headers=headers,
            json={
                "profile_id": PROFILE,
                "expected_revision": latest["revision"],
                "body": "A new draft requiring fresh review.",
                "change_reason": "Revise after manual publication",
            },
        )
        assert (
            version.status_code == 200
            and version.json()["status"] == "drafting"
            and version.json()["publication_provider"] is None
        ), version.text
        assert (
            await http.post(
                B + "/items/studio-draft/export",
                headers=headers,
                json={"profile_id": PROFILE, "expected_revision": version.json()["revision"]},
            )
        ).status_code == 409

    audit = await h.db.audit_logs.find({"org_id": "org-a", "action": "content.approve"}).to_list(
        None
    )
    assert any(r["outcome"] == "allowed" and r["actor"]["user_id"] == "reviewer" for r in audit)
    assert any(r["outcome"] == "denied" and r["actor"]["user_id"] == "member" for r in audit)
    assert "Human-authored" not in str(audit) and "Revised human-authored" not in str(audit)
    assert all(r["key_id"] == "unauthenticated" for r in audit)


async def test_planning_profiles_evidence_and_same_id_search_keep_every_source_boundary(creator_db):
    h = creator_db
    async with browser(h, "member") as (http, headers):
        for path in ("/profiles", "/profiles/" + PROFILE, "/profiles/" + PROFILE + "/pillars"):
            r = await http.get(B + path, headers=headers)
            assert r.status_code == 200, r.text
        discovery = await http.post(
            B + "/discover",
            headers=headers,
            json={"profile_id": PROFILE, "project_id": "payments-api", "limit": 5},
        )
        assert discovery.status_code == 200 and "PRIVATE_CREATOR_B" not in discovery.text, (
            discovery.text
        )
        idea = await http.post(
            B + "/ideas",
            headers=headers,
            json={
                "profile_id": PROFILE,
                "idea_id": "studio-idea",
                "title": "Fictional retry plan",
                "description": "Plan human-authored guidance.",
                "platforms": ["linkedin"],
                "origin": {"type": "manual", "project_id": "payment-service"},
                "priority": 0.5,
            },
        )
        assert idea.status_code == 201, idea.text
        prep = await http.post(
            B + "/prepare-brief",
            headers=headers,
            json={
                "profile_id": PROFILE,
                "idea_id": "studio-idea",
                "platform": "linkedin",
                "project_id": "payment-service",
            },
        )
        assert prep.status_code == 200, prep.text
        brief = await http.post(
            B + "/briefs",
            headers=headers,
            json={
                "profile_id": PROFILE,
                "brief_id": "studio-brief",
                "idea_id": "studio-idea",
                "platform": "linkedin",
                "objective": "Evidence-backed public guidance",
                "primary_message": "Explain bounded retries.",
                "research_project_id": "payment-retry-research",
                "claim_ids": ["idempotency-key-required"],
                "citations": [
                    {"claim_id": "idempotency-key-required", "citation_id": "provider-guide"}
                ],
            },
        )
        assert brief.status_code == 201 and brief.json()["claim_refs"], brief.text
        novelty = await http.post(
            B + "/novelty",
            headers=headers,
            json={
                "profile_id": PROFILE,
                "title": "Retry guidance",
                "description": "Public retries",
                "method": "lexical",
            },
        )
        assert novelty.status_code == 200, novelty.text
        history = await http.post(
            B + "/search",
            headers=headers,
            json={
                "profile_id": PROFILE,
                "query": "retry",
                "method": "hybrid",
                "kinds": ["content"],
                "token_budget": 2000,
            },
        )
        assert history.status_code == 200 and "PRIVATE_CREATOR_B" not in history.text, history.text
        assert history.json()["items"] and "PRIVATE_CREATOR_A" in history.text
        plan = await http.post(
            B + "/repurpose/studio-idea",
            headers=headers,
            json={
                "profile_id": PROFILE,
                "source_kind": "idea",
                "targets": ["medium"],
                "method": "lexical",
            },
        )
        assert plan.status_code == 200, plan.text
        foreign = await http.get(
            B.replace("org-a", "org-b") + "/profiles/" + PROFILE, headers=headers
        )
        assert foreign.status_code == 403
        assert (
            await http.get(B + "/profiles/foreign-profile/records/items", headers=headers)
        ).status_code == 403

    # A separate verified person receives read-A and write-B, never their product.
    await h.governance.issue(
        "org-a",
        "owner",
        Issue(
            subject_id="other",
            role="contributor",
            parent_id=h.root,
            expected_parent_generation=h.root_generation,
            tuples=tuple(
                Grant(action=a, resource_kind=k, selector="include", ids=ids)
                for a, k, ids in (
                    ("content:read", "creator_profile", (PROFILE,)),
                    ("content:write", "creator_profile", (PROFILE,)),
                    ("content:read", "project", ("payment-service",)),
                    ("content:write", "project", ("payments-api",)),
                )
            ),
            expires_at=utc_now() + timedelta(hours=1),
        ),
    )
    async with browser(h, "other") as (http, headers):
        a = await h.db.content_items.find_one({"org_id": "org-a", "project_ids": "payment-service"})
        assert (await http.get(P + "/items/" + a["content_id"], headers=headers)).status_code == 200
        denied = await http.patch(
            B + "/items/" + a["content_id"],
            headers=headers,
            json={
                "profile_id": PROFILE,
                "expected_revision": a["revision"],
                "title": "Must not write A",
            },
        )
        assert denied.status_code == 404
        assert (await h.db.content_items.find_one({"_id": a["_id"]}))["title"] == a["title"]
        body = {
            "profile_id": PROFILE,
            "content_id": "write-only-b",
            "platform": "linkedin",
            "content_type": "post",
            "title": "Write-only public guidance",
            "body": "Public supplied prose",
            "project_ids": ["payments-api"],
        }
        created = await http.post(B + "/items", headers=headers, json=body)
        assert created.status_code == 201, created.text
        assert (await http.get(P + "/items/write-only-b", headers=headers)).status_code == 404
        found = await http.post(
            B + "/search",
            headers=headers,
            json={
                "profile_id": PROFILE,
                "query": "PRIVATE_CREATOR",
                "method": "hybrid",
                "kinds": ["content"],
            },
        )
        assert found.status_code == 200 and "PRIVATE_CREATOR_B" not in found.text, found.text
        assert "write-only-b" not in found.text
