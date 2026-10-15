"""Request-scoped services shared by the REST routers.

Every retrieval endpoint builds the same service here rather than assembling its
own, so a source wired in for one route cannot be missing from another.
"""

from dataclasses import replace

from fastapi import Request

from app.api.gateway_context import retrieval_metadata
from app.api.security import CurrentPrincipal
from app.content.platforms import platform_services
from app.content.publication import PublicationSafetyPolicy
from app.content.publishers import default_publisher_registry
from app.core.config import Settings
from app.repositories.agent_repository import MongoProjectBindingRepository
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
    MongoResearchSourceCatalogRepository,
    MongoResearchSourceRepository,
    MongoSkillUpdateProposalRepository,
    MongoSourceSnapshotRepository,
)
from app.repositories.retrieval_repository import MongoRetrievalLogRepository
from app.repositories.skill_chunk_repository import MongoSkillChunkRepository
from app.repositories.skill_repository import MongoSkillRepository
from app.search.logging import (
    CompositeRetrievalLogger,
    RetrievalLogger,
    StructuredRetrievalLogger,
)
from app.security.job_authority import MongoJobAuthorityChecker
from app.services.agent_session_service import (
    AgentSessionAccessError,
    AgentSessionNotFoundError,
    AgentSessionService,
    AgentSessionStateError,
)
from app.services.content_discovery_service import ContentDiscoveryService
from app.services.content_export_service import ContentExportService
from app.services.content_metrics_service import ContentMetricsService
from app.services.content_novelty_service import ContentNoveltyService
from app.services.content_publication_service import ContentPublicationService
from app.services.content_publishing_service import ContentPublishingService
from app.services.content_repurpose_service import ContentRepurposeService
from app.services.content_research_service import ContentResearchIntegrationService
from app.services.content_retrieval_service import (
    ContentRetrievalService,
    default_content_retrieval_profiles,
)
from app.services.content_service import ContentService
from app.services.context_service import ContextService
from app.services.creator_service import CreatorService
from app.services.embedding_service import EmbeddingService
from app.services.knowledge_candidate_service import KnowledgeCandidateService
from app.services.memory_service import MemoryService
from app.services.project_binding_service import ProjectBindingService
from app.services.project_service import ProjectService
from app.services.research_brief_service import ResearchBriefService
from app.services.research_evidence_service import ResearchEvidenceService
from app.services.research_ingestion_service import (
    ResearchIngestionLimiter,
    ResearchIngestionService,
)
from app.services.research_notes_claims_service import ResearchNotesClaimsService
from app.services.research_project_service import ResearchProjectService
from app.services.research_search_service import (
    ResearchSearchService,
    default_research_retrieval_profiles,
)
from app.services.research_source_service import ResearchSourceService
from app.services.retrieval_log_service import PersistentRetrievalLogger
from app.services.retrieval_service import RetrievalService
from app.services.revalidation_service import RevalidationService
from app.services.search_service import SearchService
from app.services.skill_service import SkillService
from app.services.skill_update_proposal_service import SkillUpdateProposalService


def get_skill_service(request: Request) -> SkillService:
    """Build the request-scoped skill service over the MongoDB handle."""
    database = request.app.state.mongodb.database
    return SkillService(
        MongoSkillRepository(database),
        MongoSkillChunkRepository(database),
    )


def get_project_service(request: Request) -> ProjectService:
    """Build the request-scoped project service over the MongoDB handle."""
    return ProjectService(MongoProjectRepository(request.app.state.mongodb.database))


def get_project_binding_service(request: Request) -> ProjectBindingService:
    """Build V4 deterministic binding resolution over V1 project boundaries."""
    database = request.app.state.mongodb.database
    return ProjectBindingService(
        MongoProjectRepository(database),
        MongoProjectBindingRepository(database),
    )


def get_research_project_service(request: Request) -> ResearchProjectService:
    """Build V3 Phase 1 project metadata over V3 and V1 repositories."""
    database = request.app.state.mongodb.database
    return ResearchProjectService(
        MongoResearchProjectRepository(database),
        MongoProjectRepository(database),
    )


