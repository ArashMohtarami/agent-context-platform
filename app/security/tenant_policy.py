"""Server-derived action/resource envelopes for the existing bearer interface.

Legacy key scopes remain the admitted V1-V5 contract. They are converted to
complete action/resource tuples within ONE immutable organization. This is not
the later human membership/delegation policy API.
"""

from collections.abc import Awaitable, Callable, Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from hashlib import sha256
from json import dumps
from typing import Any, Literal, cast

from app.security.tenant import TenantContextRequired, require_tenant

SelectorMode = Literal["none", "include", "all_in_org"]


@dataclass(frozen=True, slots=True)
class ResourceGrant:
    action: str
    resource_kind: str
    mode: SelectorMode
    ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if (self.mode == "include") != bool(self.ids) or len(set(self.ids)) != len(self.ids):
            raise ValueError("include requires unique IDs; other selectors have no IDs")


@dataclass(frozen=True, slots=True)
class TenantPolicy:
    org_id: str
    subject_id: str
    grants: tuple[ResourceGrant, ...]
    client_id: str | None = None
    operator: bool = False
    managed: bool = False
    subject_kind: Literal["legacy", "human", "machine"] = "legacy"
    revision: int = 0
    subject_revision: int = 0
    session_id: str | None = None
    engine: Any = field(default=None, repr=False, compare=False)
    refresh: Callable[[], Awaitable[None]] | None = field(default=None, repr=False, compare=False)
    transaction_check: Callable[[Any], Awaitable[None]] | None = field(
        default=None, repr=False, compare=False
    )

    def selector(self, action: str, kind: str) -> tuple[str, ...] | None:
        if self.operator:
            return None
        grants = [
            grant for grant in self.grants if grant.action == action and grant.resource_kind == kind
        ]
        if any(grant.mode == "all_in_org" for grant in grants):
            return None
        return tuple(
            sorted(
                {
                    identifier
                    for grant in grants
                    if grant.mode == "include"
                    for identifier in grant.ids
                }
            )
        )

    @property
    def fingerprint(self) -> str:
        material = (
            self.org_id,
            self.subject_id,
            self.client_id,
            self.operator,
            self.managed,
            self.subject_kind,
            self.revision,
            self.subject_revision,
            [(g.action, g.resource_kind, g.mode, g.ids) for g in self.grants],
        )
        return sha256(dumps(material, separators=(",", ":")).encode()).hexdigest()


_policy: ContextVar[TenantPolicy | None] = ContextVar("tenant_policy", default=None)
_action: ContextVar[str | None] = ContextVar("tenant_action", default=None)


def current_action() -> str | None:
    return _action.get()


def current_policy() -> TenantPolicy | None:
    return _policy.get()


def policy_of(principal: Any) -> TenantPolicy:
    from app.security.tenant import TenantContext

    org_id = TenantContext(principal.org_id).org_id
    if getattr(principal, "managed_policy", None) is not None:
        if principal.managed_policy.org_id != org_id:
            raise TenantContextRequired("policy and tenant context disagree")
        return cast(TenantPolicy, principal.managed_policy)
    grants = tuple(
        ResourceGrant(
            scope, kind, "all_in_org" if ids is None else "include" if ids else "none", ids or ()
        )
        for scope in principal.scopes
        for kind, ids in (("project", principal.projects), ("creator_profile", principal.profiles))
    )
    return TenantPolicy(
        org_id,
        principal.key_id,
        grants,
        principal.client_id,
        revision=getattr(principal, "legacy_policy_revision", 0),
    )


def bind_policy(policy: TenantPolicy) -> None:
    if policy.org_id != require_tenant().org_id:
        raise TenantContextRequired("policy and tenant context disagree")
    _policy.set(policy)


def bind_principal(principal: Any) -> None:
    from app.security.principal import AuthenticationError
    from app.security.tenant import TenantContext, establish_tenant

    if not principal.authenticated or principal.org_id is None:
        raise AuthenticationError("the API key was not accepted")
    establish_tenant(TenantContext(principal.org_id, principal.cursor_secret))
    bind_policy(policy_of(principal))
    # The eventual route scope replaces this for the operation. It permits
    # authenticated, same-resource session attribution during dependency setup.
    if principal.scopes:
        select_action(principal.scopes[0])


@contextmanager
def policy_request_boundary() -> Iterator[None]:
    token = _policy.set(None)
    action_token = _action.set(None)
    try:
        yield
    finally:
        _action.reset(action_token)
        _policy.reset(token)


def select_action(action: str) -> None:
    policy = current_policy()
    if (
        policy is not None
        and not policy.operator
        and not any(
            g.action == action and (not policy.managed or g.mode != "none") for g in policy.grants
        )
    ):
        raise TenantContextRequired("action is absent from the trusted grant")
    _action.set(action)


@contextmanager
def policy_scope(policy: TenantPolicy, action: str | None = None) -> Iterator[TenantPolicy]:
    token = _policy.set(policy)
    action_token = _action.set(action)
    try:
        if policy.org_id != require_tenant().org_id:
            raise TenantContextRequired("policy and tenant context disagree")
        yield policy
    finally:
        _action.reset(action_token)
        _policy.reset(token)


