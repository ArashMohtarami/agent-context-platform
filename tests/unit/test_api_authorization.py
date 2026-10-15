"""What the API refuses, over HTTP, with a real key presented.

These go through the whole stack — header, dependency, scope, project — rather
than calling the checks directly, because the failure worth ruling out is an
endpoint that forgot to declare one, and only a request can show that.
"""

from collections.abc import Awaitable, Callable
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import APIRouter, FastAPI
from httpx import ASGITransport, AsyncClient

from app.api.dependencies import (
    get_content_service,
    get_context_service,
    get_creator_service,
    get_memory_service,
    get_project_service,
    get_research_project_service,
    get_retrieval_service,
    get_search_service,
    get_skill_service,
)
from app.api.routes.agent_activity import router as agent_activity_router
from app.api.routes.agents import router as agents_router
from app.api.routes.client_profiles import router as client_profiles_router
from app.api.routes.content import router as content_router
from app.api.routes.context import router as context_router
from app.api.routes.creator import router as creator_router
from app.api.routes.gateway import router as gateway_router
from app.api.routes.knowledge_candidates import router as knowledge_candidates_router
from app.api.routes.memory import router as memory_router
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
from app.models.common import Page
from app.models.content import ContentIdeaDocument, CreatorProfileDocument
from app.models.research import ResearchProjectDocument
from app.retrieval.context_builder import RetrievalContext
from app.retrieval.ranking import DEFAULT_WEIGHTS
from app.retrieval.types import RetrievalDiagnostics
from app.search.types import KnowledgeSearchResult, RetrievalTrace
from app.security.principal import UNAUTHENTICATED
from app.security.scopes import ALL_SCOPES, Scope
from app.services.audit_service import AuditService
from app.services.auth_service import AuthService
from tests.api_doubles import authenticated_application, open_application
from tests.memory_doubles import memory
from tests.project_doubles import context, project
from tests.security_doubles import (
    NOW,
    FakeApiKeyRepository,
    FakeAuditRepository,
    bearer,
    expired_key,
    issued_key,
)

PROJECT = "payment-service"
OTHER_PROJECT = "wallet-service"
ROUTERS = (
    skills_router,
    projects_router,
    context_router,
    memory_router,
    search_router,
    creator_router,
    content_router,
    research_router,
    research_sources_router,
    research_notes_claims_router,
    research_evidence_router,
    research_briefs_router,
    research_search_router,
    revalidation_router,
    knowledge_candidates_router,
    skill_update_proposals_router,
    agents_router,
    project_bindings_router,
    client_profiles_router,
)


def context_body(project_id: str) -> dict[str, Any]:
    """Build one valid create-context body for the given project."""
    return {
        "project_id": project_id,
        "type": "architecture",
        "title": "Refund flow",
        "content": "Refunds are idempotent.",
        "importance": 0.9,
        "source": "user",
    }


def memory_body(project_id: str | None = None) -> dict[str, Any]:
    """Build one valid create-memory body."""
    return {
        "type": "preference",
        "content": "Use strict typing.",
        "importance": 0.8,
        "confidence": 0.9,
        "source": "user",
        "project_id": project_id,
    }


