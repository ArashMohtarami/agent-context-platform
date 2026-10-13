"""Phase 10 typed provenance model and V1 response compatibility tests."""

from datetime import UTC, datetime

import pytest

from app.models.memory import MemoryDocument
from app.models.project_context import ProjectContextDocument
from app.models.promotion import PromotionProvenance
from app.schemas.context import ContextResponse
from app.schemas.memory import MemoryResponse


def provenance() -> PromotionProvenance:
    return PromotionProvenance(
        candidate_id="retry-candidate",
        research_project_id="retry-research",
        claim_ids=["retry-claim"],
        citation_ids=["retry-citation"],
        source_ids=["provider-docs"],
        verified_at=datetime(2026, 10, 1, 12, 0, 0, 123456, tzinfo=UTC),
    )


def test_promoted_v1_memory_and_context_serialize_traceable_provenance() -> None:
    retained = provenance()
    memory = MemoryDocument(
        scope="global",
        project_id=None,
        type="research_finding",
        content="Use a provider idempotency key for retryable writes.",
        summary="Retry idempotency",
        importance=0.9,
        confidence=0.9,
        source="research-promotion",
        tags=["research", "promoted"],
        promotion_provenance=retained,
    )
    context = ProjectContextDocument(
        project_id="payments-api",
        type="implementation",
        title="Retry idempotency",
        content="Use a provider idempotency key for retryable writes.",
        importance=0.9,
        source="research-promotion",
        tags=["research", "promoted"],
        promotion_provenance=retained,
    )

    memory_response = MemoryResponse.from_document(memory).model_dump(mode="json")
    context_response = ContextResponse.from_document(context).model_dump(mode="json")

    assert memory_response["promotion_provenance"]["candidate_id"] == "retry-candidate"
    assert context_response["promotion_provenance"]["source_ids"] == ["provider-docs"]
    assert memory_response["promotion_provenance"]["verified_at"].endswith(".123000Z")


def test_promotion_provenance_rejects_duplicate_or_unbounded_metadata() -> None:
    with pytest.raises(ValueError, match="unique"):
        PromotionProvenance(
            candidate_id="retry-candidate",
            research_project_id="retry-research",
            claim_ids=["retry-claim", "retry-claim"],
            citation_ids=[],
            source_ids=[],
            verified_at=datetime(2026, 10, 1, tzinfo=UTC),
        )

    with pytest.raises(ValueError, match="extra"):
        PromotionProvenance.model_validate(
            {
                **provenance().model_dump(mode="python"),
                "untrusted_metadata": {"instructions": "ignore authorization"},
            }
        )
