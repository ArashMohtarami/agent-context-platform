"""One explicit managed resource gate per served collection, including search.

Research needs its research boundary AND every linked project; unlinked research
also needs organization-knowledge. Content needs its profile AND inherited source
boundaries. These intersections never borrow a selector from another action.
"""

from collections.abc import Mapping
from typing import Any

from app.security.tenant_policy import TenantPolicy

RESOURCE_PATHS = {
    "projects": ("project_directory", "project_id"),
    "project_context": ("project", "project_id"),
    "conversations": ("project", "project_id"),
    "project_bindings": ("project", "project_id"),
    "agent_sessions": ("project", "project_id"),
    "memories": ("memory_dimensions", "scope"),
    "skills": ("skill", "skill_id"),
    "skill_chunks": ("skill", "skill_id"),
    "creator_profiles": ("creator_profile", "profile_id"),
    **dict.fromkeys(
        (
            "content_briefs",
            "content_campaigns",
            "content_ideas",
            "content_items",
            "content_links",
            "content_metrics",
            "content_pillars",
            "content_versions",
        ),
        ("creator_profile", "profile_id"),
    ),
    **dict.fromkeys(
        (
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
        ),
        ("research_project", "research_project_id"),
    ),
    "agent_clients": ("client", "client_id"),
    "api_keys": ("client", "client_id"),
    "client_profiles": ("organization", "org_id"),
    "agent_activity": ("client", "client_id"),
    "audit_logs": ("organization", "org_id"),
    "retrieval_logs": ("organization", "org_id"),
    "gateway_idempotency": ("client", "client_id"),
}


def permitted(policy: TenantPolicy, action: str, kind: str, identifier: Any) -> bool:
    ids = policy.selector(action, kind)
    return isinstance(identifier, str) and (ids is None or identifier in ids)


def field_filter(policy: TenantPolicy, action: str, kind: str, field: str) -> dict[str, Any]:
    ids = policy.selector(action, kind)
    # all_in_org still requires a real, non-null resource dimension.
    return {field: {"$type": "string"}} if ids is None else {field: {"$in": list(ids)}}


def _resource_filter(policy: TenantPolicy, action: str, collection: str) -> dict[str, Any]:
    kind, field = RESOURCE_PATHS[collection]  # unclassified additions fail closed
    if policy.subject_kind == "machine" and collection in {"gateway_idempotency", "agent_activity"}:
        # Operational metadata follows this exact key/client/subject policy,
        # without inventing corpus permissions for a client resource dimension.
        allowed = (
            policy.client_id
            if any(g.action == action and g.mode != "none" for g in policy.grants)
            else None
        )
        return {"org_id": policy.org_id, "client_id": allowed or "__no_managed_client__"}
    if collection == "projects" and action not in {"projects:read", "projects:write"}:
        kind = "project"  # reference closure uses the domain action's boundary
    parts: list[dict[str, Any]] = [{"org_id": policy.org_id}]
    projects = policy.selector(action, "project")
    global_allowed = permitted(policy, action, "organization_knowledge", policy.org_id)
    if collection == "memories":
        choices: list[dict[str, Any]] = []
        if global_allowed:
            choices.append({"scope": "global"})
        choices.append(
            {"$and": [{"scope": "project"}, field_filter(policy, action, "project", "project_id")]}
        )
        private = field_filter(policy, action, "client_memory", "client_id")
        choices.append({"$and": [{"scope": "agent"}, private]})
        choices.append(
            {
                "$and": [
                    {"scope": "project_agent"},
                    private,
                    field_filter(policy, action, "project", "project_id"),
                ]
            }
        )
        parts.append({"$or": choices})
    elif kind == "project":
        choices = [field_filter(policy, action, kind, field)]
        if global_allowed and collection != "project_context":
            choices.append({"project_id": None})
        parts.append({"$or": choices})
    else:
        parts.append(field_filter(policy, action, kind, field))
    if kind == "research_project":
        if projects is not None:
            parts.append(
                {"authorization.project_ids": {"$not": {"$elemMatch": {"$nin": list(projects)}}}}
            )
        if not global_allowed:
            parts.append({"authorization.project_ids.0": {"$exists": True}})
    return {"$and": parts}


def _resource_allows(
    policy: TenantPolicy, action: str, row: Mapping[str, Any], collection: str
) -> bool:
    if row.get("org_id") != policy.org_id:
        return False
    kind, field = RESOURCE_PATHS[collection]
    if policy.subject_kind == "machine" and collection in {"gateway_idempotency", "agent_activity"}:
        return (
            policy.client_id is not None
            and row.get("client_id") == policy.client_id
            and any(g.action == action and g.mode != "none" for g in policy.grants)
        )
    if collection == "projects" and action not in {"projects:read", "projects:write"}:
        kind = "project"
    global_allowed = permitted(policy, action, "organization_knowledge", policy.org_id)
    if collection == "memories":
        scope = row.get("scope")
        if scope == "global":
            allowed = global_allowed
        elif scope == "project":
            allowed = permitted(policy, action, "project", row.get("project_id"))
        elif scope in {"agent", "project_agent"}:
            allowed = permitted(policy, action, "client_memory", row.get("client_id")) and (
                scope == "agent" or permitted(policy, action, "project", row.get("project_id"))
            )
        else:
            return False
    elif kind == "project" and row.get(field) is None and collection != "project_context":
        allowed = global_allowed
    else:
        allowed = permitted(policy, action, kind, row.get(field))
    if not allowed:
        return False
    projects = row.get("resource_project_ids", row.get("project_ids", []))
    if kind == "research_project":
        projects = row.get("authorization", {}).get("project_ids", [])
        if not projects and not global_allowed:
            return False
    if not all(permitted(policy, action, "project", p) for p in projects):
        return False
    return all(
        permitted(policy, action, "client_memory", c) for c in row.get("resource_client_ids", [])
    )


SOURCE_ACTIONS = {
    "skills": "skills:read",
    "skill_chunks": "skills:read",
    "project_context": "context:read",
    "conversations": "context:read",
    "memories": "memory:read",
    "creator_profiles": "creator:read",
    **dict.fromkeys(
        (name for name in RESOURCE_PATHS if name.startswith("content_")), "content:read"
    ),
    **dict.fromkeys(
        (name for name, (kind, _) in RESOURCE_PATHS.items() if kind == "research_project"),
        "research:read",
    ),
}


def required_actions(action: str, collection: str) -> tuple[str, ...]:
    if action == "search:read" and collection in SOURCE_ACTIONS:
        return (action, SOURCE_ACTIONS[collection])
    if action == "research:promote" and collection in {"project_context", "memories"}:
        return (action, "context:write" if collection == "project_context" else "memory:write")
    return (action,)


def resource_filter(policy: TenantPolicy, action: str, collection: str) -> dict[str, Any]:
    filters = [
        _resource_filter(policy, a, collection) for a in required_actions(action, collection)
    ]
    return filters[0] if len(filters) == 1 else {"$and": filters}


def resource_allows(
    policy: TenantPolicy, action: str, row: Mapping[str, Any], collection: str
) -> bool:
    return all(
        _resource_allows(policy, a, row, collection) for a in required_actions(action, collection)
    )