# One request per scope, chosen to be the cheapest route that requires it.
CALLS: dict[Scope, tuple[str, str, dict[str, Any] | None]] = {
    "skills:read": ("GET", "/api/v1/skills", None),
    "projects:read": ("GET", "/api/v1/projects", None),
    "projects:write": (
        "POST",
        "/api/v1/projects",
        {
            "project_id": PROJECT,
            "name": "Payments",
            "description": "d",
            "repository": "r",
            "environment": "production",
        },
    ),
    "context:read": ("GET", f"/api/v1/context?project_id={PROJECT}", None),
    "context:write": ("POST", "/api/v1/context", context_body(PROJECT)),
    "memory:read": ("GET", "/api/v1/memory", None),
    "memory:write": ("POST", "/api/v1/memory", memory_body()),
    "search:read": ("POST", "/api/v1/search", {"query": "refunds"}),
    "creator:read": ("GET", "/api/v2/creator-profiles", None),
    "creator:write": (
        "POST",
        "/api/v2/creator-profiles",
        {"profile_id": "personal-brand", "name": "Personal Brand"},
    ),
    "content:read": ("GET", "/api/v2/content-ideas?profile_id=personal-brand", None),
    "content:write": (
        "POST",
        "/api/v2/content-ideas",
        {
            "profile_id": "personal-brand",
            "idea_id": "idempotency",
            "title": "Idempotency",
            "description": "A manually supplied content idea.",
            "platforms": ["linkedin"],
            "origin": {"type": "memory"},
            "priority": 0.8,
        },
    ),
    "research:read": ("GET", "/api/v3/research/projects", None),
    "research:write": (
        "POST",
        "/api/v3/research/projects",
        {
            "research_project_id": "authorization-research",
            "name": "Authorization research",
            "question": "Does this endpoint require its dedicated scope?",
            "type": "technical_research",
            "related_project_ids": [],
        },
    ),
}
# Requests that name a project, for the project-authorization cases. Each is
# written twice, once per project, so a refusal can be shown to be about the
# project and not about the route.
PROJECT_CALLS: tuple[Callable[[str], tuple[str, str, dict[str, Any] | None]], ...] = (
    lambda named: ("GET", f"/api/v1/context?project_id={named}", None),
    lambda named: ("POST", "/api/v1/context", context_body(named)),
    lambda named: ("POST", "/api/v1/context/search", {"query": "q", "project_id": named}),
    lambda named: ("GET", f"/api/v1/memory?project_id={named}", None),
    lambda named: ("POST", "/api/v1/memory", memory_body(named)),
    lambda named: ("POST", "/api/v1/search", {"query": "q", "project_id": named}),
    lambda named: ("POST", "/api/v1/skills/search/hybrid", {"query": "q", "project_id": named}),
)


def services() -> dict[Any, Callable[[], Awaitable[MagicMock]]]:
    """Override every service dependency with one that answers emptily.

    Nothing here is under test. These exist so a request that passes every
    check reaches a handler and comes back 200, which is what makes an
    authorized case distinguishable from a refused one — an empty answer is
    still an answer, and the distinction is the status code.
    """
    empty = MagicMock()
    for name in ("list_skills", "list_projects", "list_context", "list_memories"):
        setattr(empty, name, AsyncMock(return_value=Page(items=())))
    empty.create_project = AsyncMock(return_value=project(PROJECT))
    empty.create_profile = AsyncMock(
        return_value=CreatorProfileDocument(profile_id="personal-brand", name="Personal Brand")
    )
    content_idea = ContentIdeaDocument(
        profile_id="personal-brand",
        idea_id="idempotency",
        title="Idempotency",
        description="A manually supplied content idea.",
        platforms=["linkedin"],
        origin={"type": "memory"},
        priority=0.8,
    )
    empty.list_ideas = AsyncMock(return_value=Page(items=(content_idea,)))
    empty.create_idea = AsyncMock(return_value=content_idea)
    empty.create_context = AsyncMock(return_value=context(project_id=PROJECT))
    empty.create_memory = AsyncMock(return_value=memory())
    empty.context_search = AsyncMock(return_value=())
    empty.memory_search = AsyncMock(return_value=())
    empty.search_knowledge = AsyncMock(return_value=nothing_found())
    empty.list_profiles = AsyncMock(return_value=Page(items=()))
    empty.hybrid_search = AsyncMock(return_value=nothing_found().context)
    research = MagicMock()
    research_record = ResearchProjectDocument(
        research_project_id="authorization-research",
        name="Authorization research",
        question="Does this endpoint require its dedicated scope?",
        type="technical_research",
    )
    research.list_projects = AsyncMock(return_value=Page(items=()))
    research.create_project = AsyncMock(return_value=research_record)

    async def provide() -> MagicMock:
        return empty

    async def provide_research() -> MagicMock:
        return research

    return {
        get_skill_service: provide,
        get_project_service: provide,
        get_research_project_service: provide_research,
        get_creator_service: provide,
        get_content_service: provide,
        get_context_service: provide,
        get_memory_service: provide,
        get_search_service: provide,
        get_retrieval_service: provide,
    }