def get_research_source_service(request: Request) -> ResearchSourceService:
    """Build V3 Phase 2 source registration over the existing MongoDB collections."""
    database = request.app.state.mongodb.database
    return ResearchSourceService(
        MongoResearchProjectRepository(database),
        MongoResearchSourceCatalogRepository(database),
        lambda research_project_id: MongoResearchSourceRepository(database, research_project_id),
        lambda research_project_id: MongoSourceSnapshotRepository(database, research_project_id),
        max_sources_per_project=request.app.state.settings.research_max_sources_per_project,
    )


def get_research_ingestion_service(request: Request) -> ResearchIngestionService:
    """Build V3 Phase 3 constrained ingestion over source-scoped MongoDB repositories."""
    database = request.app.state.mongodb.database
    limiter = getattr(request.app.state, "research_ingestion_limiter", None)
    if not isinstance(limiter, ResearchIngestionLimiter):
        limiter = ResearchIngestionLimiter(
            request.app.state.settings.research_max_ingestion_concurrency
        )
        request.app.state.research_ingestion_limiter = limiter
    return ResearchIngestionService(
        lambda research_project_id: MongoSourceSnapshotRepository(database, research_project_id),
        lambda research_project_id: MongoResearchDocumentRepository(database, research_project_id),
        lambda research_project_id: MongoResearchChunkRepository(database, research_project_id),
        lambda research_project_id: MongoIngestionJobRepository(database, research_project_id),
        request.app.state.embeddings,
        limiter=limiter,
        authority_checker=MongoJobAuthorityChecker(database),
        max_chunks_per_ingestion=request.app.state.settings.research_max_chunks_per_ingestion,
    )


def get_research_notes_claims_service(request: Request) -> ResearchNotesClaimsService:
    """Build V3 Phase 6 note and claim storage over the existing research collections."""
    database = request.app.state.mongodb.database
    return ResearchNotesClaimsService(
        MongoResearchProjectRepository(database),
        MongoResearchSourceCatalogRepository(database),
        lambda research_project_id: MongoSourceSnapshotRepository(database, research_project_id),
        lambda research_project_id: MongoResearchNoteRepository(database, research_project_id),
        lambda research_project_id: MongoResearchClaimRepository(database, research_project_id),
        lambda research_project_id: MongoResearchCitationRepository(database, research_project_id),
    )


def get_knowledge_candidate_service(request: Request) -> KnowledgeCandidateService:
    """Build V3 Phase 9 review storage without any V1 promotion adapter."""
    database = request.app.state.mongodb.database
    return KnowledgeCandidateService(
        MongoResearchProjectRepository(database),
        MongoProjectRepository(database),
        lambda research_project_id: MongoResearchClaimRepository(database, research_project_id),
        lambda research_project_id: MongoResearchCitationRepository(database, research_project_id),
        lambda research_project_id: MongoKnowledgeCandidateRepository(
            database, research_project_id
        ),
        lambda research_project_id: MongoPromotionEventRepository(database, research_project_id),
        get_memory_service(request),
        get_context_service(request),
    )


def get_revalidation_service(request: Request) -> RevalidationService:
    """Build Phase 14 advisory staleness checks from retained V1/V3 provenance."""
    database = request.app.state.mongodb.database
    return RevalidationService(
        MongoResearchProjectRepository(database),
        lambda research_project_id: MongoKnowledgeCandidateRepository(
            database, research_project_id
        ),
        lambda research_project_id: MongoPromotionEventRepository(database, research_project_id),
        lambda research_project_id: MongoResearchCitationRepository(database, research_project_id),
        lambda research_project_id: MongoSourceSnapshotRepository(database, research_project_id),
        lambda project_id: MongoMemoryRepository(database, project_id=project_id),
        lambda project_id: MongoProjectContextRepository(database, project_id),
        get_knowledge_candidate_service(request),
    )


def get_skill_update_proposal_service(request: Request) -> SkillUpdateProposalService:
    """Build Phase 11's read-only-V1-skill proposal workflow over MongoDB."""
    database = request.app.state.mongodb.database
    return SkillUpdateProposalService(
        MongoResearchProjectRepository(database),
        MongoSkillRepository(database),
        lambda research_project_id: MongoResearchClaimRepository(database, research_project_id),
        lambda research_project_id: MongoResearchCitationRepository(database, research_project_id),
        lambda research_project_id: MongoSkillUpdateProposalRepository(
            database, research_project_id
        ),
    )


