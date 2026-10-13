"""Post-retrieval filtering, including the project isolation rule.

Search backends pre-filter what they can, but the decision about what a caller
is allowed to see is made here, on candidates, where it is enforceable for every
source and testable without a database.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Final

from app.retrieval.types import RetrievalCandidate, SourceType, validate_fraction

MAX_FILTER_TAGS: Final = 50


def visible_to_project(owner: str | None, requested: str | None) -> bool:
    """Return whether project-owned content may be shown to this request.

    Global knowledge is visible to everyone. Project-owned content is visible
    only to the project that owns it, which is what keeps one project's private
    context out of another project's answers. A request naming no project
    therefore sees global knowledge only: project context has to be asked for
    explicitly, never inherited by omission.
    """
    if owner is None:
        return True
    return requested is not None and owner == requested


@dataclass(frozen=True, slots=True)
class RetrievalFilters:
    """What one retrieval request is allowed to return.

    These are applied again after the search backends have run. The repetition
    is deliberate: a backend filter is an optimization, while this is the rule,
    and a result that escapes the first must not escape the second.
    """

    project_id: str | None = None
    memory_client_id: str | None = None
    sources: tuple[SourceType, ...] = ()
    category: str | None = None
    tags: tuple[str, ...] = ()
    trust_level: str | None = None
    minimum_importance: float = 0.0
    require_active: bool = True

    def __post_init__(self) -> None:
        """Reject filters that could not be applied as written."""
        validate_fraction(self.minimum_importance, "minimum_importance")
        if len(self.tags) > MAX_FILTER_TAGS:
            raise ValueError(f"at most {MAX_FILTER_TAGS} tag filters are supported")
        if len(set(self.tags)) != len(self.tags):
            raise ValueError("tag filters must be unique")

    def allows(self, candidate: RetrievalCandidate) -> bool:
        """Report whether one candidate satisfies every requested restriction."""
        if not visible_to_project(candidate.project_id, self.project_id):
            return False
        if candidate.source == "memory" and candidate.client_id is not None:
            # Agent-owned operational memory is never global merely because
            # its project_id is null. The repository pre-filters it, and this
            # repeated check protects against malformed/manual records.
            if candidate.client_id != self.memory_client_id:
                return False
        if self.sources and candidate.source not in self.sources:
            return False
        if self.require_active and not candidate.is_active:
            return False
        if self.category is not None and candidate.category != self.category:
            return False
        if self.trust_level is not None and candidate.trust_level != self.trust_level:
            return False
        if any(tag not in candidate.tags for tag in self.tags):
            return False
        return candidate.importance >= self.minimum_importance

    def apply(self, candidates: Iterable[RetrievalCandidate]) -> tuple[RetrievalCandidate, ...]:
        """Return the candidates this request may see, in their original order."""
        return tuple(candidate for candidate in candidates if self.allows(candidate))