def nothing_found() -> KnowledgeSearchResult:
    """Build one search that ran correctly and matched nothing."""
    return KnowledgeSearchResult(
        context=RetrievalContext(
            query="q",
            project_id=None,
            items=(),
            token_count=0,
            token_budget=4_000,
            diagnostics=RetrievalDiagnostics(
                methods=(),
                weights=DEFAULT_WEIGHTS.as_mapping(),
                vector_matches=0,
                lexical_matches=0,
                merged=0,
                filtered_out=0,
                duplicates_removed=0,
                ranked=0,
                token_budget=4_000,
                dropped_for_budget=0,
            ),
        ),
        trace=RetrievalTrace(
            query_id="00000000-0000-4000-8000-000000000000",
            retrieval_method="hybrid",
            requested_sources=(),
            searched_sources=(),
            skipped_sources=(),
            latency_ms=1.0,
        ),
    )


def guarded(
    *records: Any,
    audit: FakeAuditRepository | None = None,
) -> FastAPI:
    """Build an application that authenticates against the given stored keys."""
    keys = FakeApiKeyRepository(*records)
    application = authenticated_application(
        *ROUTERS,
        auth=AuthService(keys, clock=lambda: NOW),
        audit=AuditService(audit),
    )
    application.dependency_overrides.update(services())
    return application


async def call(
    application: FastAPI,
    method: str,
    path: str,
    body: dict[str, Any] | None = None,
    *,
    headers: dict[str, str] | None = None,
) -> Any:
    """Send one request."""
    transport = ASGITransport(app=application)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.request(method, path, json=body, headers=headers)


# --- a credential, or none -------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("scope", sorted(CALLS))
async def test_a_request_without_a_key_is_refused(scope: Scope) -> None:
    record, _ = issued_key()
    method, path, body = CALLS[scope]

    response = await call(guarded(record), method, path, body)

    assert response.status_code == 401
    assert response.json() == {"detail": "the API key was not accepted"}
    # A client is told what kind of credential to bring.
    assert response.headers["www-authenticate"] == "Bearer"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "header",
    [
        {"Authorization": "Bearer not-a-key"},
        {"Authorization": "Bearer "},
        {"Authorization": "acp_0123456789abcdef_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"},
        {"Authorization": "Basic YWRtaW46YWRtaW4="},
        {"Authorization": "Bearer acp_0123456789abcdef_wrongsecretwrongsecretwrongsecret"},
        {"X-Api-Key": "acp_0123456789abcdef_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"},
    ],
)
async def test_a_credential_that_is_not_a_valid_key_is_refused(header: dict[str, str]) -> None:
    record, _ = issued_key()

    response = await call(guarded(record), "GET", "/api/v1/skills", headers=header)

    assert response.status_code == 401


@pytest.mark.asyncio
async def test_a_revoked_key_is_refused() -> None:
    record, token = issued_key(status="revoked")

    response = await call(guarded(record), "GET", "/api/v1/skills", headers=bearer(token))

    assert response.status_code == 401


@pytest.mark.asyncio
async def test_an_expired_key_is_refused() -> None:
    record, token = expired_key()

    response = await call(guarded(record), "GET", "/api/v1/skills", headers=bearer(token))

    assert response.status_code == 401


@pytest.mark.asyncio
async def test_a_valid_key_is_accepted() -> None:
    record, token = issued_key()

    response = await call(guarded(record), "GET", "/api/v1/skills", headers=bearer(token))

    assert response.status_code == 200


