"""Real domain adapters and persisted ingestion/review/promotion in disposable MongoDB."""

import base64

import pytest
import pytest_asyncio

from tests.integration.test_v6_policy_fencing import browser
from tests.v6_research_console_fixtures import PROJECT, prepare_research

pytestmark = pytest.mark.integration
BASE = "/api/v6/organizations/org-a/research"
P = BASE + "/projects/" + PROJECT
TEXT = (
    "PRIVATE_RESEARCH_A evidence. Bounded evidence supports safe retry handling.\n\n"
    "<script>window.research_xss=1</script>"
)


@pytest_asyncio.fixture
async def research_db(managed_agents_db):
    h = managed_agents_db
    await prepare_research(h)
    return h


async def create_flow(client, headers, source_id="browser-evidence"):
    response = await client.post(
        BASE + "/sources",
        headers=headers,
        json={
            "research_project_id": PROJECT,
            "source_id": source_id,
            "source_type": "manual",
            "uri": "manual:" + source_id,
            "title": "Fictional evidence",
            "source_class": "user_provided",
        },
    )
    assert response.status_code == 201, response.text
    ingestion = await client.post(
        P + "/sources/" + source_id + "/ingest",
        headers=headers,
        json={"content": TEXT, "content_type": "text/plain"},
    )
    assert ingestion.status_code == 200, ingestion.text
    assert ingestion.json()["job"]["status"] == "completed"
    documents = await client.get(P + "/records/documents?source_id=" + source_id, headers=headers)
    assert documents.status_code == 200, documents.text
    doc = documents.json()["items"][0]
    chunks = await client.get(
        P
        + "/sources/"
        + source_id
        + "/snapshots/"
        + doc["snapshot_id"]
        + "/documents/"
        + doc["document_id"]
        + "/chunks",
        headers=headers,
    )
    assert chunks.status_code == 200, chunks.text
    chunk = chunks.json()["items"][0]
    claim = await client.post(
        BASE + "/claims",
        headers=headers,
        json={
            "research_project_id": PROJECT,
            "claim_id": "console-claim",
            "statement": "Safe retries need explicit evidence.",
        },
    )
    assert claim.status_code == 201, claim.text
    citation = {
        "research_project_id": PROJECT,
        "claim_id": "console-claim",
        "citation_id": "console-citation",
        "source_id": source_id,
        "snapshot_id": doc["snapshot_id"],
        "chunk_id": chunk["chunk_id"],
        "locator": chunk["locator"],
        "evidence_excerpt": "Bounded evidence supports safe retry handling.",
        "relation": "supports",
    }
    response = await client.post(BASE + "/citations", headers=headers, json=citation)
    assert response.status_code == 201, response.text
    candidate = await client.post(
        BASE + "/candidates",
        headers=headers,
        json={
            "research_project_id": PROJECT,
            "candidate_id": "console-candidate",
            "candidate_type": "project_context",
            "target_project_id": "payment-service",
            "title": "Evidence-backed retries",
            "content": "A reviewed research finding",
            "source_claim_ids": ["console-claim"],
            "source_citation_ids": ["console-citation"],
        },
    )
    assert candidate.status_code == 201, candidate.text
    return ingestion.json(), candidate.json(), citation