def get_research_brief_service(request: Request) -> ResearchBriefService:
    """Build V3 Phase 8 structured briefs over existing V3 repositories only."""
    database = request.app.state.mongodb.database
    return ResearchBriefService(
        MongoResearchProjectRepository(database),
        lambda research_project_id: MongoResearchClaimRepository(database, research_project_id),
        lambda research_project_id: MongoResearchCitationRepository(database, research_project_id),
        lambda research_project_id: MongoResearchSourceRepository(database, research_project_id),
        lambda research_project_id: MongoSourceSnapshotRepository(database, research_project_id),
        lambda research_project_id: MongoResearchBriefRepository(database, research_project_id),
    )


def get_research_evidence_service(request: Request) -> ResearchEvidenceService:
    """Build V3 Phase 7 citations and packets over existing V3/V1 retrieval services."""
    database = request.app.state.mongodb.database
    settings: Settings = request.app.state.settings
    return ResearchEvidenceService(
        MongoResearchProjectRepository(database),
        MongoResearchSourceCatalogRepository(database),
        lambda research_project_id: MongoSourceSnapshotRepository(database, research_project_id),
        lambda research_project_id: MongoResearchChunkRepository(database, research_project_id),
        lambda research_project_id: MongoResearchClaimRepository(database, research_project_id),
        lambda research_project_id: MongoResearchCitationRepository(database, research_project_id),
        get_research_search_service(request),
        token_budget=settings.research_retrieval_token_budget,
        max_token_budget=settings.research_retrieval_max_token_budget,
    )


def get_research_search_service(request: Request) -> ResearchSearchService:
    """Build V3 Phase 5 search from project-scoped repositories and existing V1 retrieval."""
    database = request.app.state.mongodb.database
    settings: Settings = request.app.state.settings
    return ResearchSearchService(
        lambda research_project_id: MongoResearchChunkRepository(database, research_project_id),
        lambda research_project_id: MongoResearchNoteRepository(database, research_project_id),
        lambda research_project_id: MongoResearchClaimRepository(database, research_project_id),
        lambda research_project_id: MongoResearchSourceRepository(database, research_project_id),
        lambda research_project_id: MongoSourceSnapshotRepository(database, research_project_id),
        get_retrieval_service(request),
        request.app.state.embeddings,
        profiles=default_research_retrieval_profiles(
            token_budget=settings.research_retrieval_token_budget
        ),
        candidate_multiplier=settings.vector_search_candidate_multiplier,
        max_candidates=settings.vector_search_max_candidates,
        weights=settings.ranking_weights(),
        duplicate_overlap=settings.retrieval_duplicate_overlap,
        token_budget=settings.research_retrieval_token_budget,
        max_token_budget=settings.research_retrieval_max_token_budget,
    )


def get_creator_service(request: Request) -> CreatorService:
    """Build the V2 Phase 1 creator service over profile-isolated repositories."""
    database = request.app.state.mongodb.database
    return CreatorService(
        MongoCreatorProfileRepository(database),
        lambda profile_id: MongoContentPillarRepository(database, profile_id),
    )


def get_content_research_service(request: Request) -> ContentResearchIntegrationService:
    """Build Phase 13's read-only V2/V3 evidence bridge over existing stores."""
    database = request.app.state.mongodb.database
    return ContentResearchIntegrationService(
        MongoResearchProjectRepository(database),
        lambda research_project_id: MongoResearchClaimRepository(database, research_project_id),
        lambda research_project_id: MongoResearchCitationRepository(database, research_project_id),
        get_research_evidence_service(request),
    )


def get_content_service(request: Request) -> ContentService:
    """Build the V2 Phase 3 storage service over profile-isolated MongoDB repositories."""
    database = request.app.state.mongodb.database
    return ContentService(
        MongoCreatorProfileRepository(database),
        lambda profile_id: MongoContentIdeaRepository(database, profile_id),
        lambda profile_id: MongoContentBriefRepository(database, profile_id),
        lambda profile_id: MongoContentItemRepository(database, profile_id),
        lambda profile_id: MongoContentVersionRepository(database, profile_id),
        lambda profile_id: MongoContentLinkRepository(database, profile_id),
        lambda profile_id: MongoContentCampaignRepository(database, profile_id),
        request.app.state.embeddings,
        get_content_research_service(request),
    )


