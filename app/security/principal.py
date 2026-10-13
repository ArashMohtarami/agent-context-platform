"""Who is calling, and what they are allowed to reach.

Every authorization decision in the platform is a question asked of one of
these. The rules are stated once here rather than at each endpoint, so an
endpoint cannot forget half of one.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final

from app.security.scopes import ALL_SCOPES, ANY_PROJECT, Scope, canonical

ANY_PROFILE: Final = None

# The name a caller is known by in a log when no key was required. It is never
# a key identifier, so it cannot be mistaken for one.
UNAUTHENTICATED: Final = "unauthenticated"


class AuthenticationError(Exception):
    """The caller did not prove who they are."""


class AuthorizationError(Exception):
    """The caller proved who they are and may not do this.

    The message is written to be returned: it names the missing scope, because
    that tells a legitimate caller what to ask for, and never names a project
    or creator profile it was refused, because that would confirm it exists.
    """


@dataclass(frozen=True, slots=True)
class Principal:
    """One authenticated caller.

    `projects` and `profiles` are allow-lists: `None` means every resource of
    that kind, and an explicit tuple means exactly those.  An empty project
    tuple is the safe result when a client restriction no longer overlaps one
    of its already-issued credentials; global knowledge remains reachable.
    """

    key_id: str
    name: str
    scopes: tuple[Scope, ...]
    projects: tuple[str, ...] | None = ANY_PROJECT
    profiles: tuple[str, ...] | None = ANY_PROFILE
    client_id: str | None = None
    client_type: str | None = None
    authenticated: bool = True

    def __post_init__(self) -> None:
        """Reject a principal that could not be enforced as written."""
        if not self.key_id or not self.name:
            raise ValueError("a principal requires an identifier and a name")
        if len(set(self.scopes)) != len(self.scopes):
            raise ValueError("granted scopes must be unique")
        if self.projects is not None:
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
        which belongs to every caller. It is never read as "any project" —
        that would infer an identity from an absence, and would turn an
        omitted argument into a wider grant than the caller holds.
        """
        if project_id is None:
            return True
        return self.projects is None or project_id in self.projects

    def allows_profile(self, profile_id: str) -> bool:
        """Report whether this caller may reach one creator profile."""
        return self.profiles is None or profile_id in self.profiles

    def require_scope(self, scope: Scope) -> None:
        """Refuse an operation this grant does not cover."""
        if not self.allows_scope(scope):
            raise AuthorizationError(f"this key is not authorized for {scope}")

    def require_project(self, project_id: str | None) -> None:
        """Refuse a project this grant does not cover."""
        if not self.allows_project(project_id):
            # Deliberately not named: telling a caller which project it was
            # refused confirms that the project exists.
            raise AuthorizationError("this key is not authorized for that project")

    def require_profile(self, profile_id: str) -> None:
        """Refuse a creator profile outside this caller's explicit grant."""
        if not self.allows_profile(profile_id):
            raise AuthorizationError("this key is not authorized for that creator profile")

    def describe(self) -> dict[str, object]:
        """Return a credential-free summary, safe to log."""
        description: dict[str, object] = {
            "key_id": self.key_id,
            "scopes": list(self.scopes),
            "projects": "all" if self.projects is None else list(self.projects),
            "profiles": "all" if self.profiles is None else list(self.profiles),
        }
        if self.client_id is not None:
            description["client_id"] = self.client_id
        return description


def unauthenticated_principal(scopes: Sequence[Scope] | None = None) -> Principal:
    """Build the caller a deployment runs as when authentication is off.

    It holds everything, which is the point: an open deployment is open, and
    saying so in one place is better than scattering "if auth is disabled"
    through the endpoints where one of them would eventually be missed.
    """
    return Principal(
        key_id=UNAUTHENTICATED,
        name=UNAUTHENTICATED,
        scopes=canonical(scopes if scopes is not None else ALL_SCOPES),
        projects=ANY_PROJECT,
        profiles=ANY_PROFILE,
        authenticated=False,
    )