# --- scopes ----------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("scope", sorted(CALLS))
async def test_each_endpoint_accepts_the_key_that_holds_its_scope(scope: Scope) -> None:
    record, token = issued_key(scopes=(scope,))
    method, path, body = CALLS[scope]

    response = await call(guarded(record), method, path, body, headers=bearer(token))

    assert response.status_code != 403


@pytest.mark.asyncio
@pytest.mark.parametrize("scope", sorted(CALLS))
async def test_each_endpoint_refuses_a_key_holding_every_other_scope(scope: Scope) -> None:
    # Granted everything except the one thing this endpoint needs, so a pass
    # means the endpoint checks that scope and not merely some scope.
    others = tuple(other for other in ALL_SCOPES if other != scope)
    record, token = issued_key(scopes=others)
    method, path, body = CALLS[scope]

    response = await call(guarded(record), method, path, body, headers=bearer(token))

    assert response.status_code == 403
    # Named, because that tells a legitimate caller what to ask for.
    assert scope in response.json()["detail"]


@pytest.mark.asyncio
async def test_reading_does_not_imply_writing() -> None:
    record, token = issued_key(scopes=("context:read", "memory:read", "projects:read"))
    application = guarded(record)

    for scope in ("context:write", "memory:write", "projects:write"):
        method, path, body = CALLS[scope]  # type: ignore[index]
        response = await call(application, method, path, body, headers=bearer(token))
        assert response.status_code == 403


@pytest.mark.asyncio
async def test_a_scope_refusal_names_no_credential() -> None:
    record, token = issued_key(scopes=("memory:read",))

    response = await call(guarded(record), "GET", "/api/v1/skills", headers=bearer(token))

    body = response.text
    assert response.status_code == 403
    assert token not in body
    assert record.key_hash not in body
    assert record.key_id not in body


# --- projects --------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("build", PROJECT_CALLS)
async def test_a_key_reaches_the_project_it_was_granted(
    build: Callable[[str], tuple[str, str, dict[str, Any] | None]],
) -> None:
    record, token = issued_key(projects=(PROJECT,))
    method, path, body = build(PROJECT)

    response = await call(guarded(record), method, path, body, headers=bearer(token))

    assert response.status_code != 403


@pytest.mark.asyncio
@pytest.mark.parametrize("build", PROJECT_CALLS)
async def test_a_key_cannot_reach_another_project(
    build: Callable[[str], tuple[str, str, dict[str, Any] | None]],
) -> None:
    record, token = issued_key(projects=(PROJECT,))
    method, path, body = build(OTHER_PROJECT)

    response = await call(guarded(record), method, path, body, headers=bearer(token))

    assert response.status_code == 403


@pytest.mark.asyncio
async def test_a_refused_project_is_never_named_back() -> None:
    record, token = issued_key(projects=(PROJECT,))

    response = await call(
        guarded(record),
        "GET",
        f"/api/v1/context?project_id={OTHER_PROJECT}",
        headers=bearer(token),
    )

    # Naming it would confirm it exists, which is a different disclosure from
    # refusing the request.
    assert response.status_code == 403
    assert OTHER_PROJECT not in response.text


@pytest.mark.asyncio
async def test_a_key_granted_no_project_reaches_every_project() -> None:
    record, token = issued_key(projects=())
    application = guarded(record)

    for named in (PROJECT, OTHER_PROJECT):
        response = await call(
            application, "GET", f"/api/v1/context?project_id={named}", headers=bearer(token)
        )
        assert response.status_code != 403


@pytest.mark.asyncio
async def test_a_project_check_follows_the_value_not_the_route() -> None:
    # `project_id` arrives in a query string here and in a body there. Both are
    # checked, because a caller chooses the value and not the route it takes.
    record, token = issued_key(projects=(PROJECT,))
    application = guarded(record)

    in_query = await call(
        application, "GET", f"/api/v1/context?project_id={OTHER_PROJECT}", headers=bearer(token)
    )
    in_body = await call(
        application, "POST", "/api/v1/context", context_body(OTHER_PROJECT), headers=bearer(token)
    )

    assert in_query.status_code == 403
    assert in_body.status_code == 403


