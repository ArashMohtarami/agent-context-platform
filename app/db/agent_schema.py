"""Strict validators for V4 Phase 0 agent-identity collections.

This module is intentionally independent from gateway behaviour.  It owns the
durable contracts only; authentication, authorization, sessions, resolution,
and audit policy remain in their explicitly later V4 phases.
"""

from typing import Final

from app.db.client_types import MongoDocument
from app.models.agent import (
    AgentActivityStatus,
    AgentClientStatus,
    AgentClientType,
    AgentSessionStatus,
    ProjectBindingType,
)
from app.security.scopes import ALL_SCOPES

IDENTIFIER: Final[MongoDocument] = {
    "bsonType": "string",
    "minLength": 1,
    "maxLength": 128,
    "pattern": "^[A-Za-z0-9][A-Za-z0-9._:-]*$",
}
DATE: Final[MongoDocument] = {"bsonType": "date"}
METADATA: Final[MongoDocument] = {"bsonType": "object"}
OPTIONAL_IDENTIFIER: Final[MongoDocument] = {
    "bsonType": ["string", "null"],
    "minLength": 1,
    "maxLength": 128,
    "pattern": "^[A-Za-z0-9][A-Za-z0-9._:-]*$",
}


def agent_client_validator_without_profile_id() -> MongoDocument:
    """Return the one-time migration validator before profile backfill.

    Existing strict V4 client documents cannot accept a new property until the
    validator first permits it.  V5 migration 33 uses this transitional shape,
    backfills ``profile_id: null``, then applies the final required contract.
    """
    validator = document_schema(
        [
            "client_id",
            "client_type",
            "name",
            "status",
            "owner_id",
            "scopes",
            "project_ids",
            "metadata",
            "last_seen_at",
        ],
        _agent_client_properties(),
    )
    return validator


def _agent_client_properties() -> MongoDocument:
    """Keep final and transition validators aligned on allowed properties."""
    return {
        "client_id": IDENTIFIER,
        "client_type": {"enum": [value.value for value in AgentClientType]},
        "name": {"bsonType": "string", "minLength": 1, "maxLength": 500},
        "status": {"enum": [value.value for value in AgentClientStatus]},
        "owner_id": IDENTIFIER,
        "profile_id": OPTIONAL_IDENTIFIER,
        "scopes": {
            "bsonType": "array",
            "maxItems": 32,
            "uniqueItems": True,
            "items": {"enum": list(ALL_SCOPES)},
        },
        "project_ids": {
            "bsonType": "array",
            "maxItems": 100,
            "uniqueItems": True,
            "items": IDENTIFIER,
        },
        "metadata": METADATA,
        "last_seen_at": {"bsonType": ["date", "null"]},
    }


def document_schema(required_fields: list[str], properties: MongoDocument) -> MongoDocument:
    """Add the shared strict and revisioned document fields."""
    return {
        "$jsonSchema": {
            "bsonType": "object",
            "additionalProperties": False,
            "required": ["_id", "created_at", "updated_at", "revision", *required_fields],
            "properties": {
                "_id": {"bsonType": "objectId"},
                "created_at": DATE,
                "updated_at": DATE,
                "revision": {"bsonType": ["int", "long"], "minimum": 1},
                **properties,
            },
        }
    }


AGENT_COLLECTION_VALIDATORS: Final[dict[str, MongoDocument]] = {
    "agent_clients": document_schema(
        [
            "client_id",
            "client_type",
            "name",
            "status",
            "owner_id",
            "profile_id",
            "scopes",
            "project_ids",
            "metadata",
            "last_seen_at",
        ],
        _agent_client_properties(),
    ),
    "agent_sessions": document_schema(
        [
            "session_id",
            "client_id",
            "client_type",
            "project_id",
            "repository",
            "started_at",
            "last_seen_at",
            "ended_at",
            "status",
            "metadata",
        ],
        {
            "session_id": IDENTIFIER,
            "client_id": IDENTIFIER,
            "client_type": {"enum": [value.value for value in AgentClientType]},
            "project_id": OPTIONAL_IDENTIFIER,
            "repository": {"bsonType": ["string", "null"], "minLength": 1, "maxLength": 2_048},
            "started_at": DATE,
            "last_seen_at": DATE,
            "ended_at": {"bsonType": ["date", "null"]},
            "status": {"enum": [value.value for value in AgentSessionStatus]},
            "metadata": METADATA,
        },
    ),
    "project_bindings": document_schema(
        ["binding_id", "project_id", "binding_type", "value", "repository_locator"],
        {
            "binding_id": IDENTIFIER,
            "project_id": IDENTIFIER,
            "binding_type": {"enum": [value.value for value in ProjectBindingType]},
            "value": {"bsonType": "string", "minLength": 1, "maxLength": 2_048},
            "repository_locator": {
                "bsonType": ["string", "null"],
                "minLength": 1,
                "maxLength": 2_048,
            },
        },
    ),
    "agent_activity": document_schema(
        [
            "event_id",
            "client_id",
            "client_type",
            "session_id",
            "operation",
            "tool",
            "project_id",
            "resource_type",
            "resource_id",
            "status",
            "latency_ms",
        ],
        {
            "event_id": IDENTIFIER,
            "client_id": IDENTIFIER,
            "client_type": {"enum": [value.value for value in AgentClientType]},
            "session_id": OPTIONAL_IDENTIFIER,
            "operation": IDENTIFIER,
            "tool": OPTIONAL_IDENTIFIER,
            "project_id": OPTIONAL_IDENTIFIER,
            "resource_type": OPTIONAL_IDENTIFIER,
            "resource_id": OPTIONAL_IDENTIFIER,
            "status": {"enum": [value.value for value in AgentActivityStatus]},
            "latency_ms": {"bsonType": ["double", "null"], "minimum": 0.0},
        },
    ),
}
