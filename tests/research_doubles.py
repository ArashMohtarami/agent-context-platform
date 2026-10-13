"""In-memory V3 Phase 1 research-project persistence doubles."""

from collections.abc import Sequence

from app.models.common import Page, decode_page_cursor, encode_page_cursor
from app.models.research import ResearchProjectDocument, ResearchProjectStatus
from app.repositories.errors import ConcurrencyConflictError, DuplicateDocumentError


class FakeResearchProjectRepository:
    """Store research projects and enforce the same full-link visibility rule."""

    def __init__(self, documents: Sequence[ResearchProjectDocument] = ()) -> None:
        self.documents = {document.research_project_id: document for document in documents}

    async def create(self, document: ResearchProjectDocument) -> ResearchProjectDocument:
        if document.research_project_id in self.documents:
            raise DuplicateDocumentError("research project exists")
        self.documents[document.research_project_id] = document
        return document

    async def get(self, research_project_id: str) -> ResearchProjectDocument | None:
        return self.documents.get(research_project_id)

    async def list(
        self,
        *,
        status: ResearchProjectStatus | None = None,
        allowed_project_ids: tuple[str, ...] | None = None,
        limit: int = 20,
        cursor: str | None = None,
    ) -> Page[ResearchProjectDocument]:
        visible = [
            document
            for document in self.documents.values()
            if (status is None or document.status == status)
            and (
                allowed_project_ids is None
                or set(document.related_project_ids).issubset(allowed_project_ids)
            )
        ]
        visible.sort(key=lambda document: (document.created_at, document.id))
        if cursor is not None:
            created_at, document_id = decode_page_cursor(cursor)
            visible = [
                document
                for document in visible
                if (document.created_at, document.id) > (created_at, document_id)
            ]
        items = tuple(visible[:limit])
        next_cursor = (
            encode_page_cursor(items[-1].created_at, items[-1].id)
            if len(visible) > limit and items
            else None
        )
        return Page(items=items, next_cursor=next_cursor)

    async def update(
        self,
        document: ResearchProjectDocument,
        *,
        expected_revision: int,
    ) -> ResearchProjectDocument:
        stored = self.documents.get(document.research_project_id)
        if stored is None or stored.revision != expected_revision:
            raise ConcurrencyConflictError("research project changed after it was read")
        payload = document.model_dump(by_alias=True, mode="python")
        payload["revision"] = expected_revision + 1
        updated = ResearchProjectDocument.model_validate(payload)
        self.documents[updated.research_project_id] = updated
        return updated