async def test_ingest_inspect_independent_current_review_and_controlled_promotion(research_db):
    h = research_db
    async with browser(h, "member") as (client, headers):
        ingestion, candidate, citation = await create_flow(client, headers)
        evidence = await client.get(P + "/claims/console-claim/evidence", headers=headers)
        assert evidence.status_code == 200 and len(evidence.json()["supporting_evidence"]) == 1
        assert evidence.json()["supporting_evidence"][0]["source"]["retrieved_at"]
        assert "PRIVATE_RESEARCH_B" not in evidence.text
        body = {"expected_revision": candidate["revision"], "decision": "approved"}
        assert (
            await client.post(
                P + "/candidates/console-candidate/decision", headers=headers, json=body
            )
        ).status_code == 403
        denied = await client.post(
            BASE + "/candidates/console-candidate/promote",
            headers=headers,
            json={"research_project_id": PROJECT, "expected_revision": candidate["revision"]},
        )
        assert denied.status_code == 403
        proposal = await client.post(
            BASE + "/proposals",
            headers=headers,
            json={
                "research_project_id": PROJECT,
                "proposal_id": "console-proposal",
                "skill_id": "retry-skill",
                "current_version": "1.0.0",
                "proposed_change": "Explain bounded retries",
                "proposed_patch": "--- a/retry.md\n+++ b/retry.md\n+Bounded retries",
                "source_claim_ids": ["console-claim"],
                "source_citation_ids": ["console-citation"],
                "reason": "Review evidence before Git changes",
            },
        )
        assert proposal.status_code == 201, proposal.text
        assert (
            await client.post(
                P + "/proposals/console-proposal/decision",
                headers=headers,
                json={"expected_revision": 1, "decision": "approved"},
            )
        ).status_code == 403
    original_skills = await h.db.skills.find({}).to_list(None)
    async with browser(h, "reviewer") as (reviewer, headers):
        stale = await reviewer.post(
            P + "/candidates/console-candidate/decision",
            headers=headers,
            json={"expected_revision": 999, "decision": "approved"},
        )
        assert stale.status_code == 409, stale.text
        approved = await reviewer.post(
            P + "/candidates/console-candidate/decision",
            headers=headers,
            json={"expected_revision": 1, "decision": "approved"},
        )
        assert approved.status_code == 200, approved.text
        assert approved.json()["event"]["authorization"]["owner_key_id"] is None
        assert approved.json()["event"]["target_id"] is None
        assert (
            await reviewer.post(
                P + "/candidates/console-candidate/decision",
                headers=headers,
                json={"expected_revision": 1, "decision": "rejected"},
            )
        ).status_code in {403, 409}
        patch = await reviewer.post(
            P + "/proposals/console-proposal/decision",
            headers=headers,
            json={"expected_revision": 1, "decision": "approved"},
        )
        assert patch.status_code == 200 and patch.json()["trust_level"] == "generated", patch.text
        assert patch.json()["actor"]["user_id"] == "reviewer"
        assert (
            await reviewer.post(
                BASE + "/candidates/console-candidate/promote",
                headers=headers,
                json={
                    "research_project_id": PROJECT,
                    "expected_revision": approved.json()["candidate"]["revision"],
                },
            )
        ).status_code == 403
    assert await h.db.skills.find({}).to_list(None) == original_skills
    async with browser(h, "owner") as (owner, headers):
        promoted = await owner.post(
            BASE + "/candidates/console-candidate/promote",
            headers=headers,
            json={
                "research_project_id": PROJECT,
                "expected_revision": approved.json()["candidate"]["revision"],
            },
        )
        assert promoted.status_code == 200, promoted.text
        assert promoted.json()["target"]["provenance"]["source_snapshot_ids"] == [
            citation["snapshot_id"]
        ]
        target = await owner.get(
            "/api/v6/organizations/org-a/knowledge/context/"
            + promoted.json()["target"]["target_id"]
            + "?project_id=payment-service",
            headers=headers,
        )
        assert target.status_code == 200 and target.json()["promotion_provenance"]


async def test_bounded_file_ssrf_foreign_citations_and_revoked_job_results(research_db):
    h = research_db
    async with browser(h, "member") as (client, headers):
        ingestion, _, citation = await create_flow(client, headers)
        foreign = await client.post(
            BASE + "/citations",
            headers=headers,
            json={**citation, "citation_id": "foreign-citation", "snapshot_id": h.foreign_snapshot},
        )
        assert foreign.status_code in {404, 422} and "PRIVATE_RESEARCH_B" not in foreign.text
        assert (
            await client.get(P.replace("org-a", "org-b") + "/records/sources", headers=headers)
        ).status_code == 403
        file_source = await client.post(
            BASE + "/sources",
            headers=headers,
            json={
                "research_project_id": PROJECT,
                "source_id": "file-evidence",
                "source_type": "file",
                "uri": "file:///uploads/fictional.txt",
                "title": "Fictional file",
            },
        )
        assert file_source.status_code == 201, file_source.text
        file_job = await client.post(
            P + "/sources/file-evidence/ingest",
            headers=headers,
            json={
                "content_base64": base64.b64encode(b"Fictional local file evidence").decode(),
                "content_type": "text/plain",
                "filename": "fictional.txt",
            },
        )
        assert file_job.status_code == 200 and file_job.json()["job"]["status"] == "completed", (
            file_job.text
        )
        too_large = await client.post(
            P + "/sources/file-evidence/ingest",
            headers=headers,
            json={"content": "x" * 8193, "content_type": "text/plain"},
        )
        assert too_large.status_code == 422
        job = ingestion["job"]["job_id"]
        path = P + "/sources/browser-evidence/jobs/" + job
        assert (await client.get(path, headers=headers)).status_code == 200
        await h.db.browser_sessions.update_many({"user_id": "member"}, {"$set": {"revoked": True}})
        assert (await client.get(path, headers=headers)).status_code == 401
        # A separate authorized user exercises URL controls without exhausting the
        # member's ten security mutations in this bounded browser session.
    async with browser(h, "owner") as (client, headers):
        url_source = await client.post(
            BASE + "/sources",
            headers=headers,
            json={
                "research_project_id": PROJECT,
                "source_id": "blocked-url",
                "source_type": "url",
                "uri": "http://127.0.0.1:1/private",
                "title": "Blocked local URL",
            },
        )
        assert url_source.status_code == 201, url_source.text
        blocked = await client.post(
            P + "/sources/blocked-url/ingest", headers=headers, json={"fetch_url": True}
        )
        assert blocked.status_code == 422 and "127.0.0.1" not in blocked.text
        malformed = await client.post(
            BASE + "/sources",
            headers=headers,
            json={
                "research_project_id": PROJECT,
                "source_id": "malformed-file",
                "source_type": "pdf",
                "uri": "file:///uploads/fictional.pdf",
                "title": "Fictional malformed document",
            },
        )
        assert malformed.status_code == 201, malformed.text
        failed = await client.post(
            P + "/sources/malformed-file/ingest",
            headers=headers,
            json={
                "content_base64": base64.b64encode(b"PRIVATE_INVALID_PDF").decode(),
                "content_type": "application/pdf",
                "filename": "fictional.pdf",
            },
        )
        assert failed.status_code == 422, failed.text
        failures = await client.get(P + "/records/jobs?source_id=malformed-file", headers=headers)
        assert failures.status_code == 200 and len(failures.json()["items"]) == 1, failures.text
        assert failures.json()["items"][0]["status"] == "failed"
        assert failures.json()["items"][0]["error"] == (
            "Ingestion failed within the configured source limits."
        )
        assert (
            "PRIVATE_INVALID_PDF" not in failures.text
            and "initiating_authority" not in failures.text
        )
    async with browser(h, "reviewer") as (reviewer, headers):
        for suffix in ("", "/documents"):
            denied = await reviewer.get(path + suffix, headers=headers)
            assert denied.status_code in {403, 404} and "PRIVATE_RESEARCH_A" not in denied.text


