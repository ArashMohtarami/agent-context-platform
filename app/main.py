"""FastAPI application, MongoDB, and embedding lifecycle."""

import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from pymongo.asynchronous.database import AsyncDatabase
from pymongo.errors import PyMongoError

from app.api.concurrency import RevisionConflict, revision_conflict_handler
from app.api.middleware import (
    DEFAULT_MAX_REQUEST_BYTES,
    RequestSizeLimitMiddleware,
    SecurityHeadersMiddleware,
    unavailable_dependency,
    unhandled_error,
)
from app.api.routes.agent_activity import router as agent_activity_router
from app.api.routes.agents import router as agents_router
from app.api.routes.client_profiles import router as client_profiles_router
from app.api.routes.content import router as content_router
from app.api.routes.context import router as context_router
from app.api.routes.creator import router as creator_router
from app.api.routes.gateway import router as gateway_router
from app.api.routes.health import router as health_router
from app.api.routes.knowledge_candidates import router as knowledge_candidates_router
from app.api.routes.local_identity import router as local_identity_router
from app.api.routes.memory import router as memory_router
from app.api.routes.metrics import router as metrics_router
from app.api.routes.project_bindings import router as project_bindings_router
from app.api.routes.projects import router as projects_router
from app.api.routes.research import router as research_router
from app.api.routes.research_briefs import router as research_briefs_router
from app.api.routes.research_evidence import router as research_evidence_router
from app.api.routes.research_notes_claims import router as research_notes_claims_router
from app.api.routes.research_search import router as research_search_router
from app.api.routes.research_sources import router as research_sources_router
from app.api.routes.revalidation import router as revalidation_router
from app.api.routes.search import router as search_router
from app.api.routes.skill_update_proposals import router as skill_update_proposals_router
from app.api.routes.skills import router as skills_router
from app.core.config import Settings, get_settings
from app.core.logging import RequestLoggingMiddleware, configure_logging
from app.db.client import MongoDBClient
from app.db.client_types import MongoDocument
from app.db.content_indexes import ensure_content_text_indexes, ensure_content_vector_indexes
from app.db.context_indexes import ensure_context_text_index, ensure_context_vector_index
from app.db.errors import SearchUnavailableError
from app.db.initializer import initialize_database
from app.db.memory_indexes import ensure_memory_text_index, ensure_memory_vector_index
from app.db.research_indexes import ensure_research_text_indexes, ensure_research_vector_indexes
from app.db.text_indexes import ensure_text_indexes
from app.db.vector_indexes import ensure_vector_indexes
from app.embeddings.errors import EmbeddingError
from app.identity.browser import BrowserBoundaryMiddleware
from app.identity.crypto import AuthBusy, AuthRejected
from app.identity.factory import build_local_identity
from app.identity.http_errors import auth_busy, auth_rejected, private_validation_error
from app.mcp.gateway_controls import McpGatewayControls
from app.mcp.remote_http import RemoteMcpGateway, add_remote_mcp_route
from app.mcp.remote_security import RemoteMcpTransportPolicy
from app.observability.instruments import record_build_info
from app.repositories.agent_repository import (
    MongoAgentActivityRepository,
    MongoAgentClientRepository,
    MongoAgentSessionRepository,
)
from app.repositories.api_key_repository import MongoApiKeyRepository
from app.repositories.audit_repository import MongoAuditRepository
from app.repositories.client_profile_repository import MongoClientProfileRepository
from app.repositories.credential_repository import MongoCredentialResolver
from app.repositories.idempotency_repository import MongoGatewayIdempotencyRepository
from app.security.tenant_maintenance import TenantMaintenanceMiddleware
from app.services.agent_activity_service import AgentActivityService
from app.services.agent_client_service import AgentClientService
from app.services.agent_session_service import AgentSessionService
from app.services.api_key_service import ApiKeyService
from app.services.audit_service import AuditService
from app.services.auth_service import AuthService
from app.services.client_profile_service import ClientProfileService
from app.services.embedding_service import EmbeddingService, build_embedding_service
from app.services.gateway_idempotency_service import GatewayIdempotencyService

logger = logging.getLogger("agent_context.startup")


