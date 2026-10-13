"""Authorization for MCP tool calls.

Two questions are asked before any tool runs: may this caller perform this kind
of operation, and may it reach the named project or creator profile. Both are
answered here, from a grant the server was configured with, before a request is
built — so a refused call never reaches the Context API at all.

The scopes are the platform's own, defined once in `app.security.scopes` and
enforced again by the API. This layer refuses early so a call the key could not
perform never leaves the process; it does not replace the API's check, and
cannot, because a caller could always reach the API directly.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final

from app.security.scopes import (
    ALL_SCOPES,
    SCOPE_DESCRIPTIONS,
    SCOPE_SET,
    Scope,
    parse_scopes,
)

__all__ = [
    "ALL_SCOPES",
    "ANY_PROFILE",
    "ANY_PROJECT",
    "SCOPE_DESCRIPTIONS",
    "Authorization",
    "AuthorizationError",
    "Scope",
    "build_authorization",
    "parse_scopes",
]

# A grant that names no project reaches every project. Narrowing it is how a
# deployment hands one agent one project without changing any tool.
ANY_PROJECT: Final = None
ANY_PROFILE: Final = None


class AuthorizationError(Exception):
    """Raised when a caller may not perform the operation it asked for.

    The message is written to be shown to the caller: it names the missing
    scope or the refused resource and nothing else about the deployment.
    """


@dataclass(frozen=True, slots=True)
class Authorization:
    """What one MCP caller is allowed to do.

    `projects` and `profiles` are allow-lists: `None` means every resource of
    that kind, and an explicit tuple means exactly those. An empty tuple would
    mean no resource at all, which is a grant nobody intends, so it is refused.
    """

    scopes: tuple[Scope, ...] = ALL_SCOPES
    projects: tuple[str, ...] | None = ANY_PROJECT
    profiles: tuple[str, ...] | None = ANY_PROFILE

    def __post_init__(self) -> None:
        """Reject a grant that could not be enforced as written."""
        if not self.scopes:
            raise ValueError("an authorization must grant at least one scope")
        if len(set(self.scopes)) != len(self.scopes):
            raise ValueError("granted scopes must be unique")
        for scope in self.scopes:
            if scope not in SCOPE_SET:
                raise ValueError(f"unknown MCP scope: {scope!r}")
        if self.projects is not None:
            if not self.projects:
                raise ValueError("a project allow-list must name at least one project")
            if len(set(self.projects)) != len(self.projects):
                raise ValueError("allowed projects must be unique")
        if self.profiles is not None:
            if not self.profiles:
                raise ValueError("a profile allow-list must name at least one profile")
            if len(set(self.profiles)) != len(self.profiles):
                raise ValueError("allowed profiles must be unique")

    def allows_scope(self, scope: Scope) -> bool:
        """Report whether this caller may perform one kind of operation."""
        return scope in self.scopes

    def allows_project(self, project_id: str | None) -> bool:
        """Report whether this caller may reach one project.

        Naming no project is always allowed: it addresses global knowledge,
        which belongs to everyone. It is never read as "any project" — that
        would be inferring a project identity from its absence.
        """
        if project_id is None:
            return True
        return self.projects is None or project_id in self.projects

    def allows_profile(self, profile_id: str | None) -> bool:
        """Report whether this MCP process may reach one creator profile."""
        if profile_id is None:
            return True
        return self.profiles is None or profile_id in self.profiles

    def require_scope(self, scope: Scope) -> None:
        """Refuse a call the grant does not cover."""
        if not self.allows_scope(scope):
            raise AuthorizationError(f"this caller is not authorized for {scope}")

    def require_project(self, project_id: str | None) -> None:
        """Refuse a call that names a project the grant does not cover."""
        if not self.allows_project(project_id):
            raise AuthorizationError("this caller is not authorized for that project")

    def require_profile(self, profile_id: str | None) -> None:
        """Refuse a tool call that names a profile outside its grant."""
        if not self.allows_profile(profile_id):
            raise AuthorizationError("this caller is not authorized for that creator profile")

    def describe(self) -> dict[str, object]:
        """Return a credential-free summary, safe to log or return to a caller."""
        return {
            "scopes": list(self.scopes),
            "projects": "all" if self.projects is None else list(self.projects),
            "profiles": "all" if self.profiles is None else list(self.profiles),
        }


def build_authorization(
    scopes: Sequence[str] | None = None,
    projects: Sequence[str] | None = None,
    profiles: Sequence[str] | None = None,
) -> Authorization:
    """Build the grant this server runs under.

    Configuring neither grants everything, which is the documented local case:
    a stdio server is reachable only by the process that spawned it. Narrowing
    is a deployment decision, and both halves narrow independently.
    """
    return Authorization(
        scopes=ALL_SCOPES if scopes is None else parse_scopes(scopes),
        projects=ANY_PROJECT if projects is None else tuple(projects),
        profiles=ANY_PROFILE if profiles is None else tuple(profiles),
    )
