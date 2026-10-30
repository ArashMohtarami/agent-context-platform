"""Fictional quality evidence over real auth, CPU embeddings and owned mongot."""

import asyncio
import json
import os
from datetime import datetime, timedelta
from pathlib import Path
from time import perf_counter

import pytest

from app.identity.models import MANAGEMENT_ACTIONS
from app.managed_agents.models import ClientApproval, CredentialIssue
from app.management.memberships import Memberships
from app.management.models import RootApproval
from app.models.common import utc_now
from app.models.memory import MemoryDocument
from app.models.project_context import ProjectContextDocument
from app.models.skill import SkillDocument
from app.models.skill_chunk import SkillChunkDocument
from app.repositories.skill_chunk_repository import MongoSkillChunkRepository
from app.repositories.skill_repository import MongoSkillRepository
from app.security.policy.models import Grant, Issue, Selector
from app.security.tenant import TenantContext, tenant_scope
from app.security.tenant_policy import TenantPolicy, policy_scope
from app.services.skill_service import SkillService
from app.skills.chunker import MarkdownSkillChunker, estimate_tokens
from app.skills.importer import SkillImporter
from app.skills.parser import SkillParser
from benchmarks.retrieval.live import Evaluation, load_model, run
from benchmarks.retrieval.scorer import ROOT, Attempt, Report, digest, identity, load_dataset
from tests.v6_knowledge_fixtures import prepare_knowledge
from tests.v6_legacy_integration import unified_search_ready
from tests.v6_managed_agent_fixtures import g

pytestmark = pytest.mark.integration


async def issue_reader(h):
    """Separate fictional operator, owner approver and recipient. No fixture bypass."""
    root = await h.db.policy_assignments.find_one({"_id": h.root})
    expiry = utc_now() + timedelta(hours=1)
    base = (g("context:read"), g("search:read"))
    initial = await h.agents.approve(
        "org-a",
        "owner",
        ClientApproval(
            client_id="benchmark-reader",
            name="Fictional benchmark reader",
            subject_id="member",
            parent_id=h.root,
            expected_parent_generation=root["generation"],
            tuples=base,
            expires_at=expiry,
        ),
    )
    rights = (
        *base,
        g("skills:read", "skill", ("fact-retry", "fact-redact")),
        g("search:read", "skill", ("fact-retry", "fact-redact")),
        g("memory:read"),
        g("memory:read", "client_memory", ("benchmark-reader",)),
        g("search:read", "client_memory", ("benchmark-reader",)),
        g("memory:read", "organization_knowledge", ("org-a",)),
        g("search:read", "organization_knowledge", ("org-a",)),
    )
    own_issue = (g("credentials:issue:own", "member", ("member",)),)
    with tenant_scope(TenantContext("org-a")):
        updated = await Memberships(h.engine).operator_approve_root(
            "org-a",
            "owner",
            RootApproval(
                tuples=tuple(
                    Grant.model_validate(t)
                    for t in root["tuples"]
                    if t["action"] in MANAGEMENT_ACTIONS
                ),
                # Replaced the prerequisite corpus: retire its now-absent skill
                # references and explicitly review only the new imported skills.
                ceiling=tuple(
                    Grant.model_validate(t)
                    for t in root["ceiling"]
                    if t["resource_kind"] != "skill"
                )
                + rights
                + own_issue,
                targets=Selector(mode="include", ids=("member",)),
                allowed_roles=("contributor",),
                expires_at=utc_now() + timedelta(hours=2),
            ),
            "fictional-phase6-operator-permitted-corpus-review",
        )
    await h.governance.issue(
        "org-a",
        "owner",
        Issue(
            subject_id="member",
            parent_id=h.root,
            expected_parent_generation=updated["generation"],
            tuples=rights + own_issue,
            expires_at=expiry,
        ),
    )
    approved = await h.agents.approve(
        "org-a",
        "owner",
        ClientApproval(
            client_id="benchmark-reader",
            name="Fictional benchmark reader",
            subject_id="member",
            expected_revision=initial["revision"],
            parent_id=h.root,
            expected_parent_generation=updated["generation"],
            tuples=rights,
            expires_at=expiry,
        ),
    )
    receipt = await h.agents.issue(
        "org-a",
        "member",
        "benchmark-reader",
        CredentialIssue(
            issuance_id="fictional-phase6-once",
            expected_client_revision=approved["revision"],
            tuples=rights,
            # Deliberately shorter than the client's one-hour persisted bound.
            expires_at=utc_now() + timedelta(minutes=30),
        ),
    )
    assert receipt["delivered"] and receipt["token"]
    return approved, receipt


