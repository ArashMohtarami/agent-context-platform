"""Console projections reuse scoped repositories and the existing domain services."""

from typing import Any, Literal

from fastapi import Request

from app.api.dependencies import (
    get_research_ingestion_service,
    get_research_project_service,
    get_research_source_service,
)
from app.identity.browser import BrowserContext
from app.knowledge.boundary import resource
from app.models.research import ResearchSourceType
from app.repositories import research_repository as repositories
from app.research.console.boundary import projects, research_resource
from app.research.console.contracts import RetainedChunk, RetainedDocument
from app.schemas import research as dto
from app.security.job_authority import JobAuthorityError, MongoJobAuthorityChecker
from app.security.policy.evaluation import PolicyDenied
from app.security.policy.resources import resource_allows
from app.security.tenant_policy import current_action, current_policy

Kind = Literal[
    "sources",
    "notes",
    "claims",
    "briefs",
    "candidates",
    "proposals",
    "events",
    "snapshots",
    "documents",
    "jobs",
]
Record = (
    dto.ResearchSourceResponse
    | dto.ResearchNoteResponse
    | dto.ResearchClaimResponse
    | dto.ResearchBriefResponse
    | dto.KnowledgeCandidateResponse
    | dto.SkillUpdateProposalResponse
    | dto.PromotionEventResponse
    | dto.SourceSnapshotResponse
    | RetainedDocument
    | dto.IngestionJobResponse
)
CATALOG: dict[str, tuple[Any, Any]] = {
    "sources": (repositories.MongoResearchSourceRepository, dto.ResearchSourceResponse),
    "notes": (repositories.MongoResearchNoteRepository, dto.ResearchNoteResponse),
    "claims": (repositories.MongoResearchClaimRepository, dto.ResearchClaimResponse),
    "briefs": (repositories.MongoResearchBriefRepository, dto.ResearchBriefResponse),
    "candidates": (repositories.MongoKnowledgeCandidateRepository, dto.KnowledgeCandidateResponse),
    "proposals": (repositories.MongoSkillUpdateProposalRepository, dto.SkillUpdateProposalResponse),
    "events": (repositories.MongoPromotionEventRepository, dto.PromotionEventResponse),
    "snapshots": (repositories.MongoSourceSnapshotRepository, dto.SourceSnapshotResponse),
    "documents": (repositories.MongoResearchDocumentRepository, RetainedDocument),
    "jobs": (repositories.MongoIngestionJobRepository, dto.IngestionJobResponse),
}


def project_allowed(action: str, row: Any) -> None:
    policy = current_policy()
    if not policy or not resource_allows(
        policy, action, row.model_dump(by_alias=True), "research_projects"
    ):
        raise PolicyDenied("research project is outside effective access")


def skill_selector(action: str) -> tuple[str, ...] | None:
    policy = current_policy()
    if policy is None:
        raise PolicyDenied("verified human policy is required")
    a, b = policy.selector(action, "skill"), policy.selector("skills:read", "skill")
    return b if a is None else a if b is None else tuple(sorted(set(a) & set(b)))


def skill_allowed(action: str, skill_id: str) -> None:
    resource(action, "skill", skill_id)
    resource("skills:read", "skill", skill_id)


def project_document(document: Any, model: Any) -> Any:
    if model is RetainedDocument:
        values = document.model_dump()
        return RetainedDocument(**{k: values[k] for k in RetainedDocument.model_fields})
    result = model.from_document(document)
    if model is dto.IngestionJobResponse and result.error:
        result = result.model_copy(
            update={"error": "Ingestion failed within the configured source limits."}
        )
    return result


