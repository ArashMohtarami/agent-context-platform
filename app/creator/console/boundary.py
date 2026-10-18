"""Safe content errors and exact action/resource gates."""

from collections.abc import Callable, Coroutine
from typing import Any

from fastapi import HTTPException, Request, Response

from app.content import errors as content_errors
from app.creator import errors as creator_errors
from app.creator.console.audit import record
from app.knowledge.boundary import resource
from app.research.console.boundary import ResearchRoute
from app.security.policy.evaluation import PolicyDenied
from app.security.tenant_policy import current_action, current_policy


def project_selector() -> tuple[str, ...] | None:
    p = current_policy()
    if p is None:
        raise PolicyDenied("verified human policy is required")
    return p.selector(current_action() or "", "project")


def profile(identifier: str) -> None:
    resource(current_action() or "", "creator_profile", identifier)


def types_with(suffix: str) -> tuple[type[Exception], ...]:
    return tuple(
        c
        for m in (content_errors, creator_errors)
        for n, c in vars(m).items()
        if isinstance(c, type) and issubclass(c, Exception) and n.endswith(suffix)
    )


MISSING = types_with("NotFoundError")
CONFLICT: tuple[type[Exception], ...] = (
    *types_with("AlreadyExistsError"),
    *types_with("StateError"),
    content_errors.ContentVersionConflictError,
    content_errors.ContentAlreadyPublishedError,
)


class CreatorRoute(ResearchRoute):
    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        handler = super().get_route_handler()

        async def guarded(request: Request) -> Response:
            try:
                response = await handler(request)
            except Exception as error:
                await record(
                    request,
                    "denied"
                    if isinstance(
                        error,
                        (
                            PolicyDenied,
                            ValueError,
                            content_errors.ContentError,
                            *MISSING,
                            *CONFLICT,
                        ),
                    )
                    or getattr(error, "status_code", 500) < 500
                    else "failed",
                )
                raise
            await record(request, "allowed")
            return response

        async def mapped(request: Request) -> Response:
            try:
                return await guarded(request)
            except MISSING:
                raise HTTPException(404, "creator record was not found in this view") from None
            except CONFLICT:
                raise HTTPException(
                    409, "content changed or is not ready; reload its revision"
                ) from None
            except content_errors.ContentRetrievalUnavailableError:
                raise HTTPException(503, "content retrieval is temporarily unavailable") from None
            except content_errors.ContentError:
                raise HTTPException(
                    422, "content lifecycle or safety checks refused the operation"
                ) from None

        return mapped
