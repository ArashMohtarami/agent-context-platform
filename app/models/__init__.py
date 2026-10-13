"""Validated MongoDB persistence models."""

from app.models.agent import (
    AgentActivityDocument,
    AgentClientDocument,
    AgentSessionDocument,
    ProjectBindingDocument,
)
from app.models.client_profile import ClientProfileDocument
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
from app.models.conversation import ConversationDocument
from app.models.memory import MemoryDocument
from app.models.project import ProjectDocument
from app.models.project_context import ProjectContextDocument
from app.models.research import (
    IngestionJobDocument,
    KnowledgeCandidateDocument,
    PromotionEventDocument,
    ResearchBriefDocument,
    ResearchChunkDocument,
    ResearchCitationDocument,
    ResearchClaimDocument,
    ResearchDocumentDocument,
    ResearchNoteDocument,
    ResearchProjectDocument,
    ResearchSourceDocument,
    SourceSnapshotDocument,
)
from app.models.retrieval_log import RetrievalLogDocument
from app.models.skill import SkillDocument
from app.models.skill_chunk import SkillChunkDocument

__all__ = [
    "AgentActivityDocument",
    "AgentClientDocument",
    "AgentSessionDocument",
    "ClientProfileDocument",
    "ContentBriefDocument",
    "ContentCampaignDocument",
    "ContentIdeaDocument",
    "ContentItemDocument",
    "ContentLinkDocument",
    "ContentMetricDocument",
    "ContentPillarDocument",
    "ContentVersionDocument",
    "ConversationDocument",
    "CreatorProfileDocument",
    "IngestionJobDocument",
    "KnowledgeCandidateDocument",
    "MemoryDocument",
    "ProjectBindingDocument",
    "ProjectContextDocument",
    "ProjectDocument",
    "PromotionEventDocument",
    "ResearchBriefDocument",
    "ResearchChunkDocument",
    "ResearchCitationDocument",
    "ResearchClaimDocument",
    "ResearchDocumentDocument",
    "ResearchNoteDocument",
    "ResearchProjectDocument",
    "ResearchSourceDocument",
    "RetrievalLogDocument",
    "SkillChunkDocument",
    "SkillDocument",
    "SourceSnapshotDocument",
]
