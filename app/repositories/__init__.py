"""Typed MongoDB repository implementations."""

from app.repositories.agent_repository import (
    MongoAgentActivityRepository,
    MongoAgentClientRepository,
    MongoAgentSessionRepository,
    MongoProjectBindingRepository,
)
from app.repositories.client_profile_repository import MongoClientProfileRepository
from app.repositories.content_repository import (
    MongoContentBriefRepository,
    MongoContentCampaignRepository,
    MongoContentIdeaRepository,
    MongoContentItemRepository,
    MongoContentLinkRepository,
    MongoContentMetricRepository,
    MongoContentPillarRepository,
    MongoContentVersionRepository,
    MongoCreatorProfileRepository,
)
from app.repositories.context_repository import MongoProjectContextRepository
from app.repositories.conversation_repository import MongoConversationRepository
from app.repositories.memory_repository import MongoMemoryRepository
from app.repositories.project_repository import MongoProjectRepository
from app.repositories.research_repository import (
    MongoIngestionJobRepository,
    MongoKnowledgeCandidateRepository,
    MongoPromotionEventRepository,
    MongoResearchBriefRepository,
    MongoResearchChunkRepository,
    MongoResearchCitationRepository,
    MongoResearchClaimRepository,
    MongoResearchDocumentRepository,
    MongoResearchNoteRepository,
    MongoResearchProjectRepository,
    MongoResearchSourceRepository,
    MongoSourceSnapshotRepository,
)
from app.repositories.retrieval_repository import MongoRetrievalLogRepository
from app.repositories.skill_chunk_repository import MongoSkillChunkRepository
from app.repositories.skill_repository import MongoSkillRepository

__all__ = [
    "MongoAgentActivityRepository",
    "MongoAgentClientRepository",
    "MongoAgentSessionRepository",
    "MongoClientProfileRepository",
    "MongoContentBriefRepository",
    "MongoContentCampaignRepository",
    "MongoContentIdeaRepository",
    "MongoContentItemRepository",
    "MongoContentLinkRepository",
    "MongoContentMetricRepository",
    "MongoContentPillarRepository",
    "MongoContentVersionRepository",
    "MongoConversationRepository",
    "MongoCreatorProfileRepository",
    "MongoIngestionJobRepository",
    "MongoKnowledgeCandidateRepository",
    "MongoMemoryRepository",
    "MongoProjectBindingRepository",
    "MongoProjectContextRepository",
    "MongoProjectRepository",
    "MongoPromotionEventRepository",
    "MongoResearchBriefRepository",
    "MongoResearchChunkRepository",
    "MongoResearchCitationRepository",
    "MongoResearchClaimRepository",
    "MongoResearchDocumentRepository",
    "MongoResearchNoteRepository",
    "MongoResearchProjectRepository",
    "MongoResearchSourceRepository",
    "MongoRetrievalLogRepository",
    "MongoSkillChunkRepository",
    "MongoSkillRepository",
    "MongoSourceSnapshotRepository",
]