async def start_lexical_search(database: AsyncDatabase[MongoDocument]) -> None:
    """Reconcile the Search indexes, which need no embedding model.

    A failure is logged and reported by `/health/ready` rather than stopping
    startup, so the process still serves everything that does work.
    """
    try:
        action = await ensure_text_indexes(database)
        context_action = await ensure_context_text_index(database)
        memory_action = await ensure_memory_text_index(database)
        content_actions = await ensure_content_text_indexes(database)
        research_actions = await ensure_research_text_indexes(database)
    except SearchUnavailableError as error:
        logger.warning(
            "text_index_unavailable",
            extra={"embedding_error": type(error).__name__},
        )
        return
    logger.info(f"text_index_{action}")
    logger.info(f"context_text_index_{context_action}")
    logger.info(f"memory_text_index_{memory_action}")
    for action in content_actions:
        logger.info(f"content_text_index_{action}")
    for action in research_actions:
        logger.info(f"research_text_index_{action}")


async def start_embeddings(
    embeddings: EmbeddingService | None,
    database: AsyncDatabase[MongoDocument],
) -> None:
    """Load the embedding model, then reconcile the vector index it implies.

    Neither failure stops startup; `/health/ready` reports them instead, so the
    container is never reported healthy while retrieval cannot work.
    """
    if embeddings is None:
        logger.info("embedding_service_disabled")
        return
    try:
        info = await embeddings.start()
    except EmbeddingError as error:
        logger.warning(
            "embedding_model_unavailable",
            extra={
                "embedding_model": embeddings.model,
                "embedding_error": type(error).__name__,
            },
        )
        return
    try:
        action = await ensure_vector_indexes(database, dimensions=info.dimensions)
        context_action = await ensure_context_vector_index(database, dimensions=info.dimensions)
        memory_action = await ensure_memory_vector_index(database, dimensions=info.dimensions)
        content_actions = await ensure_content_vector_indexes(database, dimensions=info.dimensions)
        research_actions = await ensure_research_vector_indexes(
            database, dimensions=info.dimensions
        )
    except SearchUnavailableError as error:
        logger.warning(
            "vector_index_unavailable",
            extra={
                "embedding_model": info.model,
                "embedding_dimensions": info.dimensions,
                "embedding_error": type(error).__name__,
            },
        )
        return
    logger.info(
        f"vector_index_{action}",
        extra={"embedding_model": info.model, "embedding_dimensions": info.dimensions},
    )
    logger.info(
        f"context_vector_index_{context_action}",
        extra={"embedding_model": info.model, "embedding_dimensions": info.dimensions},
    )
    logger.info(
        f"memory_vector_index_{memory_action}",
        extra={"embedding_model": info.model, "embedding_dimensions": info.dimensions},
    )
    for action in content_actions:
        logger.info(
            f"content_vector_index_{action}",
            extra={"embedding_model": info.model, "embedding_dimensions": info.dimensions},
        )
    for action in research_actions:
        logger.info(
            f"research_vector_index_{action}",
            extra={"embedding_model": info.model, "embedding_dimensions": info.dimensions},
        )


