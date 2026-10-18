"""Session adapters select the same action-specific repository gates as machines."""

from collections.abc import Awaitable, Callable, Coroutine
from typing import Any

from fastapi import Request, Response
from fastapi.routing import APIRoute

from app.api.concurrency import RevisionConflict
from app.api.routes.console import enabled
from app.db.errors import SearchUnavailableError
from app.embeddings.errors import EmbeddingError
from app.identity.browser import CurrentBrowser
from app.identity.crypto import AuthBusy, AuthRejected
from app.memory.errors import MemoryNotFoundError, MemorySupersessionError
from app.models.common import Identifier
from app.models.memory import MemoryScope
from app.projects.errors import (
    ContextNotFoundError,
    ProjectAlreadyExistsError,
    ProjectNotFoundError,
)
from app.repositories.errors import ConcurrencyConflictError
from app.security.policy.evaluation import PolicyDenied
from app.security.policy.resources import permitted
from app.security.tenant import TenantContextRequired
from app.security.tenant_policy import current_policy, select_action
from app.skills.errors import SkillNotFoundError


def action_gate(action: str) -> Callable[..., Awaitable[None]]:
    async def gate(request: Request, context: CurrentBrowser, org_id: Identifier) -> None:
        await enabled(request, context)
        policy = current_policy()
        if (
            not policy
            or not policy.managed
            or policy.subject_kind != "human"
            or policy.org_id != org_id
        ):
            raise PolicyDenied("verified human policy is required")
        select_action(action)
        if action.endswith((":write", ":approve", ":promote")):
            context.service.sessions.recent(context.row)

    return gate


def resource(action: str, kind: str, identifier: str) -> None:
    policy = current_policy()
    if not policy or not permitted(policy, action, kind, identifier):
        raise PolicyDenied("resource is outside effective access")


def shared_memory(action: str, scope: MemoryScope | None, project_id: str | None) -> MemoryScope:
    # Sponsorship/management is not a client's private-memory identity. Never
    # manufacture a client_id or accept one from a human request.
    resolved = scope or ("project" if project_id else "global")
    if resolved not in {"global", "project"}:
        raise PolicyDenied("private memory requires its verified machine identity")
    if (resolved == "project") != (project_id is not None):
        raise ValueError("memory address is inconsistent")
    policy = current_policy()
    if policy is None:
        raise PolicyDenied("verified policy is required")
    resource(
        action, "project" if project_id else "organization_knowledge", project_id or policy.org_id
    )
    return resolved


class KnowledgeRoute(APIRoute):
    """One content-free error mapping around existing service/domain errors."""

    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        handler = super().get_route_handler()

        async def guarded(request: Request) -> Response:
            from fastapi import HTTPException

            try:
                return await handler(request)
            except (AuthRejected, AuthBusy):
                raise
            except TenantContextRequired:
                raise PolicyDenied("operation is outside effective access") from None
            except (
                ProjectNotFoundError,
                ContextNotFoundError,
                MemoryNotFoundError,
                SkillNotFoundError,
            ):
                raise HTTPException(404, "knowledge was not found in this view") from None
            except ConcurrencyConflictError:
                raise RevisionConflict() from None
            except (ProjectAlreadyExistsError, MemorySupersessionError):
                raise HTTPException(409, "knowledge changed; reload the current record") from None
            except (EmbeddingError, SearchUnavailableError):
                raise HTTPException(
                    503, "knowledge retrieval or embedding is unavailable"
                ) from None
            except ValueError:
                raise HTTPException(422, "knowledge filters or cursor were not accepted") from None

        return guarded
