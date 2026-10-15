"""Reusable strict MongoDB representation of actor provenance."""

from typing import Final

from app.db.client_types import MongoDocument
from app.models.actor import ActorType
from app.models.agent import AgentClientType

IDENTIFIER: Final[MongoDocument] = {
    "bsonType": ["string", "null"],
    "minLength": 1,
    "maxLength": 128,
    "pattern": "^[A-Za-z0-9][A-Za-z0-9._:-]*$",
}

ACTOR_PROVENANCE: Final[MongoDocument] = {
    "bsonType": "object",
    "additionalProperties": False,
    "required": ["actor_type", "client_id", "client_type"],
    "properties": {
        "actor_type": {"enum": [value.value for value in ActorType]},
        "client_id": IDENTIFIER,
        "user_id": IDENTIFIER,
        "org_id": IDENTIFIER,
        "auth_method": {"enum": [None, "local"]},
        "assurance": {"enum": [None, "password", "mfa"]},
        "client_type": {
            "bsonType": ["string", "null"],
            "enum": [None, *[value.value for value in AgentClientType]],
        },
    },
}
