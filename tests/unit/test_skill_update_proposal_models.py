"""V3 Phase 11 generated skill update proposal model and index tests."""

import pytest
from pydantic import ValidationError

from app.db.research_indexes import RESEARCH_STANDARD_INDEX_DEFINITIONS
from app.db.schema import COLLECTION_VALIDATORS
from app.models.common import deterministic_object_id
from app.models.research import (
    ResearchAuthorization,
    SkillUpdateProposalDocument,
    SkillUpdateProposalStatus,
)


@pytest.fixture
def proposal() -> SkillUpdateProposalDocument:
    return SkillUpdateProposalDocument(
        research_project_id="retry-evidence",
        proposal_id="idempotency-guidance",
        skill_id="backend-guidance",
        current_version="1.2.3",
        proposed_change="Clarify idempotency-key guidance from retained evidence.",
        proposed_patch="--- a/skills/backend-guidance.md\n+++ b/skills/backend-guidance.md",
        source_claim_ids=["retry-key"],
        source_citation_ids=["provider-support"],
        reason="The provider documentation changed its retry requirement.",
        authorization=ResearchAuthorization(project_ids=["payments-api"], owner_key_id="writer"),
    )


def test_skill_update_proposal_has_stable_identity_and_generated_trust(
    proposal: SkillUpdateProposalDocument,
) -> None:
    assert proposal.id == deterministic_object_id(
        "skill_update_proposal", "retry-evidence", "idempotency-guidance"
    )
    assert proposal.status is SkillUpdateProposalStatus.PENDING_REVIEW
    assert proposal.trust_level == "generated"
    assert proposal.model_dump(mode="json")["trust_level"] == "generated"


def test_skill_update_proposal_refuses_non_generated_trust_or_invalid_base_version(
    proposal: SkillUpdateProposalDocument,
) -> None:
    payload = proposal.model_dump(by_alias=True, mode="python")
    payload["trust_level"] = "trusted"
    with pytest.raises(ValidationError, match="generated"):
        SkillUpdateProposalDocument.model_validate(payload)

    payload = proposal.model_dump(by_alias=True, mode="python")
    payload["current_version"] = "release-candidate"
    with pytest.raises(ValidationError):
        SkillUpdateProposalDocument.model_validate(payload)


def test_skill_update_proposal_schema_and_indexes_preserve_review_boundary() -> None:
    schema = COLLECTION_VALIDATORS["skill_update_proposals"]["$jsonSchema"]
    properties = schema["properties"]
    required = set(schema["required"])

    assert {
        "research_project_id",
        "proposal_id",
        "skill_id",
        "current_version",
        "proposed_change",
        "proposed_patch",
        "source_claim_ids",
        "source_citation_ids",
        "reason",
        "status",
        "trust_level",
        "authorization",
    }.issubset(required)
    assert properties["trust_level"] == {"enum": ["generated"]}
    indexes = {
        index.document["name"]
        for index in RESEARCH_STANDARD_INDEX_DEFINITIONS["skill_update_proposals"]
    }
    assert indexes == {
        "skill_update_proposal_project_id_uq",
        "skill_update_proposal_project_status_updated",
        "skill_update_proposal_skill_version_updated",
        "skill_update_proposal_auth_created_page",
    }
