"""Real persistence, execution, replay and revocation fences for ingestion."""

import pytest
from v6_isolation_fixtures import token

from app.models.research import ResearchSourceDocument
from app.repositories.credential_repository import MongoCredentialResolver
from app.repositories.research_repository import (
    MongoIngestionJobRepository,
    MongoResearchChunkRepository,
    MongoResearchDocumentRepository,
    MongoSourceSnapshotRepository,
)
from app.research.providers import IngestionPayload, ManualSourceProvider
from app.security.job_authority import JobAuthorityError, MongoJobAuthorityChecker
from app.security.tenant import TenantContext, tenant_scope
from app.security.tenant_policy import policy_of, policy_scope
from app.services.auth_service import AuthService
from app.services.research_ingestion_service import (
    ResearchIngestionJobNotFoundError,
    ResearchIngestionService,
)

pytestmark = pytest.mark.integration


def ingestion(db, **kwargs):
    return ResearchIngestionService(
        lambda rp: MongoSourceSnapshotRepository(db, rp),
        lambda rp: MongoResearchDocumentRepository(db, rp),
        lambda rp: MongoResearchChunkRepository(db, rp),
        lambda rp: MongoIngestionJobRepository(db, rp),
        None,
        authority_checker=MongoJobAuthorityChecker(db),
        **kwargs,
    )


async def source(db, org):
    # A registered, owned manual source; submitted bytes remain inert.
    await db["research_sources"].update_one({"org_id": org}, {"$set": {"source_type": "manual"}})
    raw = await db["research_sources"].find_one({"org_id": org})
    return ResearchSourceDocument.model_validate(raw, context={"from_storage": True})


async def test_matching_job_ids_never_share_payloads_or_replay_across_orgs(isolated_database):
    db = isolated_database
    jobs = []
    for label in ("a", "b"):
        principal = await AuthService(MongoCredentialResolver(db)).authenticate(token(label))
        with (
            tenant_scope(TenantContext(principal.org_id)),
            policy_scope(policy_of(principal), "research:write"),
        ):
            service = ingestion(db)
            registered = await source(db, principal.org_id)
            payload = IngestionPayload(
                data=b"Same job natural key and body.",
                content_type="text/plain",
                title="same",
                language="en",
            )
            result = await service.ingest(
                source=registered, payload=payload, manually_provided=True
            )
            repeated = await service.ingest(
                source=registered, payload=payload, manually_provided=True
            )
            assert repeated.idempotent and repeated.job.id == result.job.id
            assert (
                await service.get_job(
                    research_project_id=registered.research_project_id, job_id=result.job.job_id
                )
            ).org_id == principal.org_id
            jobs.append(result.job)
    assert jobs[0].job_id == jobs[1].job_id and jobs[0].id != jobs[1].id
    assert jobs[0].initiating_authority.org_id != jobs[1].initiating_authority.org_id
    with tenant_scope(TenantContext("org-a")):
        with pytest.raises(JobAuthorityError):
            await MongoJobAuthorityChecker(db).check(jobs[1].initiating_authority)
    await db["api_keys"].update_one({"org_id": "org-a"}, {"$set": {"status": "revoked"}})
    with tenant_scope(TenantContext("org-a")):
        with pytest.raises(ResearchIngestionJobNotFoundError):
            await ingestion(db).get_job(
                research_project_id=jobs[0].research_project_id, job_id=jobs[0].job_id
            )
    with pytest.raises(JobAuthorityError, match="worker"):
        MongoJobAuthorityChecker(db, worker_schema_version=36)


async def test_revocation_during_provider_work_prevents_result_persistence(isolated_database):
    db = isolated_database

    class RevokingProvider(ManualSourceProvider):
        async def extract(self, registered, payload):
            result = await super().extract(registered, payload)
            await db["api_keys"].update_one({"org_id": "org-a"}, {"$set": {"status": "revoked"}})
            return result

    principal = await AuthService(MongoCredentialResolver(db)).authenticate(token("a"))
    before = await db["research_documents"].count_documents({"org_id": "org-a"})
    with tenant_scope(TenantContext("org-a")), policy_scope(policy_of(principal), "research:write"):
        service = ingestion(db, manual_provider=RevokingProvider())
        registered = await source(db, "org-a")
        with pytest.raises(JobAuthorityError):
            await service.ingest(
                source=registered,
                payload=IngestionPayload(
                    data=b"Do not persist after revocation.",
                    content_type="text/plain",
                    title="revoked",
                    language="en",
                ),
                manually_provided=True,
            )
    assert await db["research_documents"].count_documents({"org_id": "org-a"}) == before


async def test_cached_mcp_ingestion_result_rechecks_source_authority(isolated_database):
    import httpx

    from app.repositories.agent_repository import MongoAgentClientRepository
    from app.repositories.idempotency_repository import MongoGatewayIdempotencyRepository
    from app.security.scopes import ALL_SCOPES
    from app.services.gateway_idempotency_service import GatewayIdempotencyService
    from tests.integration.test_v6_mcp_isolation import application

    db = isolated_database
    await db["api_keys"].update_one({"org_id": "org-a"}, {"$set": {"client_id": "agent-one"}})
    await db["agent_clients"].update_one(
        {"org_id": "org-a"}, {"$set": {"scopes": list(ALL_SCOPES)}}
    )
    principal = await AuthService(
        MongoCredentialResolver(db), MongoAgentClientRepository(db)
    ).authenticate(token("a"))
    with tenant_scope(TenantContext("org-a")), policy_scope(policy_of(principal), "research:write"):
        registered = await source(db, "org-a")
        result = await ingestion(db).ingest(
            source=registered,
            payload=IngestionPayload(
                data=b"private cached job", content_type="text/plain", title="cached", language="en"
            ),
            manually_provided=True,
        )
    app = application(db)
    app.state.gateway_idempotency_service = GatewayIdempotencyService(
        MongoGatewayIdempotencyRepository(db), retention_seconds=120
    )
    headers = {"Authorization": "Bearer " + token("a")}
    payload = {
        "operation": "ingest_research_source",
        "idempotency_key": "same-retry",
        "payload_hash": "a" * 64,
    }
    prefix = "/api/v4/gateway/idempotency/reservations"
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as http:
        reserved = await http.post(prefix, json=payload, headers=headers)
        assert reserved.status_code == 200 and reserved.json()["decision"] == "proceed"
        cached = {
            "text": "PRIVATE_CACHED_RESULT",
            "data": {
                "research_ingestion": {
                    "source_id": registered.source_id,
                    "job": {
                        "research_project_id": registered.research_project_id,
                        "job_id": result.job.job_id,
                    },
                }
            },
        }
        completed = await http.post(
            prefix + "/complete", json={**payload, "result": cached}, headers=headers
        )
        assert completed.status_code == 204, completed.text
        replay = await http.post(prefix, json=payload, headers=headers)
        assert replay.status_code == 200 and replay.json()["decision"] == "replay", replay.text
        await db["research_sources"].update_one(
            {"org_id": "org-a"}, {"$set": {"status": "archived"}}
        )
        denied = await http.post(prefix, json=payload, headers=headers)
        assert denied.status_code == 403, denied.text
        assert "PRIVATE_CACHED_RESULT" not in denied.text