async def seed_corpus(h, data, embedder):
    # A benchmark fixture, never advertised as an empty-install commission path.
    for collection in ("project_context", "memories", "skills", "skill_chunks"):
        await h.db[collection].delete_many({})
    records = data["corpus"]["records"]
    vectors = await embedder.embed_chunks([r["content"] for r in records])
    runtime_ids, skill_runtime = {}, {}
    for org in ("org-a", "org-b"):
        with (
            tenant_scope(TenantContext(org)),
            policy_scope(
                TenantPolicy(
                    org, "fictional-licensed-fixture-import", (), operator=True, engine=h.engine
                )
            ),
        ):
            importer = SkillImporter(
                SkillParser(max_file_bytes=32000),
                MarkdownSkillChunker(),
                SkillService(MongoSkillRepository(h.db), MongoSkillChunkRepository(h.db), embedder),
            )
            imported = await importer.import_directory(ROOT / "skills" / org)
            assert imported.new == 2 and imported.chunks_embedded == 2
            for r, vector in zip(records, vectors, strict=True):
                if r["org_id"] != org:
                    continue
                if r["source"] == "active_skill":
                    chunks = await h.db.skill_chunks.find(
                        {"org_id": org, "skill_id": r["id"]}
                    ).to_list(10)
                    # Canonical SkillParser.parse_text strips outer body whitespace.
                    assert len(chunks) == 1 and chunks[0]["content"] == r["content"].strip()
                    row = chunks[0]
                    if org == "org-a":
                        skill_runtime[r["id"], row["chunk_id"]] = str(row["_id"])
                    # Importer timestamps are real; pin source recency outside timed query.
                    for collection in ("skills", "skill_chunks"):
                        await h.db[collection].update_many(
                            {"org_id": org, "skill_id": r["id"]},
                            {
                                "$set": {
                                    "created_at": datetime.fromisoformat(r["updated_at"]),
                                    "updated_at": datetime.fromisoformat(r["updated_at"]),
                                }
                            },
                        )
                else:
                    common = dict(
                        content=r["content"],
                        status=r["state"],
                        revision=r["revision"],
                        source="fictional-benchmark-v1",
                        external_id=r["id"],
                        created_at=datetime.fromisoformat(r["updated_at"]),
                        updated_at=datetime.fromisoformat(r["updated_at"]),
                        importance=0.5,
                        embedding=list(vector.values),
                        embedding_model=vector.model,
                        embedding_dimensions=vector.dimensions,
                        embedding_generated_at=utc_now(),
                    )
                    if r["source"] == "memory":
                        scope = "global" if r["project_id"] is None else "project_agent"
                        document = MemoryDocument(
                            **common,
                            scope=scope,
                            type="decision",
                            confidence=0.9,
                            project_id=r["project_id"],
                            client_id=r["client_id"],
                            summary=r["title"],
                        )
                        collection = "memories"
                    else:
                        document = ProjectContextDocument(
                            **common,
                            project_id=r["project_id"],
                            type="architecture_decision",
                            title=r["title"],
                        )
                        collection = "project_context"
                    row = document.model_dump(by_alias=True)
                    await h.db[collection].insert_one(row)
                runtime_ids[str(row["_id"])] = identity(r)
    assert len(runtime_ids) == 71
    # Every quality source is a real-model row that satisfies current schemas.
    for collection, model in (
        ("skills", SkillDocument),
        ("skill_chunks", SkillChunkDocument),
        ("memories", MemoryDocument),
        ("project_context", ProjectContextDocument),
    ):
        for row in await h.db[collection].find({}).to_list(200):
            with tenant_scope(TenantContext(row["org_id"])):
                model.model_validate(row, context={"from_storage": True})
            if collection != "skills":
                assert row["embedding_dimensions"] == 384 and len(row["embedding"]) == 384
                assert row["embedding_model"] == embedder.model
    return runtime_ids, skill_runtime