# --- the shape of the enforcement itself -----------------------------------


@pytest.mark.asyncio
async def test_an_application_that_never_decided_whether_it_authenticates_fails_closed() -> None:
    # A wiring mistake must not become an unauthenticated platform, so an app
    # with no decision refuses rather than serves.
    bare = FastAPI()
    for router in ROUTERS:
        bare.include_router(router)
    bare.dependency_overrides.update(services())

    response = await call(bare, "GET", "/api/v1/skills")

    assert response.status_code == 500
    assert response.json() == {"detail": "authentication is not configured"}


@pytest.mark.asyncio
async def test_an_explicitly_authentication_disabled_deployment_refuses_domain_traffic() -> None:
    application = open_application(*ROUTERS)
    application.dependency_overrides.update(services())
    application.state.auth = None

    response = await call(application, "GET", "/api/v1/skills")

    assert response.status_code == 401


def test_every_route_that_is_not_health_declares_a_scope() -> None:
    # Asserted against the routers rather than a list kept here, so a route
    # added without a scope fails this instead of quietly being open.
    for router in ROUTERS:
        for route in router.routes:
            dependencies = _declared(route)
            assert dependencies, f"{route.path} declares no authorization"  # type: ignore[attr-defined]


# The closure `requires()` returns. A route declares a scope by depending on
# one of these, whether as a route-level dependency or an annotated parameter.
_SCOPE_DEPENDENCY = "requires.<locals>.dependency"


def _declared(route: Any) -> list[Any]:
    """Return the scope dependencies one route declares, however it wrote them."""
    dependant = getattr(route, "dependant", None)
    if dependant is None:
        return []
    return [
        dependency
        for dependency in dependant.dependencies
        if getattr(dependency.call, "__qualname__", "") == _SCOPE_DEPENDENCY
    ]


def test_the_routers_under_test_are_every_router_the_application_serves() -> None:
    # If a router is added to the app and not here, the coverage above silently
    # stops covering it.
    from app.api.routes.local_identity import router as local_identity_router
    from app.main import create_app

    application = create_app.__wrapped__ if hasattr(create_app, "__wrapped__") else create_app
    served = {
        route.path
        for route in application().routes  # type: ignore[operator]
        if getattr(route, "path", "").startswith("/api/")
    }
    # Agent activity selects either an admin scope or a self-audit scope
    # dynamically inside its handler; its dedicated API tests cover those
    # branches, while this table verifies every declarative route dependency.
    covered = {
        route.path
        for router in (*ROUTERS, gateway_router, agent_activity_router, local_identity_router)
        for route in router.routes
    }

    assert served == covered


def test_an_unauthenticated_caller_is_named_so_in_a_record() -> None:
    assert UNAUTHENTICATED == "unauthenticated"


# --- what gets recorded ----------------------------------------------------


@pytest.mark.asyncio
async def test_a_refusal_is_recorded_without_the_credential() -> None:
    record, token = issued_key(scopes=("memory:read",), projects=(PROJECT,))
    trail = FakeAuditRepository()
    application = guarded(record, audit=trail)

    await call(application, "GET", "/api/v1/skills", headers=bearer(token))
    await call(
        application,
        "GET",
        f"/api/v1/context?project_id={OTHER_PROJECT}",
        headers=bearer("acp_0123456789abcdef_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"),
    )

    written = trail.records
    assert ("authorize", "denied") in trail.actions()
    assert ("authenticate", "denied") in trail.actions()
    for entry in written:
        assert token not in str(entry.model_dump())
        assert record.key_hash not in str(entry.model_dump())