def start_authentication(
    settings: Settings,
    database: AsyncDatabase[MongoDocument],
) -> AuthService | None:
    """Build the authenticator, or report that this deployment has none.

    Tenant domain traffic requires authentication. The narrow credential
    resolver establishes one organization before any domain repository runs.
    """
    if not settings.api_auth_enabled:
        raise RuntimeError("tenant domain traffic requires authentication and organization context")
    return AuthService(MongoCredentialResolver(database), MongoAgentClientRepository(database))


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build the API with an injectable configuration for tests."""

    remote_mcp = RemoteMcpGateway()

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncGenerator[None, None]:
        resolved_settings = settings if settings is not None else get_settings()
        configure_logging(resolved_settings.log_level)
        # Published before anything else can fail, so a deployment that never
        # reaches readiness still says which build it was trying to be.
        record_build_info()
        mongodb = MongoDBClient(resolved_settings)
        await mongodb.connect()
        if resolved_settings.v6_tenant_isolation:
            if not resolved_settings.api_auth_enabled:
                raise RuntimeError("authentication-disabled tenant startup is refused")
            await initialize_database(
                mongodb.database,
                tenant_foundation=True,
                tenant_isolation=True,
                local_identity=resolved_settings.v6_local_identity,
            )
        elif resolved_settings.v6_tenant_foundation:
            await initialize_database(mongodb.database, tenant_foundation=True)
        else:
            await initialize_database(mongodb.database)
        application.state.tenant_control_database = mongodb.database
        await start_lexical_search(mongodb.database)
        embeddings = build_embedding_service(resolved_settings)
        await start_embeddings(embeddings, mongodb.database)
        application.state.settings = resolved_settings
        application.state.mongodb = mongodb
        application.state.embeddings = embeddings
        application.state.auth = start_authentication(resolved_settings, mongodb.database)
        application.state.local_identity = (
            build_local_identity(resolved_settings, mongodb.database)
            if resolved_settings.v6_local_identity
            else None
        )
        application.state.agent_client_service = AgentClientService(
            MongoAgentClientRepository(mongodb.database),
            ApiKeyService(MongoApiKeyRepository(mongodb.database)),
        )
        application.state.client_profile_service = ClientProfileService(
            MongoClientProfileRepository(mongodb.database),
            MongoAgentClientRepository(mongodb.database),
        )
        application.state.agent_activity_service = AgentActivityService(
            MongoAgentActivityRepository(mongodb.database)
        )
        application.state.agent_session_service = AgentSessionService(
            MongoAgentSessionRepository(mongodb.database)
        )
        application.state.gateway_idempotency_service = GatewayIdempotencyService(
            MongoGatewayIdempotencyRepository(mongodb.database),
            retention_seconds=resolved_settings.mcp_idempotency_retention_seconds,
        )
        application.state.audit = AuditService(
            MongoAuditRepository(mongodb.database) if resolved_settings.audit_log_enabled else None
        )
        try:
            async with remote_mcp.run(
                application,
                authorization=resolved_settings.mcp_authorization(),
                server_name=resolved_settings.mcp_server_name,
                controls=McpGatewayControls.from_settings(resolved_settings),
                transport_policy=RemoteMcpTransportPolicy.from_settings(resolved_settings),
            ):
                yield
        finally:
            if application.state.local_identity is not None:
                application.state.local_identity.passwords.close()
            if embeddings is not None:
                await embeddings.close()
            await mongodb.close()

    application = FastAPI(
        title=settings.app_name if settings is not None else "Agent Context Platform",
        lifespan=lifespan,
    )
    # The server generates the response to an unhandled exception outside every
    # middleware, so without this it is the one answer that would carry neither
    # the security headers nor the shape the rest of the API uses.
    application.add_exception_handler(Exception, unhandled_error)
    application.add_exception_handler(RevisionConflict, revision_conflict_handler)
    application.add_exception_handler(AuthRejected, auth_rejected)
    application.add_exception_handler(AuthBusy, auth_busy)
    application.add_exception_handler(RequestValidationError, private_validation_error)
    # More specific than the handler above, and registered after it so it wins:
    # a database that is down is an outage to report as one, not a defect.
    application.add_exception_handler(PyMongoError, unavailable_dependency)
    # Outermost first: a body too large is refused before anything reads it,
    # and every answer carries the security headers whatever produced it.
    application.add_middleware(TenantMaintenanceMiddleware)
    application.add_middleware(BrowserBoundaryMiddleware)
    application.add_middleware(SecurityHeadersMiddleware)
    application.add_middleware(
        RequestSizeLimitMiddleware,
        maximum_bytes=(settings.api_max_request_bytes if settings is not None else None)
        or DEFAULT_MAX_REQUEST_BYTES,
    )
    application.add_middleware(RequestLoggingMiddleware)
    application.include_router(health_router)
    application.include_router(local_identity_router)
    application.include_router(metrics_router)
    application.include_router(skills_router)
    application.include_router(projects_router)
    application.include_router(project_bindings_router)
    application.include_router(context_router)
    application.include_router(memory_router)
    application.include_router(search_router)
    application.include_router(creator_router)
    application.include_router(content_router)
    application.include_router(research_router)
    application.include_router(knowledge_candidates_router)
    application.include_router(skill_update_proposals_router)
    application.include_router(research_sources_router)
    application.include_router(research_notes_claims_router)
    application.include_router(research_evidence_router)
    application.include_router(research_briefs_router)
    application.include_router(research_search_router)
    application.include_router(revalidation_router)
    application.include_router(agents_router)
    application.include_router(client_profiles_router)
    application.include_router(agent_activity_router)
    application.include_router(gateway_router)
    # Streamable HTTP is the same low-level MCP server as stdio.  There is no
    # Claude-specific endpoint or backend: both transports share one tool
    # registry and the Phase 2 client-aware gateway.
    add_remote_mcp_route(application, remote_mcp)
    return application


app = create_app()
