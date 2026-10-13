"""Standard MongoDB indexes for V4 Phase 0 identity metadata."""

from typing import Final

from pymongo import ASCENDING, DESCENDING, IndexModel

PROJECT_BINDING_MAPPING_UNIQUE_INDEX: Final = IndexModel(
    [("binding_type", ASCENDING), ("value", ASCENDING)],
    unique=True,
    name="project_binding_mapping_uq",
)
PROJECT_BINDING_REPOSITORY_LOCATOR_UNIQUE_INDEX: Final = IndexModel(
    [("repository_locator", ASCENDING)],
    unique=True,
    partialFilterExpression={"repository_locator": {"$type": "string"}},
    name="project_binding_repository_locator_uq",
)

AGENT_STANDARD_INDEX_DEFINITIONS: Final[dict[str, tuple[IndexModel, ...]]] = {
    "agent_clients": (
        IndexModel([("client_id", ASCENDING)], unique=True, name="agent_client_id_uq"),
        IndexModel(
            [("owner_id", ASCENDING), ("status", ASCENDING), ("updated_at", DESCENDING)],
            name="agent_client_owner_status_updated",
        ),
        IndexModel(
            [("status", ASCENDING), ("last_seen_at", DESCENDING)],
            name="agent_client_status_last_seen",
        ),
    ),
    "agent_sessions": (
        IndexModel([("session_id", ASCENDING)], unique=True, name="agent_session_id_uq"),
        IndexModel(
            [("client_id", ASCENDING), ("status", ASCENDING), ("last_seen_at", DESCENDING)],
            name="agent_session_client_status_last_seen",
        ),
        IndexModel(
            [("project_id", ASCENDING), ("status", ASCENDING), ("last_seen_at", DESCENDING)],
            name="agent_session_project_status_last_seen",
        ),
    ),
    "project_bindings": (
        IndexModel([("binding_id", ASCENDING)], unique=True, name="project_binding_id_uq"),
        IndexModel(
            [("binding_type", ASCENDING), ("value", ASCENDING)],
            name="project_binding_lookup",
        ),
        # A repository identifier can belong to one platform project only.
        # A duplicate mapping would make later project resolution ambiguous.
        PROJECT_BINDING_MAPPING_UNIQUE_INDEX,
        # SSH and HTTPS are alternative representations of one repository,
        # so they must not race into different projects under separate types.
        PROJECT_BINDING_REPOSITORY_LOCATOR_UNIQUE_INDEX,
        IndexModel(
            [("project_id", ASCENDING), ("binding_type", ASCENDING)],
            name="project_binding_project_type",
        ),
    ),
    "agent_activity": (
        IndexModel([("event_id", ASCENDING)], unique=True, name="agent_activity_event_id_uq"),
        IndexModel(
            [("client_id", ASCENDING), ("created_at", DESCENDING)],
            name="agent_activity_client_created",
        ),
        IndexModel(
            [("session_id", ASCENDING), ("created_at", DESCENDING)],
            name="agent_activity_session_created",
        ),
        IndexModel(
            [("project_id", ASCENDING), ("created_at", DESCENDING)],
            name="agent_activity_project_created",
        ),
        IndexModel(
            [("tool", ASCENDING), ("status", ASCENDING), ("created_at", DESCENDING)],
            name="agent_activity_tool_status_created",
        ),
    ),
}