@pytest.mark.asyncio
async def test_a_search_query_never_reaches_the_audit_trail() -> None:
    record, token = issued_key(scopes=("search:read",), projects=(PROJECT,))
    trail = FakeAuditRepository()
    application = guarded(record, audit=trail)
    query = "what is the production database password"

    await call(
        application,
        "POST",
        "/api/v1/search",
        {"query": query, "project_id": OTHER_PROJECT},
        headers=bearer(token),
    )

    # A path is a route; a query string is the caller's data, and a search
    # query is the one field that can describe the person who ran it.
    assert trail.records
    for entry in trail.records:
        assert query not in str(entry.model_dump())


@pytest.mark.asyncio
async def test_a_write_is_recorded_but_a_read_is_not() -> None:
    record, token = issued_key()
    trail = FakeAuditRepository()
    application = guarded(record, audit=trail)

    await call(application, "GET", "/api/v1/skills", headers=bearer(token))
    await call(application, "POST", "/api/v1/context", context_body(PROJECT), headers=bearer(token))
    await call(application, "POST", "/api/v1/memory", memory_body(PROJECT), headers=bearer(token))

    # Recording every read would bury the entries that matter under the ones
    # that do not. What an investigator needs afterwards is who added or
    # corrected a piece of knowledge, not who looked at one.
    assert trail.actions() == [("context.write", "allowed"), ("memory.write", "allowed")]
    assert [entry.project_id for entry in trail.records] == [PROJECT, PROJECT]


@pytest.mark.asyncio
async def test_a_recorded_write_says_what_happened_not_what_was_written() -> None:
    record, token = issued_key()
    trail = FakeAuditRepository()
    body = context_body(PROJECT)
    body["content"] = "The production signing key is hunter2."

    await call(guarded(record, audit=trail), "POST", "/api/v1/context", body, headers=bearer(token))

    written = str(trail.records[0].model_dump())
    assert trail.records[0].action == "context.write"
    # A trail that copied stored knowledge would double what a leak exposes
    # while telling an investigator nothing the records themselves cannot.
    assert "hunter2" not in written
    assert body["title"] not in written


@pytest.mark.asyncio
async def test_a_refused_write_is_recorded_as_refused_and_never_as_done() -> None:
    record, token = issued_key(scopes=("context:read",))
    trail = FakeAuditRepository()

    response = await call(
        guarded(record, audit=trail),
        "POST",
        "/api/v1/context",
        context_body(PROJECT),
        headers=bearer(token),
    )

    assert response.status_code == 403
    assert trail.actions() == [("authorize", "denied")]


@pytest.mark.asyncio
async def test_an_unwritable_audit_trail_does_not_change_the_decision() -> None:
    record, token = issued_key(scopes=("memory:read",))
    application = guarded(record, audit=FakeAuditRepository(fails=True))

    refused = await call(application, "GET", "/api/v1/skills", headers=bearer(token))
    allowed = await call(application, "GET", "/api/v1/memory", headers=bearer(token))

    # A refusal stays a refusal and a success stays a success; neither becomes
    # a 500 because the trail could not be written.
    assert refused.status_code == 403
    assert allowed.status_code == 200


@pytest.mark.asyncio
async def test_using_a_key_records_that_it_was_used() -> None:
    record, token = issued_key()
    keys = FakeApiKeyRepository(record)
    application = authenticated_application(
        *ROUTERS, auth=AuthService(keys, clock=lambda: NOW), audit=AuditService(None)
    )
    application.dependency_overrides.update(services())

    await call(application, "GET", "/api/v1/skills", headers=bearer(token))

    assert keys.touched == [(record.key_id, NOW)]


def test_health_is_reachable_without_a_key() -> None:
    # Docker's healthcheck has no credential, and readiness carries no
    # knowledge — only whether the parts of the platform are working.
    from app.api.routes.health import router as health_router

    assert isinstance(health_router, APIRouter)
    for route in health_router.routes:
        assert not _declared(route)