def get_content_metrics_service(request: Request) -> ContentMetricsService:
    """Build Phase 10 generic manual metrics over profile-isolated MongoDB data."""
    database = request.app.state.mongodb.database
    return ContentMetricsService(
        MongoCreatorProfileRepository(database),
        lambda profile_id: MongoContentItemRepository(database, profile_id),
        lambda profile_id: MongoContentMetricRepository(database, profile_id),
    )


def get_content_retrieval_service(request: Request) -> ContentRetrievalService:
    """Build V2 retrieval from profile-bound repositories and local embeddings."""
    database = request.app.state.mongodb.database
    settings: Settings = request.app.state.settings
    return ContentRetrievalService(
        lambda profile_id: MongoContentIdeaRepository(database, profile_id),
        lambda profile_id: MongoContentBriefRepository(database, profile_id),
        lambda profile_id: MongoContentItemRepository(database, profile_id),
        request.app.state.embeddings,
        profiles=default_content_retrieval_profiles(
            creation_priorities=settings.content_creation_priorities(),
            token_budget=settings.content_retrieval_token_budget,
        ),
        candidate_multiplier=settings.vector_search_candidate_multiplier,
        max_candidates=settings.vector_search_max_candidates,
        weights=settings.content_ranking_weights(),
        duplicate_overlap=settings.retrieval_duplicate_overlap,
        token_budget=settings.content_retrieval_token_budget,
        max_token_budget=settings.retrieval_max_token_budget,
    )


def get_content_discovery_service(request: Request) -> ContentDiscoveryService:
    """Build the Phase 5 evidence assembler from scoped repository adapters."""
    database = request.app.state.mongodb.database
    return ContentDiscoveryService(
        MongoCreatorProfileRepository(database),
        MongoProjectRepository(database),
        lambda profile_id: MongoContentPillarRepository(database, profile_id),
        lambda profile_id: MongoContentIdeaRepository(database, profile_id),
        lambda profile_id: MongoContentItemRepository(database, profile_id),
        lambda project_id: MongoProjectContextRepository(database, project_id),
        lambda project_id: MongoMemoryRepository(database, project_id=project_id),
        MongoSkillRepository(database),
    )


def get_content_novelty_service(request: Request) -> ContentNoveltyService:
    """Build Phase 6 advisory novelty checks over the existing content retrieval path."""
    return ContentNoveltyService(get_content_retrieval_service(request))


def get_content_repurpose_service(request: Request) -> ContentRepurposeService:
    """Build Phase 6 structured planning with reviewed content-skill metadata."""
    database = request.app.state.mongodb.database
    return ContentRepurposeService(
        get_content_retrieval_service(request),
        MongoSkillRepository(database),
    )


def get_content_export_service(request: Request) -> ContentExportService:
    """Build Phase 7 manual format/export adapters without a publishing provider."""
    settings: Settings = request.app.state.settings
    return ContentExportService(
        platform_services(linkedin_max_characters=settings.content_linkedin_max_characters)
    )


def get_content_publication_service(request: Request) -> ContentPublicationService:
    """Build Phase 8 review/approval logic over the existing content lifecycle."""
    settings: Settings = request.app.state.settings
    return ContentPublicationService(
        get_content_service(request),
        policy=PublicationSafetyPolicy(
            block_high_severity=settings.content_publication_block_high_severity,
            sensitive_terms=settings.content_publication_sensitive_terms,
            sensitive_project_ids=frozenset(settings.content_publication_sensitive_project_ids),
        ),
    )


def get_content_publishing_service(request: Request) -> ContentPublishingService:
    """Build Phase 9 explicit publishing with only the no-network manual provider."""
    settings: Settings = request.app.state.settings
    return ContentPublishingService(
        get_content_service(request),
        policy=PublicationSafetyPolicy(
            block_high_severity=settings.content_publication_block_high_severity,
            sensitive_terms=settings.content_publication_sensitive_terms,
            sensitive_project_ids=frozenset(settings.content_publication_sensitive_project_ids),
        ),
        publishers=default_publisher_registry(),
    )


def get_context_service(request: Request) -> ContextService:
    """Build the request-scoped context service over the MongoDB handle."""
    database = request.app.state.mongodb.database
    embeddings: EmbeddingService | None = request.app.state.embeddings
    return ContextService(
        MongoProjectRepository(database),
        lambda project_id: MongoProjectContextRepository(database, project_id),
        embeddings,
    )


