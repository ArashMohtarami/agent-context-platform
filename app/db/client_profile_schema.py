"""Strict MongoDB validation for V5's client-neutral onboarding profiles."""

from typing import Final

from app.db.client_types import MongoDocument
from app.models.client_profile import ClientProfileStatus
from app.security.scopes import ALL_SCOPES

IDENTIFIER: Final[MongoDocument] = {
    "bsonType": "string",
    "minLength": 1,
    "maxLength": 128,
    "pattern": "^[A-Za-z0-9][A-Za-z0-9._:-]*$",
}
DATE: Final[MongoDocument] = {"bsonType": "date"}


def client_profile_document_schema() -> MongoDocument:
    """Return the bounded, revisioned profile collection contract."""
    return _client_profile_document_schema(
        required_fields=(
            "profile_id",
            "name",
            "description",
            "scopes",
            "rate_limit_profile",
            "status",
            "metadata",
        )
    )


def client_profile_validator_without_description_and_status() -> MongoDocument:
    """Allow migration 34 to backfill newly required lifecycle fields."""
    return _client_profile_document_schema(
        required_fields=("profile_id", "name", "scopes", "rate_limit_profile", "metadata")
    )


def _client_profile_document_schema(*, required_fields: tuple[str, ...]) -> MongoDocument:
    """Build final and temporary validators from one property definition."""
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
                "profile_id": IDENTIFIER,
                "name": {"bsonType": "string", "minLength": 1, "maxLength": 500},
                "description": {"bsonType": "string", "minLength": 1, "maxLength": 500},
                "scopes": {
                    "bsonType": "array",
                    "minItems": 1,
                    "maxItems": 32,
                    "uniqueItems": True,
                    "items": {"enum": list(ALL_SCOPES)},
                },
                "rate_limit_profile": {**IDENTIFIER, "bsonType": ["string", "null"]},
                "status": {"enum": [value.value for value in ClientProfileStatus]},
                "metadata": {"bsonType": "object"},
            },
        }
    }


CLIENT_PROFILE_COLLECTION = "client_profiles"
CLIENT_PROFILE_COLLECTION_VALIDATORS: Final[dict[str, MongoDocument]] = {
    CLIENT_PROFILE_COLLECTION: client_profile_document_schema(),
}
