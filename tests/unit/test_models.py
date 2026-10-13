"""Persistence-model validation and deterministic identity tests."""

from collections.abc import Callable
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from app.models import (
    ConversationDocument,
    MemoryDocument,
    ProjectContextDocument,
    ProjectDocument,
    RetrievalLogDocument,
    SkillChunkDocument,
    SkillDocument,
)


def test_natural_keys_produce_deterministic_ids() -> None:
    skill_values = {
        "skill_id": "fastapi-production",
        "name": "FastAPI Production",
        "version": "1.0.0",
        "description": "Production practices",
        "category": "backend",
        "trust_level": "trusted",
        "source_path": "skills/backend/fastapi.md",
        "content_hash": "a" * 64,
    }
    project_values = {
        "project_id": "payments",
        "name": "Payments",
        "description": "Payment service",
        "repository": "ssh://git/payments.git",
        "environment": "production",
    }

    assert SkillDocument(**skill_values).id == SkillDocument(**skill_values).id
    assert ProjectDocument(**project_values).id == ProjectDocument(**project_values).id
    first_context = ProjectContextDocument(
        project_id="payments",
        type="architecture",
        title="Boundaries",
        content="Service boundaries",
        importance=0.8,
        source="user",
        external_id="architecture-boundaries",
    )
    second_context = ProjectContextDocument(
        project_id="payments",
        type="architecture",
        title="A changed title",
        content="Changed content",
        importance=0.9,
        source="user",
        external_id="architecture-boundaries",
    )
    assert first_context.id == second_context.id


def test_all_collection_models_have_aware_timestamps_and_revision() -> None:
    documents = [
        SkillDocument(
            skill_id="python",
            name="Python",
            version="1.0.0",
            description="Python rules",
            category="backend",
            trust_level="trusted",
            source_path="skills/python.md",
            content_hash="b" * 64,
        ),
        SkillChunkDocument(
            skill_id="python",
            skill_version="1.0.0",
            chunk_id="intro-0",
            content="Typed Python",
            section="Introduction",
            chunk_index=0,
            tokens=2,
        ),
        ProjectDocument(
            project_id="project-a",
            name="Project A",
            description="A project",
            repository="ssh://git/project-a.git",
            environment="development",
        ),
        ProjectContextDocument(
            project_id="project-a",
            type="constraint",
            title="Isolation",
            content="Never cross projects",
            importance=1.0,
            source="user",
        ),
        MemoryDocument(
            scope="global",
            type="preference",
            content="Use strict typing",
            importance=0.8,
            confidence=1.0,
            source="user",
        ),
        ConversationDocument(
            project_id="project-a",
            session_id="session-1",
            role="user",
            content="Remember this conversation separately",
        ),
        RetrievalLogDocument(
            query="typed repositories",
            project_id="project-a",
            requested_sources=["project_context"],
            result_count=1,
            selected_result_ids=["result-1"],
            latency_ms=5.5,
            retrieval_method="lexical",
        ),
    ]

    for document in documents:
        assert document.created_at.tzinfo is not None
        assert document.updated_at.tzinfo is not None
        assert document.revision == 1
        # Only the retrievable documents carry vectors in this phase.
        carries_vector_fields = "embedding" in document.model_dump()
        assert carries_vector_fields is isinstance(
            document, SkillChunkDocument | ProjectContextDocument | MemoryDocument
        )


@pytest.mark.parametrize(
    "factory",
    [
        lambda: SkillDocument(
            skill_id="python",
            name="Python",
            version="not-semver",
            description="Rules",
            category="backend",
            trust_level="trusted",
            source_path="skills/python.md",
            content_hash="b" * 64,
        ),
        lambda: ProjectContextDocument(
            project_id="project-a",
            type="unsupported",
            title="Invalid",
            content="Invalid type",
            importance=0.5,
            source="user",
        ),
        lambda: MemoryDocument(
            scope="global",
            project_id="project-a",
            type="preference",
            content="Invalid scope",
            importance=0.5,
            confidence=0.5,
            source="user",
        ),
        lambda: ProjectDocument(
            project_id="project-a",
            name="Project",
            description="Description",
            repository="repo",
            environment="development",
            tags=["duplicate", "duplicate"],
        ),
        lambda: RetrievalLogDocument(
            query="query",
            requested_sources=["skills"],
            result_count=0,
            selected_result_ids=["unexpected"],
            latency_ms=1.0,
            retrieval_method="lexical",
        ),
        lambda: ConversationDocument(
            project_id="project-a",
            session_id="session",
            role="user",
            content="content",
            unexpected=True,
        ),
    ],
)
def test_invalid_documents_are_rejected(factory: Callable[[], object]) -> None:
    with pytest.raises(ValidationError):
        factory()


def test_timestamp_order_is_validated() -> None:
    created_at = datetime.now(UTC)
    with pytest.raises(ValidationError, match="updated_at"):
        ConversationDocument(
            project_id="project-a",
            session_id="session",
            role="user",
            content="content",
            created_at=created_at,
            updated_at=created_at - timedelta(seconds=1),
        )
