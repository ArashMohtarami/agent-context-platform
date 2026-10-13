"""What a caller may do, named once.

One scope per kind of operation rather than one per endpoint, so a grant stays
readable as the surface grows and an operator can tell at a glance what a key
can reach. Every layer reads these same names: the API enforces them per
endpoint, the MCP server checks them before it builds a request, and
`contextctl keys create` writes them onto a key.
"""

from collections.abc import Iterable
from types import MappingProxyType
from typing import Final, Literal

Scope = Literal[
    "skills:read",
    "projects:read",
    "projects:write",
    "context:read",
    "context:write",
    "memory:read",
    "memory:write",
    "search:read",
    "creator:read",
    "creator:write",
    "content:read",
    "content:write",
    "research:read",
    "research:write",
    "research:approve",
    "research:promote",
    "content:publish",
    "agent:self",
    "session:write",
    "audit:self:read",
    "agent:manage",
    "system:read",
]

ALL_SCOPES: Final[tuple[Scope, ...]] = (
    "skills:read",
    "projects:read",
    "projects:write",
    "context:read",
    "context:write",
    "memory:read",
    "memory:write",
    "search:read",
    "creator:read",
    "creator:write",
    "content:read",
    "content:write",
    "research:read",
    "research:write",
    "research:approve",
    "research:promote",
    "content:publish",
    "agent:self",
    "session:write",
    "audit:self:read",
    "agent:manage",
    "system:read",
)
SCOPE_SET: Final = frozenset(ALL_SCOPES)

# The seven the plan requires. Two more are ours: `system:read` for readiness,
# kept separate so a grant for diagnostics carries no knowledge access, and
# `projects:write` because creating an isolation boundary is not something a
# read scope should permit and the alternative would be letting `projects:read`
# do it.
REQUIRED_SCOPES: Final[tuple[Scope, ...]] = (
    "skills:read",
    "context:read",
    "context:write",
    "memory:read",
    "memory:write",
    "search:read",
    "projects:read",
)

# A grant that names no project reaches every project. Narrowing it is how a
# deployment hands one caller one project without changing any endpoint.
ANY_PROJECT: Final = None

SCOPE_DESCRIPTIONS: Final[MappingProxyType[Scope, str]] = MappingProxyType(
    {
        "skills:read": "Read skill metadata and retrieve skill content",
        "projects:read": "Read project boundaries",
        "projects:write": "Create a project boundary",
        "context:read": "Read and search one project's durable context",
        "context:write": "Store durable context in one project",
        "memory:read": "Read and search global and project memory",
        "memory:write": "Store and correct memory",
        "search:read": "Search across every source at once",
        "creator:read": "Read creator profiles and content pillars",
        "creator:write": "Create and update creator profiles and content pillars",
        "content:read": "Read creator content records",
        "content:write": "Create and update creator content records",
        "research:read": "Read research-project metadata",
        "research:write": "Create and update research-project metadata",
        "research:approve": "Approve reviewed research evidence",
        "research:promote": "Promote approved research into durable knowledge",
        "content:publish": "Publish approved creator content",
        "agent:self": "Read this agent client's own gateway metadata",
        "session:write": "Create and update this agent's lightweight sessions",
        "audit:self:read": "Read this agent's own content-free activity history",
        "agent:manage": "Administer agent clients and their credentials",
        "system:read": "Read platform readiness",
    }
)

# Scopes that change stored knowledge. A key holding none of these can only
# read, which is the grant most callers should have.
WRITE_SCOPES: Final = frozenset(
    {
        "projects:write",
        "context:write",
        "memory:write",
        "creator:write",
        "content:write",
        "research:write",
        "research:approve",
        "research:promote",
        "content:publish",
    }
)


def parse_scopes(value: Iterable[str]) -> tuple[Scope, ...]:
    """Validate scope names, rejecting anything unrecognized.

    An unknown scope is refused rather than ignored: silently dropping one
    would quietly widen a grant that was meant to narrow it, and a typo in a
    deployment's configuration would grant more than it read.
    """
    parsed: list[Scope] = []
    for name in value:
        candidate = name.strip()
        if candidate not in SCOPE_SET:
            raise ValueError(f"unknown scope: {name!r}")
        parsed.append(candidate)  # type: ignore[arg-type]
    if len(set(parsed)) != len(parsed):
        raise ValueError("scopes must be unique")
    return tuple(parsed)


def canonical(scopes: Iterable[Scope]) -> tuple[Scope, ...]:
    """Order scopes the same way everywhere, so two equal grants look equal."""
    held = set(scopes)
    return tuple(scope for scope in ALL_SCOPES if scope in held)
