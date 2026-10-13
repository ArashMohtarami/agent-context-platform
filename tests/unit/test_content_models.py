"""V2 Phase 0 persistence model validation and serialization tests."""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from app.models.content import (
    ContentBriefDocument,
    ContentCampaignDocument,
    ContentIdeaDocument,
    ContentItemDocument,
    ContentLinkDocument,
    ContentMetricDocument,
    ContentPillarDocument,
    ContentVersionDocument,
    CreatorProfileDocument,
)


def documents() -> tuple[object, ...]:
    """Build one valid record for every V2 collection."""
    return (
        CreatorProfileDocument(
            profile_id="personal-brand",
            name="Technical Personal Brand",
            audiences=["backend engineers"],
            domains=["distributed systems"],
            voice={"tone": ["direct"], "avoid": ["generic hype"]},
        ),
        ContentPillarDocument(
            profile_id="personal-brand",
            pillar_id="distributed-systems",
            name="Distributed Systems",
            description="Practical distributed-systems lessons.",
            priority=0.9,
        ),
        ContentIdeaDocument(
            profile_id="personal-brand",
            idea_id="payment-idempotency",
            title="Why payment APIs need idempotency",
            description="A project-grounded explanation of safe retries.",
            pillar_id="distributed-systems",
            platforms=["linkedin", "medium"],
            origin={
                "type": "project_context",
                "project_id": "payment-service",
                "source_ids": ["payment-idempotency-context"],
            },
            priority=0.8,
        ),
        ContentBriefDocument(
            profile_id="personal-brand",
            brief_id="payment-idempotency-linkedin",
            idea_id="payment-idempotency",
            platform="linkedin",
            objective="Explain safe payment retries through real implementation experience.",
            primary_message="Idempotency turns uncertain retries into a controlled API contract.",
            source_refs=[
                {
                    "source_type": "project_context",
                    "source_id": "payment-idempotency-context",
                    "project_id": "payment-service",
                }
            ],
        ),
        ContentItemDocument(
            profile_id="personal-brand",
            content_id="payment-idempotency-linkedin-post",
            brief_id="payment-idempotency-linkedin",
            platform="linkedin",
            content_type="post",
            title="Why payment APIs need idempotency",
            body="A current draft grounded in project context.",
            source_refs=[
                {
                    "source_type": "project_context",
                    "source_id": "payment-idempotency-context",
                    "project_id": "payment-service",
                }
            ],
            project_ids=["payment-service"],
        ),
        ContentVersionDocument(
            profile_id="personal-brand",
            content_id="payment-idempotency-linkedin-post",
            version=1,
            title="Why payment APIs need idempotency",
            body="The first preserved draft.",
            status="drafting",
        ),
        ContentLinkDocument(
            profile_id="personal-brand",
            link_id="idempotency-context-derived",
            content_id="payment-idempotency-linkedin-post",
            source_type="project_context",
            source_id="payment-idempotency-context",
            project_id="payment-service",
            relation="derived_from",
        ),
        ContentMetricDocument(
            profile_id="personal-brand",
            metric_id="payment-idempotency-linkedin-20260920",
            content_id="payment-idempotency-linkedin-post",
            platform="linkedin",
            captured_at=datetime(2026, 9, 20, tzinfo=UTC),
            metrics={"views": 100.0, "likes": 5.0},
        ),
        ContentCampaignDocument(
            profile_id="personal-brand",
            campaign_id="payment-reliability-series",
            name="Payment Reliability Series",
            description="A coordinated technical-content series about payment reliability.",
            content_ids=["payment-idempotency-linkedin-post"],
            platforms=["linkedin", "medium"],
        ),
    )


def test_v2_models_have_deterministic_identity_timestamps_and_round_trip() -> None:
    """Every V2 record can survive BSON-like serialization and revalidation."""
    for document in documents():
        document_type = type(document)
        encoded = document.model_dump(by_alias=True, mode="python")  # type: ignore[union-attr]
        decoded = document_type.model_validate(encoded)  # type: ignore[union-attr]
        assert decoded == document
        assert document.created_at.tzinfo is not None  # type: ignore[union-attr]
        assert document.updated_at.tzinfo is not None  # type: ignore[union-attr]
        assert document.revision == 1  # type: ignore[union-attr]


def test_v2_natural_keys_produce_stable_ids() -> None:
    """A repeatable creator/content key cannot make duplicate records."""
    first = ContentIdeaDocument(
        profile_id="personal-brand",
        idea_id="same-idea",
        title="Original title",
        description="Original description.",
        platforms=["linkedin"],
        origin={"type": "memory"},
        priority=0.5,
    )
    second = ContentIdeaDocument(
        profile_id="personal-brand",
        idea_id="same-idea",
        title="Changed title",
        description="Changed description.",
        platforms=["medium"],
        origin={"type": "memory"},
        priority=0.8,
    )

    assert first.id == second.id


@pytest.mark.parametrize(
    ("factory", "match"),
    [
        (
            lambda: ContentIdeaDocument(
                profile_id="personal-brand",
                idea_id="invalid-status",
                title="Invalid lifecycle",
                description="This must fail model validation.",
                platforms=["linkedin"],
                origin={"type": "memory"},
                priority=0.5,
                status="draft",
            ),
            "status",
        ),
        (
            lambda: ContentItemDocument(
                profile_id="personal-brand",
                content_id="invalid-platform",
                platform="mastodon",
                content_type="post",
                title="Invalid platform",
                body="This must fail model validation.",
            ),
            "platform",
        ),
        (
            lambda: ContentLinkDocument(
                profile_id="personal-brand",
                link_id="invalid-relation",
                content_id="item",
                source_type="memory",
                source_id="memory-1",
                relation="copied_from",
            ),
            "relation",
        ),
        (
            lambda: CreatorProfileDocument(
                profile_id="personal-brand",
                name="Duplicate audiences",
                audiences=["backend engineers", "backend engineers"],
            ),
            "profile values",
        ),
    ],
)
def test_v2_models_reject_invalid_lifecycle_platform_relation_and_preferences(
    factory: object, match: str
) -> None:
    with pytest.raises(ValidationError, match=match):
        factory()  # type: ignore[operator]
