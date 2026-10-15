"""Phase 6 advisory novelty and non-generative repurposing behavior."""

from datetime import UTC, datetime

import pytest

from app.db.content_indexes import ContentQueryFilters
from app.models.content import ContentBriefDocument, ContentIdeaDocument, ContentItemDocument
from app.models.skill import SkillDocument
from app.services.content_novelty_service import ContentNoveltyService
from app.services.content_repurpose_service import ContentRepurposeService
from app.services.content_retrieval_service import ContentSearchItem, ContentSearchResult

PROFILE = "personal-brand"
PROJECT = "payment-service"
NOW = datetime(2026, 9, 23, tzinfo=UTC)


def history_item(
    *,
    identifier: str,
    title: str,
    content: str,
    tags: tuple[str, ...] = (),
    pillar_id: str | None = None,
    project_ids: tuple[str, ...] = (),
    kind: str = "content",
) -> ContentSearchItem:
    return ContentSearchItem(
        kind=kind,  # type: ignore[arg-type]
        identifier=identifier,
        title=title,
        content=content,
        status="published",
        platforms=("medium",),
        tags=tags,
        pillar_id=pillar_id,
        project_ids=project_ids,
        source_refs=(),
        score=0.9,
        methods=("lexical",),
        tokens=20,
        updated_at=NOW,
    )


class Retrieval:
    def __init__(self, items: tuple[ContentSearchItem, ...]) -> None:
        self.items = items
        self.calls: list[dict[str, object]] = []

    async def search(
        self,
        *,
        profile_id: str,
        query: str,
        filters: ContentQueryFilters,
        retrieval_profile: str = "content_history",
        method: str = "hybrid",
        limit: int = 10,
        token_budget: int | None = None,
    ) -> ContentSearchResult:
        self.calls.append(
            {
                "profile_id": profile_id,
                "query": query,
                "filters": filters,
                "retrieval_profile": retrieval_profile,
                "method": method,
                "limit": limit,
                "token_budget": token_budget,
            }
        )
        return ContentSearchResult(
            profile_id=profile_id,
            retrieval_profile=retrieval_profile,  # type: ignore[arg-type]
            method=method,  # type: ignore[arg-type]
            items=self.items,
            token_budget=400,
            tokens_used=sum(item.tokens for item in self.items),
            diagnostics={},
        )


class Skills:
    def __init__(self, *items: SkillDocument) -> None:
        self.items = {item.skill_id: item for item in items}

    async def get_active(self, skill_id: str) -> SkillDocument | None:
        return self.items.get(skill_id)


def content_skill(skill_id: str) -> SkillDocument:
    return SkillDocument(
        skill_id=skill_id,
        name=skill_id.replace("-", " ").title(),
        version="1.0.0",
        description="Reviewed platform writing guidance.",
        category="content",
        trust_level="trusted",
        source_path=f"skills/content/{skill_id}.md",
        content_hash="a" * 64,
    )


@pytest.mark.asyncio
async def test_novelty_same_topic_same_angle_returns_explainable_history() -> None:
    retrieval = Retrieval(
        (
            history_item(
                identifier="outbox-medium",
                title="Reliable Outbox Delivery",
                content="Persist payment events before asynchronous delivery retries.",
                tags=("outbox", "payments"),
                pillar_id="payments",
                project_ids=(PROJECT,),
            ),
        )
    )
    result = await ContentNoveltyService(retrieval).check_content_novelty(
        profile_id=PROFILE,
        title="Reliable Outbox Delivery",
        description="Persist payment events before asynchronous delivery retries.",
        tags=("outbox", "payments"),
        pillar_id="payments",
        allowed_project_ids=frozenset({PROJECT}),
    )

    assert result.classification == "same-topic-same-angle"
    assert result.advisory is True
    assert {"outbox", "payments"} <= set(result.similar_content[0].shared_topic_terms)
    assert "persist" in result.similar_content[0].shared_angle_terms
    assert retrieval.calls[0]["retrieval_profile"] == "content_history"


@pytest.mark.asyncio
async def test_novelty_distinguishes_a_new_angle_and_omits_private_history() -> None:
    retrieval = Retrieval(
        (
            history_item(
                identifier="outbox-implementation",
                title="Outbox implementation details",
                content="Use a transactional table and a retry worker.",
                tags=("outbox",),
                project_ids=(PROJECT,),
            ),
            history_item(
                identifier="private-outbox",
                title="Outbox production incident",
                content="Private customer remediation details.",
                tags=("outbox",),
                project_ids=("other-project",),
            ),
        )
    )
    result = await ContentNoveltyService(retrieval).check_content_novelty(
        profile_id=PROFILE,
        title="Outbox operational monitoring",
        description="Alert when delivery lag grows and retries are exhausted.",
        tags=("outbox",),
        allowed_project_ids=frozenset({PROJECT}),
    )

    assert result.classification == "same-topic-new-angle"
    assert [item.identifier for item in result.similar_content] == ["outbox-implementation"]
    assert result.angle_overlap == 0.0


