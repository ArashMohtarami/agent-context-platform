"""Browser publication is explicitly manual and revision-bound."""

from typing import Literal

from pydantic import Field

from app.identity.models import Input
from app.models.common import ContentText, Identifier
from app.models.content import ContentPlatform, ContentSourceReference
from app.schemas.content import ContentItemCreateRequest, ContentVersionCreateRequest


class Draft(ContentItemCreateRequest):
    # Trusted actor provenance is installed by CurrentBrowser, not supplied by a caller.
    created_by: None = None


class Version(ContentVersionCreateRequest):
    created_by: None = None


class Receipt(Input):
    profile_id: Identifier
    expected_revision: int = Field(ge=1)
    provider: Literal["manual"] = "manual"
    confirmed_manual_publication: Literal[True]
    idempotency_key: str | None = Field(default=None, min_length=1, max_length=128)


class PageResponse[T](Input):
    items: tuple[T, ...]
    next_cursor: str | None


class Export(Input):
    profile_id: Identifier
    expected_revision: int = Field(ge=1)


class CitationKey(Input):
    claim_id: Identifier
    citation_id: Identifier


class Brief(Input):
    profile_id: Identifier
    brief_id: Identifier
    idea_id: Identifier
    platform: ContentPlatform
    objective: ContentText
    primary_message: ContentText
    audience: list[str] = Field(default_factory=list, max_length=50)
    supporting_points: list[ContentText] = Field(default_factory=list, max_length=50)
    source_refs: list[ContentSourceReference] = Field(default_factory=list, max_length=100)
    skills: list[Identifier] = Field(default_factory=list, max_length=50)
    research_project_id: Identifier | None = None
    claim_ids: list[Identifier] = Field(default_factory=list, max_length=100)
    citations: list[CitationKey] = Field(default_factory=list, max_length=100)
