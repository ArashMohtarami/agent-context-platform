"""Real local-model/mongot measurements; repeated synthetic text is not relevance evidence."""

import asyncio
import os
from datetime import timedelta
from pathlib import Path
from time import perf_counter

import pytest
from v6_knowledge_fixtures import prepare_knowledge

from app.models.common import utc_now
from app.models.skill_chunk import SkillChunkDocument
from app.repositories.context_repository import MongoProjectContextRepository
from app.repositories.memory_repository import MongoMemoryRepository
from app.repositories.skill_chunk_repository import MongoSkillChunkRepository
from app.security.tenant import TenantContext, tenant_scope
from app.services.embedding_service import EmbeddingService, build_embedding_service
from app.services.retrieval_service import RetrievalService
from tests.integration.test_v6_acceptance_performance import distribution, evidence
from tests.integration.test_v6_policy_fencing import browser
from tests.v6_legacy_integration import unified_search_ready
from tests.v6_performance_fixtures import seed_reference_background

pytestmark = pytest.mark.integration


async def test_measured_hybrid_search_with_real_model_and_matching_tenant_chunks(
    managed_agents_db, tmp_path, monkeypatch
):
    import torch

    torch.set_num_threads(2)
    h = await prepare_knowledge(managed_agents_db)
    settings = h.app.state.settings.model_copy(
        update={
            "embedding_enabled": True,
            "embedding_device": "cpu",
            "embedding_model_cache_directory": Path(os.environ["V6_ACCEPTANCE_MODEL_CACHE"]),
            "embedding_allow_downloads": False,
        }
    )
    embedder = build_embedding_service(settings)
    assert embedder is not None
    started = perf_counter()
    await embedder.start()
    load_ms = (perf_counter() - started) * 1000
    h.app.state.settings, h.app.state.embeddings = settings, embedder
    count = int(os.environ.get("V6_PERFORMANCE_CHUNKS", "100000"))
    assert count >= 10000 and count % 1000 == 0
    # A declared throughput corpus: 32 real embeddings per tenant repeated
    # across unique deterministic chunk keys. It cannot prove semantic quality.
    prototypes = {
        org: [
            f"PRIVATE_SENTINEL_{org[-1].upper()} isolation retry evidence topic {index}. "
            "Retry writes use bounded attempts and an idempotency key."
            for index in range(32)
        ]
        for org in ("org-a", "org-b")
    }
    vectors = {org: await embedder.embed_chunks(texts) for org, texts in prototypes.items()}
    await h.db.skill_chunks.delete_many({})
    seeded = perf_counter()
    try:
        for org in prototypes:
            with tenant_scope(TenantContext(org)):
                for offset in range(0, count, 1000):
                    rows = []
                    for index in range(offset, offset + 1000):
                        vector = vectors[org][index % 32]
                        row = SkillChunkDocument(
                            skill_id="retry-skill",
                            skill_version="1.0.0",
                            chunk_id=f"performance-{index:06d}",
                            chunk_index=index,
                            content=prototypes[org][index % 32],
                            tokens=32,
                            embedding=list(vector.values),
                            embedding_model=vector.model,
                            embedding_dimensions=vector.dimensions,
                            embedding_generated_at=utc_now(),
                        )
                        rows.append(row.model_dump(by_alias=True))
                    await h.db.skill_chunks.insert_many(rows)
        assert {
            org: await h.db.skill_chunks.count_documents({"org_id": org}) for org in prototypes
        } == {"org-a": count, "org-b": count}
        seed_seconds = perf_counter() - seeded
        reference_population = await seed_reference_background(h, vectors, prototypes)
        # Include real context/shared-memory candidates in the foreground query.
        # Private agent memory remains outside the human search envelope.
        await unified_search_ready(h.db, embedder.dimensions)
        body = {
            "query": "isolation retry idempotency",
            "project_id": "payment-service",
            "sources": ["active_skill", "project_context", "memory"],
            "token_budget": 4096,
            "limit": 10,
        }
        url = "/api/v6/organizations/org-a/knowledge/search"
        async with browser(h, "member") as (client, headers):
            # Index readiness and document visibility are separate. Require
            # actual results from both methods before the timed warm workload.
            for _ in range(8):
                response = await client.post(url, headers=headers, json=body)
                assert response.status_code == 200, response.text
                diagnostics = response.json()["retrieval"]["diagnostics"]
                assert set(response.json()["retrieval"]["searched_sources"]) == {
                    "active_skill",
                    "project_context",
                    "memory",
                }
                if diagnostics["vector_matches"] and diagnostics["lexical_matches"]:
                    break
                await asyncio.sleep(6.2)
            else:
                pytest.fail("real local-model corpus did not become searchable")
            repositories = {
                "skill": MongoSkillChunkRepository,
                "context": MongoProjectContextRepository,
                "memory": MongoMemoryRepository,
            }
            parts = {name: [] for name in ("embedding", "merge")}
            parts.update(
                {
                    f"{source}.{method}": []
                    for source in repositories
                    for method in ("lexical", "vector")
                }
            )

            def timed_async(kind, original):
                async def measured(*args, **kwargs):
                    started = perf_counter()
                    try:
                        return await original(*args, **kwargs)
                    finally:
                        parts[kind].append((perf_counter() - started) * 1000)

                return measured

            monkeypatch.setattr(
                EmbeddingService,
                "embed_query",
                timed_async("embedding", EmbeddingService.embed_query),
            )
            for source, repository in repositories.items():
                for kind in ("lexical", "vector"):
                    method = "text_search" if kind == "lexical" else "vector_search"
                    monkeypatch.setattr(
                        repository,
                        method,
                        timed_async(f"{source}.{kind}", getattr(repository, method)),
                    )
            original_rank = RetrievalService.rank_context

            def measured_rank(*args, **kwargs):
                started = perf_counter()
                try:
                    return original_rank(*args, **kwargs)
                finally:
                    parts["merge"].append((perf_counter() - started) * 1000)

            monkeypatch.setattr(RetrievalService, "rank_context", measured_rank)
            elapsed = []
            # The shipped session POST boundary permits ten security-action
            # requests per user/minute, including read-only search POSTs.
            # Honor it: establish a fresh real minute, then sample at <=10/min.
            # No throttle state is deleted and no auth check is monkeypatched.
            await asyncio.sleep(60 - utc_now().timestamp() % 60)
            await asyncio.sleep(0.25)
            for _ in range(20):
                started = perf_counter()
                response = await client.post(url, headers=headers, json=body)
                elapsed.append((perf_counter() - started) * 1000)
                assert response.status_code == 200, response.text
                assert "PRIVATE_SENTINEL_A" in response.text
                assert "PRIVATE_SENTINEL_B" not in response.text
                assert "PRIVATE_AGENT_SENTINEL" not in response.text
                assert 0 < len(response.json()["results"]) <= 10
                diagnostics = response.json()["retrieval"]["diagnostics"]
                assert diagnostics["vector_matches"] and diagnostics["lexical_matches"]
                await asyncio.sleep(6.2)
        assert all(len(samples) == 20 for samples in parts.values())
        measured_parts = {name: distribution(samples) for name, samples in parts.items()}
        # A broad reviewed selector must also work above the bounded ID-
        # preselection ceiling. Exercise real human governance and a new
        # session; never replace the authenticated policy with a test principal.
        from app.identity.models import MANAGEMENT_ACTIONS
        from app.management.memberships import Memberships
        from app.management.models import RootApproval
        from app.security.policy.models import Grant, Issue, Selector

        broad = tuple(
            Grant(action=action, resource_kind="skill", selector="all_in_org")
            for action in ("search:read", "skills:read")
        )
        root = await h.db.policy_assignments.find_one({"_id": h.root})
        with tenant_scope(TenantContext("org-a")):
            approved = await Memberships(h.engine).operator_approve_root(
                "org-a",
                "owner",
                RootApproval(
                    tuples=tuple(
                        Grant.model_validate(g)
                        for g in root["tuples"]
                        if g["action"] in MANAGEMENT_ACTIONS
                    ),
                    ceiling=tuple(Grant.model_validate(g) for g in root["ceiling"]) + broad,
                    targets=Selector.model_validate(root["targets"]),
                    allowed_roles=tuple(root["allowed_roles"]),
                    expires_at=root["expires_at"],
                ),
                "fictional-independent-all-skill-search-review",
            )
            await h.governance.issue(
                "org-a",
                "owner",
                Issue(
                    subject_id="member",
                    parent_id=h.root,
                    expected_parent_generation=approved["generation"],
                    tuples=(*h.knowledge_grants, *broad),
                    expires_at=utc_now() + timedelta(days=1),
                ),
            )
        async with browser(h, "member") as (client, headers):
            all_response = await client.post(
                url, headers=headers, json={**body, "sources": ["active_skill"]}
            )
            assert all_response.status_code == 200, all_response.text
            assert "PRIVATE_SENTINEL_A" in all_response.text
            assert "PRIVATE_SENTINEL_B" not in all_response.text
            all_diagnostics = all_response.json()["retrieval"]["diagnostics"]
            assert all_diagnostics["vector_matches"] and all_diagnostics["lexical_matches"]
        evidence(
            tmp_path,
            "hybrid-search-performance",
            {
                "chunks_per_org": count,
                "matching_logical_ids": True,
                "corpus": "32 repeated synthetic texts per org; unique chunk keys; "
                "not relevance evidence",
                "model": embedder.model,
                "dimensions": embedder.dimensions,
                "model_load_ms": load_ms,
                "seed_seconds": seed_seconds,
                "mixed_background_population": reference_population,
                "torch_threads": torch.get_num_threads(),
                "warm_end_to_end": distribution(elapsed),
                "parts": measured_parts,
                "reviewed_all_in_org_skill_search_verified": True,
                "parts_note": "lexical/vector overlap; durations include actual repository work",
                "target_p95_ms": 2000,
                "concurrency": 1,
                "sampling": "paced at <=10 session POST requests/minute; "
                "waits excluded from latency",
                "scope": "real session action/resource adapter; "
                "skill/context/shared-memory search; "
                "full declared background populations; unissued disabled background clients",
            },
        )
        assert distribution(elapsed)["p95_ms"] <= 2000
    finally:
        await embedder.close()