@pytest.mark.asyncio
async def test_novelty_unrelated_history_is_new_topic_not_an_objective_claim() -> None:
    result = await ContentNoveltyService(
        Retrieval(
            (
                history_item(
                    identifier="keycloak-post",
                    title="Keycloak token rotation",
                    content="Rotate signing keys safely.",
                    tags=("security",),
                ),
            )
        )
    ).check_content_novelty(
        profile_id=PROFILE,
        title="Payment idempotency keys",
        description="Reuse a request key for retry-safe card charges.",
        tags=("payments",),
    )

    assert result.classification == "new-topic"
    assert result.similar_content == ()
    assert (result.topic_overlap, result.angle_overlap) == (0.0, 0.0)


@pytest.mark.asyncio
async def test_repurpose_plan_is_platform_specific_and_preserves_medium_provenance() -> None:
    source = ContentItemDocument(
        profile_id=PROFILE,
        content_id="outbox-medium",
        platform="medium",
        content_type="article",
        title="The Outbox keeps payment events reliable",
        body=(
            "Persist payment events with the payment transaction. "
            "For example, retry delivery from the durable outbox table. "
            "This avoids losing an accepted payment event."
        ),
        status="published",
        project_ids=[PROJECT],
        source_refs=[
            {"source_type": "project_context", "source_id": "outbox-adr", "project_id": PROJECT}
        ],
    )
    retrieval = Retrieval(
        (
            history_item(
                identifier="outbox-medium",
                title=source.title,
                content=source.body,
                project_ids=(PROJECT,),
            ),
            history_item(
                identifier="outbox-linkedin-old",
                title="The first Outbox lesson",
                content="A prior reviewed outbox explanation.",
                project_ids=(PROJECT,),
            ),
        )
    )
    result = await ContentRepurposeService(
        retrieval, Skills(content_skill("linkedin-writing"), content_skill("github-writing"))
    ).prepare_repurpose_plan(
        source=source,
        targets=("linkedin", "github"),
        allowed_project_ids=frozenset({PROJECT}),
    )

    linkedin, github = result.targets
    assert result.source_refs[0].source_id == "outbox-adr"
    assert result.core_thesis == source.title
    assert source.body not in result.reusable_supporting_points
    assert linkedin.target_content_skill is not None
    assert linkedin.target_content_skill.skill_id == "linkedin-writing"
    assert github.target_content_skill is not None
    assert github.target_content_skill.skill_id == "github-writing"
    assert linkedin.target_platform_differences != github.target_platform_differences
    assert [item.identifier for item in linkedin.previous_related_content] == [
        "outbox-linkedin-old"
    ]
    assert retrieval.calls[0]["retrieval_profile"] == "content_repurposing"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("source", "expected_kind"),
    (
        (
            ContentIdeaDocument(
                profile_id=PROFILE,
                idea_id="idea-source",
                title="Outbox idea",
                description="Explain durable event delivery.",
                platforms=["linkedin"],
                origin={"type": "project_context", "project_id": PROJECT, "source_ids": ["adr"]},
                priority=0.5,
            ),
            "idea",
        ),
        (
            ContentBriefDocument(
                profile_id=PROFILE,
                brief_id="brief-source",
                idea_id="idea-source",
                platform="medium",
                objective="Explain reliable outbox delivery.",
                primary_message="The transaction must retain the event.",
                source_refs=[
                    {"source_type": "project_context", "source_id": "adr", "project_id": PROJECT}
                ],
            ),
            "brief",
        ),
    ),
)
async def test_repurpose_supports_idea_and_brief_sources(
    source: ContentIdeaDocument | ContentBriefDocument, expected_kind: str
) -> None:
    result = await ContentRepurposeService(
        Retrieval(()), Skills(content_skill("linkedin-writing"))
    ).prepare_repurpose_plan(source=source, targets=("linkedin",))

    assert result.source_kind == expected_kind
    assert result.source_refs[0].source_id == "adr"


@pytest.fixture(autouse=True)
def admitted_tenant_for_domain_regression(tenant_runtime):
    """Existing behavior runs inside one explicitly admitted organization."""
    yield