async def test_project_notes_claim_assessment_brief_and_history_use_real_domain_records(
    research_db,
):
    h = research_db
    async with browser(h, "member") as (client, headers):
        project = await client.post(
            BASE + "/projects",
            headers=headers,
            json={
                "research_project_id": "console-study",
                "name": "Fictional console study",
                "question": "What is supported by retained evidence?",
                "type": "technical_research",
                "related_project_ids": ["payment-service"],
            },
        )
        assert project.status_code == 201, project.text
        notes = {
            "research_project_id": PROJECT,
            "note_id": "console-note",
            "title": "Fictional working note",
            "body": "**Inert working evidence**",
        }
        note = await client.post(BASE + "/notes", headers=headers, json=notes)
        assert note.status_code == 201 and note.json()["actor"]["user_id"] == "member", note.text
        assert (
            await client.get(P + "/record/notes/console-note", headers=headers)
        ).status_code == 200
        patch = {"research_project_id": PROJECT, "expected_revision": 99, "body": "unsaved draft"}
        assert (
            await client.patch(BASE + "/notes/console-note", headers=headers, json=patch)
        ).status_code == 409
        updated = await client.patch(
            BASE + "/notes/console-note", headers=headers, json={**patch, "expected_revision": 1}
        )
        assert updated.status_code == 200 and updated.json()["revision"] == 2, updated.text
        claim = await client.post(
            BASE + "/claims",
            headers=headers,
            json={
                "research_project_id": PROJECT,
                "claim_id": "assessment-claim",
                "statement": "Evidence has unresolved coverage.",
            },
        )
        assert claim.status_code == 201, claim.text
        decision = {
            "expected_revision": 1,
            "status": "unsupported",
            "confidence": 0.2,
            "confidence_rationale": "No supporting evidence has been retained.",
        }
        assert (
            await client.post(
                P + "/claims/assessment-claim/decision", headers=headers, json=decision
            )
        ).status_code == 403
        brief = await client.post(
            BASE + "/briefs",
            headers=headers,
            json={
                "research_project_id": PROJECT,
                "brief_id": "console-brief",
                "scope": "Explicitly bounded fictional study",
            },
        )
        assert brief.status_code == 201 and brief.json()["status"] == "draft", brief.text
        assert (
            await client.get(P + "/record/briefs/console-brief", headers=headers)
        ).status_code == 200
    async with browser(h, "reviewer") as (reviewer, headers):
        assessed = await reviewer.post(
            P + "/claims/assessment-claim/decision", headers=headers, json=decision
        )
        assert assessed.status_code == 200 and assessed.json()["status"] == "unsupported", (
            assessed.text
        )
        assert (
            await reviewer.post(
                P + "/claims/assessment-claim/decision", headers=headers, json=decision
            )
        ).status_code in {403, 409}
        for kind in ("claims", "notes", "briefs", "events"):
            page = await reviewer.get(P + "/records/" + kind, headers=headers)
            assert page.status_code == 200 and "PRIVATE_RESEARCH_B" not in page.text
        assert (await reviewer.get(P + "/freshness", headers=headers)).status_code == 200
