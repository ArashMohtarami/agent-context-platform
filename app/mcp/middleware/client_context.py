"""Per-call client context carried through the MCP gateway."""

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass

from app.models.agent import AgentClientType
from app.models.common import validate_identifier
from app.security.scopes import Scope, parse_scopes

_current: ContextVar["ClientRequestContext | None"] = ContextVar("mcp_client_context", default=None)


@dataclass(frozen=True, slots=True)
class ClientRequestContext:
    """Credential-free identity and authorization facts for one MCP call.

    ``client_id`` is absent for a V1--V3 legacy API key.  That is intentional:
    legacy credentials remain usable, while only client-linked credentials gain
    agent activity provenance.
    """

    org_id: str
    key_id: str
    allowed_profile_ids: tuple[str, ...] | None
    request_id: str
    client_id: str | None
    client_type: AgentClientType | None
    scopes: tuple[Scope, ...]
    allowed_project_ids: tuple[str, ...] | None
    session_id: str | None = None

    def __post_init__(self) -> None:
        validate_identifier(self.org_id)
        validate_identifier(self.key_id)
        validate_identifier(self.request_id)
        if self.client_id is not None:
            validate_identifier(self.client_id)
        if self.session_id is not None:
            validate_identifier(self.session_id)
        if len(set(self.scopes)) != len(self.scopes):
            raise ValueError("gateway scopes must be unique")
        if self.allowed_profile_ids is not None:
            if len(set(self.allowed_profile_ids)) != len(self.allowed_profile_ids):
                raise ValueError("gateway profile identifiers must be unique")
            for profile_id in self.allowed_profile_ids:
                validate_identifier(profile_id)
        if self.allowed_project_ids is not None:
            if len(set(self.allowed_project_ids)) != len(self.allowed_project_ids):
                raise ValueError("gateway allowed project identifiers must be unique")
            for project_id in self.allowed_project_ids:
                validate_identifier(project_id)

    @classmethod
    def from_identity(
        cls,
        identity: dict[str, object],
        *,
        request_id: str,
        session_id: str | None = None,
    ) -> "ClientRequestContext":
        """Build a checked gateway context from the API's narrow identity reply."""
        org_id = identity.get("org_id")
        key_id = identity.get("key_id")
        if not isinstance(org_id, str) or not isinstance(key_id, str):
            raise ValueError("gateway identity requires a trusted organization and key")
        if "allowed_profile_ids" not in identity:
            raise ValueError("gateway profile selector is missing")
        raw_profiles = identity["allowed_profile_ids"]
        if raw_profiles is not None and (
            not isinstance(raw_profiles, list) or not all(isinstance(v, str) for v in raw_profiles)
        ):
            raise ValueError("gateway profile selector is invalid")
        profiles = None if raw_profiles is None else tuple(raw_profiles)
        raw_client_id = identity.get("client_id")
        client_id = raw_client_id if isinstance(raw_client_id, str) else None
        raw_client_type = identity.get("client_type")
        client_type = AgentClientType(raw_client_type) if isinstance(raw_client_type, str) else None
        if (client_id is None) != (client_type is None):
            raise ValueError("gateway identity returned incomplete client identity")
        raw_scopes = identity.get("scopes")
        if not isinstance(raw_scopes, list) or not all(
            isinstance(value, str) for value in raw_scopes
        ):
            raise ValueError("gateway identity returned invalid scopes")
        if "allowed_project_ids" not in identity:
            raise ValueError("gateway project selector is missing")
        raw_projects = identity["allowed_project_ids"]
        if raw_projects is None:
            projects = None
        elif isinstance(raw_projects, list) and all(
            isinstance(value, str) for value in raw_projects
        ):
            projects = tuple(raw_projects)
        else:
            raise ValueError("gateway identity returned invalid project grants")
        return cls(
            org_id=org_id,
            key_id=key_id,
            allowed_profile_ids=profiles,
            request_id=request_id,
            client_id=client_id,
            client_type=client_type,
            scopes=parse_scopes(raw_scopes),
            allowed_project_ids=projects,
            session_id=session_id,
        )

    def allows_scope(self, scope: Scope) -> bool:
        """Return whether the authenticated credential grants one capability."""
        return scope in self.scopes

    def allows_project(self, project_id: str | None) -> bool:
        """Global knowledge remains reachable; named projects obey the allow-list."""
        return (
            project_id is None
            or self.allowed_project_ids is None
            or project_id in self.allowed_project_ids
        )

    def headers(self) -> dict[str, str]:
        """Return safe provenance headers for API requests made during this call."""
        headers: dict[str, str] = {"X-Agent-Context-Request-ID": self.request_id}
        if self.client_id is not None:
            headers["X-Agent-Context-Client-ID"] = self.client_id
        if self.client_type is not None:
            headers["X-Agent-Context-Client-Type"] = self.client_type.value
        if self.session_id is not None:
            headers["X-Agent-Context-Session-ID"] = self.session_id
        return headers


@contextmanager
def bind_client_context(context: ClientRequestContext) -> Iterator[None]:
    """Make one gateway context available to the API client for this call only."""
    token: Token[ClientRequestContext | None] = _current.set(context)
    try:
        from app.security.tenant import TenantContext, tenant_scope

        with tenant_scope(TenantContext(context.org_id)):
            yield
    finally:
        _current.reset(token)


def current_client_context() -> ClientRequestContext | None:
    """Return the bound context, if this request entered through the gateway."""
    return _current.get()
