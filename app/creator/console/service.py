"""Existing creator services with session policy, never a borrowed Principal."""

from typing import Any, Literal

from fastapi import Request

from app.api import dependencies as services
from app.creator.console.boundary import profile, project_selector
from app.identity.browser import BrowserContext
from app.knowledge.boundary import resource
from app.repositories import content_repository as repositories
from app.schemas import content as dto
from app.security.tenant_policy import current_action

Kind = Literal["ideas", "briefs", "items"]
CATALOG: dict[str, tuple[Any, Any]] = {
    "ideas": (repositories.MongoContentIdeaRepository, dto.ContentIdeaResponse),
    "briefs": (repositories.MongoContentBriefRepository, dto.ContentBriefResponse),
    "items": (repositories.MongoContentItemRepository, dto.ContentItemResponse),
}
Record = dto.ContentIdeaResponse | dto.ContentBriefResponse | dto.ContentItemResponse


class CreatorConsole:
    def __init__(self, request: Request, context: BrowserContext) -> None:
        self.request, self.context = request, context
        self.content = services.get_content_service(request)
        self.creator = services.get_creator_service(request)
        self.db = request.app.state.mongodb.database

    async def item(self, profile_id: str, content_id: str) -> Any:
        profile(profile_id)
        return await self.content.get_content(profile_id, content_id)

    async def page(self, profile_id: str, kind: Kind, limit: int, cursor: str | None) -> Any:
        profile(profile_id)
        await self.creator.get_profile(profile_id)
        repo, model = CATALOG[kind]
        page = await repo(self.db, profile_id).list(limit=limit, cursor=cursor)
        return {
            "items": tuple(model.from_document(d) for d in page.items),
            "next_cursor": page.next_cursor,
        }

    def projects(self, identifiers: Any) -> None:
        for identifier in identifiers:
            resource(current_action() or "", "project", identifier)

    def source_refs(self, refs: Any) -> None:
        self.projects(r.project_id for r in refs if r.project_id is not None)

    async def research(self, identifier: str) -> None:
        resource(current_action() or "", "research_project", identifier)
        row = await services.get_research_project_service(self.request).get_project(identifier)
        self.projects(row.related_project_ids)
        if not row.related_project_ids:
            resource(current_action() or "", "organization_knowledge", self.context.row["org_id"])

    @property
    def allowed_projects(self) -> frozenset[str] | None:
        ids = project_selector()
        return None if ids is None else frozenset(ids)
