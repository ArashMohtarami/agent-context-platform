"""Concurrency-tolerant MongoDB migration runner."""

from datetime import UTC, datetime
from typing import Final

from pymongo.asynchronous.database import AsyncDatabase
from pymongo.errors import DuplicateKeyError

from app.db.client_types import MongoDocument
from app.db.migrations.base import Migration
from app.db.migrations.v001_initial_schema import InitialSchemaMigration
from app.db.migrations.v002_skill_chunk_embeddings import SkillChunkEmbeddingMigration
from app.db.migrations.v003_project_context_embeddings import ProjectContextEmbeddingMigration
from app.db.migrations.v004_memory_records import MemoryRecordMigration
from app.db.migrations.v005_api_keys_and_audit import ApiKeyAndAuditMigration
from app.db.migrations.v006_creator_content_schema import CreatorContentSchemaMigration
from app.db.migrations.v007_creator_profile_access import CreatorProfileAccessMigration
from app.db.migrations.v008_content_audit_action import ContentAuditActionMigration
from app.db.migrations.v009_content_retrieval_embeddings import ContentRetrievalEmbeddingMigration
from app.db.migrations.v010_content_approval_audit_action import ContentApprovalAuditActionMigration
from app.db.migrations.v011_content_publication_receipt import ContentPublicationReceiptMigration
from app.db.migrations.v012_content_metric_source_metadata import ContentMetricSourceMigration
from app.db.migrations.v013_research_schema import ResearchSchemaMigration
from app.db.migrations.v014_research_project_auth import ResearchProjectAuthorizationMigration
from app.db.migrations.v015_research_source_registry import ResearchSourceRegistryMigration
from app.db.migrations.v016_research_brief_structure import ResearchBriefStructureMigration
from app.db.migrations.v017_candidate_review_workflow import CandidateReviewWorkflowMigration
from app.db.migrations.v018_controlled_promotion_provenance import (
    ControlledPromotionProvenanceMigration,
)
from app.db.migrations.v019_skill_update_proposals import SkillUpdateProposalMigration
from app.db.migrations.v020_content_brief_research_evidence import (
    ContentBriefResearchEvidenceMigration,
)
from app.db.migrations.v021_revalidation_freshness import RevalidationFreshnessMigration
from app.db.migrations.v022_agent_identity_schema import AgentIdentitySchemaMigration
from app.db.migrations.v023_agent_identity_contracts import AgentIdentityContractMigration
from app.db.migrations.v024_agent_credentials import AgentCredentialMigration
from app.db.migrations.v025_retrieval_log_agent_metadata import RetrievalLogAgentMetadataMigration
from app.db.migrations.v026_project_binding_normalization import (
    ProjectBindingNormalizationMigration,
)
from app.db.migrations.v027_agent_memory_scopes import AgentMemoryScopesMigration
from app.db.migrations.v028_actor_provenance import ActorProvenanceMigration
from app.db.migrations.v029_agent_session_lifecycle import AgentSessionLifecycleMigration
from app.db.migrations.v030_agent_scope_validator import AgentScopeValidatorMigration
from app.db.migrations.v031_gateway_idempotency import GatewayIdempotencyMigration
from app.db.migrations.v032_skill_update_proposal_actor import SkillUpdateProposalActorMigration
from app.db.migrations.v033_client_profiles import ClientProfileMigration
from app.db.migrations.v034_client_profile_lifecycle import ClientProfileLifecycleMigration
from app.db.migrations.v035_gemini_client_type import GeminiClientTypeMigration

MIGRATIONS: Final[tuple[Migration, ...]] = (
    InitialSchemaMigration(),
    SkillChunkEmbeddingMigration(),
    ProjectContextEmbeddingMigration(),
    MemoryRecordMigration(),
    ApiKeyAndAuditMigration(),
    CreatorContentSchemaMigration(),
    CreatorProfileAccessMigration(),
    ContentAuditActionMigration(),
    ContentRetrievalEmbeddingMigration(),
    ContentApprovalAuditActionMigration(),
    ContentPublicationReceiptMigration(),
    ContentMetricSourceMigration(),
    ResearchSchemaMigration(),
    ResearchProjectAuthorizationMigration(),
    ResearchSourceRegistryMigration(),
    ResearchBriefStructureMigration(),
    CandidateReviewWorkflowMigration(),
    ControlledPromotionProvenanceMigration(),
    SkillUpdateProposalMigration(),
    ContentBriefResearchEvidenceMigration(),
    RevalidationFreshnessMigration(),
    AgentIdentitySchemaMigration(),
    AgentIdentityContractMigration(),
    AgentCredentialMigration(),
    RetrievalLogAgentMetadataMigration(),
    ProjectBindingNormalizationMigration(),
    AgentMemoryScopesMigration(),
    ActorProvenanceMigration(),
    AgentSessionLifecycleMigration(),
    AgentScopeValidatorMigration(),
    GatewayIdempotencyMigration(),
    SkillUpdateProposalActorMigration(),
    ClientProfileMigration(),
    ClientProfileLifecycleMigration(),
    GeminiClientTypeMigration(),
)


async def run_migrations(database: AsyncDatabase[MongoDocument]) -> None:
    """Apply each unapplied migration and record its version once."""
    history = database["_schema_migrations"]
    await history.create_index("version", unique=True, name="migration_version_uq")
    applied = {
        document["version"]
        async for document in history.find({}, {"version": 1, "_id": 0})
        if isinstance(document.get("version"), int)
    }
    for migration in MIGRATIONS:
        if migration.version in applied:
            continue
        await migration.up(database)
        try:
            await history.insert_one(
                {
                    "version": migration.version,
                    "description": migration.description,
                    "applied_at": datetime.now(UTC),
                }
            )
        except DuplicateKeyError:
            # A concurrent instance completed the same idempotent migration.
            continue