def get_memory_service(request: Request) -> MemoryService:
    """Build the request-scoped memory service over the MongoDB handle."""
    database = request.app.state.mongodb.database
    embeddings: EmbeddingService | None = request.app.state.embeddings
    return MemoryService(
        MongoProjectRepository(database),
        lambda scope, project_id, client_id: MongoMemoryRepository(
            database,
            scope=scope,
            project_id=project_id,
            client_id=client_id,
        ),
        embeddings,
    )


def get_retrieval_service(request: Request) -> RetrievalService:
    """Build retrieval over the database and, when enabled, the loaded model.

    Lexical retrieval works without an embedding model, so a disabled model is
    refused by the vector endpoint rather than by this dependency. Project
    context is wired in as a source here; a request that names no project still
    reaches none of it, while global memory stays inside the verified organization.
    """
    embeddings: EmbeddingService | None = request.app.state.embeddings
    settings: Settings = request.app.state.settings
    database = request.app.state.mongodb.database
    return RetrievalService(
        MongoSkillRepository(database),
        MongoSkillChunkRepository(database),
        embeddings,
        contexts=lambda project_id: MongoProjectContextRepository(database, project_id),
        memories=lambda project_id, client_id: MongoMemoryRepository(
            database,
            project_id=project_id,
            client_id=client_id,
            # The repository's query guard is bound to the caller's current
            # project/client address; the filters still define the searched
            # union inside that address.
            scope=(
                "project_agent"
                if project_id is not None and client_id is not None
                else "agent"
                if client_id is not None
                else "project"
                if project_id is not None
                else "global"
            ),
        ),
        candidate_multiplier=settings.vector_search_candidate_multiplier,
        max_candidates=settings.vector_search_max_candidates,
        weights=settings.ranking_weights(),
        duplicate_overlap=settings.retrieval_duplicate_overlap,
        token_budget=settings.retrieval_token_budget,
        max_token_budget=settings.retrieval_max_token_budget,
    )


async def get_retrieval_logger(
    request: Request,
    principal: CurrentPrincipal,
) -> RetrievalLogger:
    """Build the retrieval hooks this deployment reports searches to.

    The structured hook always runs: it carries identifiers and counters that
    no deployment should have to choose to keep. Storing the query text is the
    opt-in half, because that is the part of a search that can describe the
    person who ran it.
    """
    settings: Settings = request.app.state.settings
    metadata = retrieval_metadata(request, principal)
    # A session identifier is an attribution claim, not client input. Retain it
    # only after the V4 session service verifies active ownership by this
    # bearer-linked client. Missing session wiring fails closed for provenance
    # while never affecting the underlying search.
    session_service = getattr(request.app.state, "agent_session_service", None)
    if metadata.session_id is not None and isinstance(session_service, AgentSessionService):
        try:
            await session_service.require_active_for_gateway(principal, metadata.session_id)
        except (AgentSessionAccessError, AgentSessionNotFoundError, AgentSessionStateError):
            metadata = replace(metadata, session_id=None)
    elif metadata.session_id is not None:
        metadata = replace(metadata, session_id=None)
    if not settings.retrieval_log_enabled:
        return StructuredRetrievalLogger(
            client_id=metadata.client_id,
            client_type=metadata.client_type,
            session_id=metadata.session_id,
        )
    database = request.app.state.mongodb.database
    return CompositeRetrievalLogger(
        StructuredRetrievalLogger(
            client_id=metadata.client_id,
            client_type=metadata.client_type,
            session_id=metadata.session_id,
        ),
        PersistentRetrievalLogger(
            lambda project_id: MongoRetrievalLogRepository(database, project_id=project_id),
            client_id=metadata.client_id,
            client_type=metadata.client_type,
            session_id=metadata.session_id,
        ),
    )


async def get_search_service(
    request: Request,
    principal: CurrentPrincipal,
) -> SearchService:
    """Build the unified search over the same retrieval every endpoint uses.

    The retrieval service is built here rather than separately, so the unified
    answer is drawn from exactly the sources the individual endpoints reach.
    """
    return SearchService(
        get_retrieval_service(request),
        logger=await get_retrieval_logger(request, principal),
    )