class ResearchConsole:
    def __init__(self, request: Request, context: BrowserContext) -> None:
        self.request, self.context = request, context
        self.db = request.app.state.mongodb.database

    async def project(self, identifier: str) -> Any:
        research_resource(identifier)
        row = await get_research_project_service(self.request).get_project(identifier)
        project_allowed(current_action() or "", row)
        return row

    async def source(self, research_id: str, source_id: str) -> Any:
        await self.project(research_id)
        source = await get_research_source_service(self.request).get_source(source_id)
        if source.research_project_id != research_id:
            raise PolicyDenied("source is unavailable in this project")
        return source

    async def check_job(self, job: Any) -> None:
        if job.initiating_authority is None:
            raise PolicyDenied("legacy job initiating authority is unavailable")
        try:
            await MongoJobAuthorityChecker(
                self.db, human_sessions=self.context.service.sessions
            ).check(job.initiating_authority)
        except JobAuthorityError:
            raise PolicyDenied("job initiating authority is unavailable") from None

    async def job(self, research_id: str, source_id: str, job_id: str) -> Any:
        await self.source(research_id, source_id)
        job = await get_research_ingestion_service(self.request).get_job(
            research_project_id=research_id, job_id=job_id
        )
        if job.source_id != source_id:
            raise PolicyDenied("job is unavailable in this source")
        await self.check_job(job)
        return job

    async def page(
        self,
        research_id: str,
        kind: Kind,
        *,
        limit: int,
        cursor: str | None,
        source_id: str | None,
        status: str | None,
    ) -> Any:
        await self.project(research_id)
        repo_type, model = CATALOG[kind]
        repo = repo_type(self.db, research_id)
        filters: dict[str, Any] = {}
        if kind in {"sources", "notes", "claims", "briefs", "candidates", "proposals", "events"}:
            filters["allowed_project_ids"] = projects()
        if status and kind not in {"snapshots", "events", "documents"}:
            filters["status"] = status
        if source_id and kind in {"snapshots", "documents", "jobs"}:
            await self.source(research_id, source_id)
            filters["source_id"] = source_id
        if kind == "proposals":
            filters["skill_ids"] = skill_selector("research:read")
        if kind == "jobs":
            # Only jobs started by this still-active browser session. The
            # initiating write capability is also rechecked; no post-page
            # hiding of another person's job IDs, counts or cursor population.
            policy = current_policy()
            if policy is None:
                raise PolicyDenied("verified human policy is required")
            if source_id:
                source = await self.source(research_id, source_id)
                if not resource_allows(
                    policy, "research:write", source.model_dump(), "research_sources"
                ):
                    raise PolicyDenied("initiating job capability is unavailable")
            filters.update(
                user_id=self.context.row["user_id"], session_id=self.context.row["public_id"]
            )
        page = await repo.list(limit=limit, cursor=cursor, **filters)
        if kind == "jobs":
            for job in page.items:
                await self.check_job(job)
        return dto.ResearchPageSchema(
            items=tuple(project_document(d, model) for d in page.items),
            next_cursor=page.next_cursor,
        )

    async def chunks(
        self,
        research_id: str,
        source_id: str,
        snapshot_id: str,
        document_id: str,
        limit: int,
        cursor: str | None,
    ) -> Any:
        await self.source(research_id, source_id)
        document = await repositories.MongoResearchDocumentRepository(self.db, research_id).get(
            source_id, snapshot_id, document_id
        )
        if document is None:
            raise PolicyDenied("document is unavailable")
        page = await repositories.MongoResearchChunkRepository(self.db, research_id).list(
            document_id=document_id, limit=limit, cursor=cursor
        )
        if any(c.source_id != source_id or c.snapshot_id != snapshot_id for c in page.items):
            raise PolicyDenied("document provenance is inconsistent")
        return dto.ResearchPageSchema(
            items=tuple(
                RetainedChunk(**{k: c.model_dump()[k] for k in RetainedChunk.model_fields})
                for c in page.items
            ),
            next_cursor=page.next_cursor,
        )


def browser_source_type(source_type: ResearchSourceType) -> None:
    if source_type not in {
        ResearchSourceType.MANUAL,
        ResearchSourceType.URL,
        ResearchSourceType.FILE,
        ResearchSourceType.MARKDOWN,
        ResearchSourceType.PDF,
    }:
        raise PolicyDenied("this source acquisition mode is unavailable in the console")