async def test_frozen_corpus_real_retrieval(managed_agents_db):
    started = perf_counter()
    data = load_dataset()
    h = await prepare_knowledge(managed_agents_db)
    embedder, model = await load_model(os.environ["ACP_FOCUSED_MODEL_CACHE"], data["settings"])
    h.app.state.embeddings = embedder
    try:
        runtime_ids, skill_runtime = await seed_corpus(h, data, embedder)
        approved, receipt = await issue_reader(h)
        await unified_search_ready(h.db, embedder.dimensions)
        evaluator = Evaluation(
            h.db, h.app.state.auth, receipt["token"], embedder, runtime_ids, data["settings"]
        )
        evaluator.skill_runtime = skill_runtime
        # Readiness is a separately declared probe, not a timed labeled query.
        probe = dict(data["queries"]["queries"][0], query="ORL-CURSOR-9")
        for _ in range(120):
            lex, ld = await evaluator.retrieve(probe, "lexical")
            hybrid, hd = await evaluator.retrieve(probe, "hybrid")
            if lex and hybrid and ld["lexical_matches"] and hd["vector_matches"]:
                break
            await asyncio.sleep(0.5)
        else:
            pytest.fail("real corpus failed lexical/vector queryability after READY")
        # Verify the actual permitted export against frozen scope BEFORE quality.
        # A filtered-away intended record is an invalid comparison, not a miss.
        await evaluator.retrieve(probe, "markdown")
        for key, snapshot in evaluator.snapshots.items():
            org, project, client, _ = key
            expected = {
                identity(r)
                for r in data["corpus"]["records"]
                if r["state"] == "active"
                and r["org_id"] == org
                and r["project_id"] in (None, project)
                and r["client_id"] in (None, client)
            }
            assert {r["identity"] for r in snapshot} == expected
        indexes = {
            name: await (await h.db[name].list_search_indexes()).to_list(None)
            for name in ("skill_chunks", "project_context", "memories")
        }
        identities = {
            "freeze": data["freeze"],
            "model": model,
            "indexes": json.loads(json.dumps(indexes, default=str)),
            "estimator_sha256": digest(Path("app/skills/chunker.py")),
            "runtime_records": runtime_ids,
            "access_grants": [
                t.model_dump(mode="json") if hasattr(t, "model_dump") else t
                for t in approved["tuples"]
            ],
        }
        directory = Path(os.environ["ACP_FOCUSED_RETRIEVAL_REPORT"])
        journal = directory / "retrieval-attempts.jsonl"
        report = await run(
            evaluator,
            data,
            identities,
            {
                "setup_ms": (perf_counter() - started) * 1000,
                "download_ms": 0,
                "human_effort": "not measured; integration fixture",
                "readiness": "six READY/queryable indexes, real probes, exact authorized export",
                "boundary_controls": "pending; not approved",
            },
            journal=journal,
        )
        output = directory / "retrieval-report.json"
        # Retain the completed query study even if a later boundary control fails.
        output.write_text(report.model_dump_json(indent=2) + "\n")
        output.chmod(0o600)
        # Ensure schema roundtrip, exact attempts and current credential refusal.
        assert len(report.attempts) == 723
        assert len([a for a in report.attempts if a.stage == "quality"]) == 180
        negative = dict(probe, project_id="payments-api")
        boundary = await evaluator.attempt(negative, "hybrid", "quality", 0)
        await h.agents.revoke(
            "org-a", "owner", "benchmark-reader", receipt["revision"], key_id=receipt["key_id"]
        )
        revoked = [
            await evaluator.attempt(probe, arm, "quality", 0)
            for arm in ("markdown", "lexical", "hybrid")
        ]
        report.setup["boundary_controls"] = {
            "second_project": boundary.model_dump(),
            "revoked": [r.model_dump() for r in revoked],
        }
        output.write_text(report.model_dump_json(indent=2) + "\n")
        output.chmod(0o600)
        assert boundary.status == "denied" and not boundary.items
        assert all(a.status == "denied" and not a.items for a in revoked)
        assert [
            Attempt.model_validate_json(line) for line in journal.read_text().splitlines()
        ] == report.attempts
        validated = Report.model_validate_json(output.read_text())
        assert all(a.status == "ok" for a in validated.attempts), (
            "retain query errors in private report"
        )
        assert report.comparisons["targets"]["zero_forbidden_all_stages"], (
            "forbidden returns block arm"
        )
        for key, snapshot in evaluator.snapshots.items():
            org, project, client, _ = key
            expected = {
                identity(r)
                for r in data["corpus"]["records"]
                if r["state"] == "active"
                and r["org_id"] == org
                and r["project_id"] in (None, project)
                and r["client_id"] in (None, client)
            }
            assert {r["identity"] for r in snapshot} == expected
        assert all(
            sum(estimate_tokens(i.content) for i in a.items) <= 4000 for a in report.attempts
        )
        # Target misses do not fail plumbing or trigger tuning; the report retains them.
    finally:
        await embedder.close()
