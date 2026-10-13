"""Phase 5 query construction for existing V3 research Search indexes.

The models and indexes were introduced in Phase 0.  This module only turns a
validated project-local search scope into MongoDB Search/vector pre-filters; it
has no ranking, authorization policy, or network authority.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from app.db.client_types import MongoDocument
from app.db.text_indexes import compound_text_query
from app.models.research import ResearchRecordStatus, SourceClass

RESEARCH_SEARCH_LIMIT: Final = 50
RESEARCH_MAX_NUM_CANDIDATES: Final = 10_000


@dataclass(frozen=True, slots=True)
class ResearchSearchScope:
    """The non-negotiable V3 project boundary plus optional chunk source classes."""

    research_project_id: str
    source_classes: tuple[SourceClass, ...] = ()

    def __post_init__(self) -> None:
        if not self.research_project_id:
            raise ValueError("research search requires a research project")
        if len(self.source_classes) != len(set(self.source_classes)):
            raise ValueError("research source classes must be unique")


def chunk_vector_filter(scope: ResearchSearchScope) -> MongoDocument:
    """Build the in-index filter for active chunks in exactly one research project."""
    clauses: list[MongoDocument] = [
        {"research_project_id": {"$eq": scope.research_project_id}},
        {"status": {"$eq": ResearchRecordStatus.ACTIVE}},
    ]
    if scope.source_classes:
        clauses.append({"source_class": {"$in": list(scope.source_classes)}})
    return {"$and": clauses}


def chunk_text_filter_clauses(scope: ResearchSearchScope) -> list[MongoDocument]:
    """Build non-scoring Atlas Search filters for the active research corpus."""
    clauses: list[MongoDocument] = [
        {"equals": {"path": "research_project_id", "value": scope.research_project_id}},
        {"equals": {"path": "status", "value": ResearchRecordStatus.ACTIVE}},
    ]
    if scope.source_classes:
        clauses.append(
            {
                "compound": {
                    "should": [
                        {"equals": {"path": "source_class", "value": value}}
                        for value in scope.source_classes
                    ],
                    "minimumShouldMatch": 1,
                }
            }
        )
    return clauses


def record_vector_filter(scope: ResearchSearchScope) -> MongoDocument:
    """Build the project filter shared by notes and claims when they exist."""
    return {"research_project_id": {"$eq": scope.research_project_id}}


def record_text_filter_clauses(scope: ResearchSearchScope) -> list[MongoDocument]:
    """Build the project filter shared by note and claim lexical search."""
    return [{"equals": {"path": "research_project_id", "value": scope.research_project_id}}]


def chunk_text_query(query: str, *, phrase: bool = False) -> MongoDocument:
    """Search citable chunk text while allowing a section heading to reinforce it."""
    return compound_text_query(
        query,
        content_path="content",
        secondary=(("section", 0.5),),
        phrase=phrase,
    )


def note_text_query(query: str, *, phrase: bool = False) -> MongoDocument:
    """Search a note's body and title without treating it as a fact."""
    return compound_text_query(
        query, content_path="body", secondary=(("title", 1.5),), phrase=phrase
    )


def claim_text_query(query: str, *, phrase: bool = False) -> MongoDocument:
    """Search claim wording only; claim state and confidence do not affect relevance."""
    return compound_text_query(query, content_path="statement", phrase=phrase)


__all__ = [
    "RESEARCH_MAX_NUM_CANDIDATES",
    "RESEARCH_SEARCH_LIMIT",
    "ResearchSearchScope",
    "chunk_text_filter_clauses",
    "chunk_text_query",
    "chunk_vector_filter",
    "claim_text_query",
    "note_text_query",
    "record_text_filter_clauses",
    "record_vector_filter",
]