def policy_filter(collection: str) -> dict[str, object]:
    """Resource predicates applied BEFORE selection, limits, counts and joins."""
    tenant = require_tenant()
    policy = current_policy()
    # Explicit operator tenant scopes are trusted OS/maintenance boundaries.
    # HTTP callers always receive a policy derived from a verified bearer.
    if policy is None:
        return {"org_id": tenant.org_id}
    if policy.org_id != tenant.org_id:
        raise TenantContextRequired("policy and tenant context disagree")
    action = _action.get()
    if action is None and not policy.operator:
        raise TenantContextRequired("an action-specific policy is required")
    action = action or "operator"
    parts: list[dict[str, object]] = [{"org_id": tenant.org_id}]
    projects = policy.selector(action, "project")
    profiles = policy.selector(action, "creator_profile")
    if policy.managed:
        from app.security.policy.resources import resource_filter

        return resource_filter(policy, action, collection)
    if projects is not None:
        if collection == "projects":
            parts.append({"project_id": {"$in": list(projects)}})
        elif collection in {
            "project_context",
            "conversations",
            "project_bindings",
            "agent_sessions",
        }:
            parts.append({"$or": [{"project_id": None}, {"project_id": {"$in": list(projects)}}]})
        elif collection == "memories":
            parts.append(
                {
                    "$or": [
                        {"scope": {"$in": ["global", "agent"]}},
                        {"project_id": {"$in": list(projects)}},
                    ]
                }
            )
        elif collection in {
            "research_projects",
            "research_sources",
            "source_snapshots",
            "research_documents",
            "research_chunks",
            "research_notes",
            "research_claims",
            "research_citations",
            "research_briefs",
            "knowledge_candidates",
            "skill_update_proposals",
            "promotion_events",
            "ingestion_jobs",
        }:
            parts.append(
                {"authorization.project_ids": {"$not": {"$elemMatch": {"$nin": list(projects)}}}}
            )
    if (
        action == "agent:manage"
        and collection in {"api_keys", "agent_clients", "client_profiles"}
        and projects is not None
    ):
        field = "projects" if collection == "api_keys" else "project_ids"
        parts.append({field: {"$not": {"$elemMatch": {"$nin": list(projects)}}}})
        # Empty historically meant ALL. A narrowed administrator cannot turn
        # that spelling into broader authority than the managing action holds.
        parts.append(
            {"project_mode": {"$ne": "all_in_org"}}
            if collection != "client_profiles"
            else {field + ".0": {"$exists": True}}
        )
    if action == "agent:manage" and collection == "api_keys" and profiles is not None:
        parts.append({"profiles": {"$not": {"$elemMatch": {"$nin": list(profiles)}}}})
        parts.append({"profile_mode": {"$ne": "all_in_org"}})
    if profiles is not None:
        if collection == "creator_profiles" or collection.startswith("content_"):
            parts.append({"profile_id": {"$in": list(profiles)}})
    if collection == "memories" and not policy.operator:
        parts.append(
            {
                "$or": [
                    {"scope": {"$in": ["global", "project"]}},
                    {"client_id": policy.client_id or "__no_private_client__"},
                ]
            }
        )
    return parts[0] if len(parts) == 1 else {"$and": parts}


def policy_allows(document: Mapping[str, object], collection: str) -> bool:
    """Independent result check, including an index returning a foreign row."""
    if document.get("org_id") != require_tenant().org_id:
        return False
    policy = current_policy()
    if policy is None or policy.operator:
        return True
    action = _action.get()
    if action is None:
        return False
    if policy.managed:
        from app.security.policy.resources import resource_allows

        return resource_allows(policy, action, document, collection)
    projects = policy.selector(action, "project")
    profiles = policy.selector(action, "creator_profile")
    if projects is not None:
        project_id = document.get("project_id")
        if isinstance(project_id, str) and project_id not in projects:
            return False
        resource_projects = document.get("resource_project_ids")
        if isinstance(resource_projects, list | tuple) and not set(resource_projects).issubset(
            projects
        ):
            return False
        authorization = document.get("authorization")
        if isinstance(authorization, Mapping) and not set(
            authorization.get("project_ids", [])
        ).issubset(projects):
            return False
    if action == "agent:manage" and collection in {"api_keys", "agent_clients", "client_profiles"}:
        requested_projects = document.get(
            "projects" if collection == "api_keys" else "project_ids", []
        )
        if projects is not None:
            if document.get("project_mode") == "all_in_org" or (
                collection == "client_profiles" and not requested_projects
            ):
                return False
            if not isinstance(requested_projects, list) or not set(requested_projects).issubset(
                projects
            ):
                return False
        if action == "agent:manage" and collection == "api_keys" and profiles is not None:
            requested_profiles = document.get("profiles", [])
            if (
                document.get("profile_mode") == "all_in_org"
                or not isinstance(requested_profiles, list)
                or not set(requested_profiles).issubset(profiles)
            ):
                return False
    if profiles is not None and (
        collection == "creator_profiles" or collection.startswith("content_")
    ):
        if document.get("profile_id") not in profiles:
            return False
    if collection == "memories" and document.get("scope") in {"agent", "project_agent"}:
        return document.get("client_id") == policy.client_id and policy.client_id is not None
    private_clients = document.get("resource_client_ids")
    if isinstance(private_clients, list | tuple) and any(
        client != policy.client_id for client in private_clients
    ):
        return False
    return True
