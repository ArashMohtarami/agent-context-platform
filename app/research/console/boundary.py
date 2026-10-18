"""Tenant/action boundary and safe error classes for the evidence console."""

from collections.abc import Callable, Coroutine
from typing import Any

from fastapi import HTTPException, Request, Response

from app.knowledge.boundary import KnowledgeRoute, resource
from app.research import errors
from app.security.job_authority import JobAuthorityError
from app.security.policy.evaluation import PolicyDenied
from app.security.tenant_policy import current_action, current_policy
from app.services.research_ingestion_service import (
    ResearchIngestionCapacityError,
    ResearchIngestionError,
    ResearchIngestionJobNotFoundError,
)
from app.services.research_source_service import ResearchSourceLimitError


def exception_types(*suffixes: str) -> tuple[type[Exception], ...]:
    return tuple(
        c
        for name, c in vars(errors).items()
        if isinstance(c, type) and issubclass(c, Exception) and name.endswith(suffixes)
    )


MISSING: tuple[type[Exception], ...] = (
    *exception_types("NotFoundError"),
    ResearchIngestionJobNotFoundError,
)
CONFLICT: tuple[type[Exception], ...] = (
    *exception_types("AlreadyExistsError", "StateError", "VersionMismatchError"),
    errors.RevalidationNotNeededError,
)
PROVENANCE = (
    errors.CandidateProvenanceError,
    errors.ResearchCitationProvenanceError,
    errors.SkillUpdateProposalProvenanceError,
    errors.CandidatePromotionTargetError,
)


def projects() -> tuple[str, ...] | None:
    policy = current_policy()
    if policy is None:
        raise PolicyDenied("verified human policy is required")
    return policy.selector(current_action() or "", "project")


def research_resource(research_project_id: str) -> None:
    resource(current_action() or "", "research_project", research_project_id)


class ResearchRoute(KnowledgeRoute):
    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        handler = super().get_route_handler()

        async def guarded(request: Request) -> Response:
            try:
                return await handler(request)
            except MISSING:
                raise HTTPException(404, "research record was not found in this view") from None
            except CONFLICT:
                raise HTTPException(
                    409, "research record changed; reload its current revision"
                ) from None
            except (ResearchIngestionCapacityError, ResearchSourceLimitError):
                raise HTTPException(429, "research capacity is temporarily unavailable") from None
            except (*PROVENANCE, ResearchIngestionError):
                raise HTTPException(
                    422, "source input or evidence provenance was not accepted"
                ) from None
            except JobAuthorityError:
                raise HTTPException(403, "job authority is unavailable") from None

        return guarded
