"""Audit record for anything that touches authorization.

What is recorded is who asked, for what, and what was decided — never what was
returned. An audit trail that copied the knowledge base would double what a
leak exposes while telling an investigator nothing they could not get from the
records themselves.
"""

from typing import Literal

from pydantic import Field, field_validator

from app.models.actor import ActorProvenance, system_actor
from app.models.common import Identifier, MongoDocumentModel, ShortText

AuditOutcome = Literal["allowed", "denied", "failed"]
AuditAction = Literal[
    "authenticate",
    "authorize",
    "key.create",
    "key.revoke",
    "context.write",
    "memory.write",
    "creator.write",
    "content.write",
    "content.approve",
    "content.publish",
    "research.write",
]
MAX_REASON = 200


class AuditLogDocument(MongoDocumentModel):
    """One authorization decision worth being able to reconstruct later."""

    action: AuditAction
    outcome: AuditOutcome
    # The key identifier, never the key. For an unauthenticated deployment this
    # is the literal `unauthenticated`, which no key can ever be called.
    key_id: str = Field(min_length=1, max_length=64)
    key_name: ShortText | None = None
    project_id: Identifier | None = None
    profile_id: Identifier | None = None
    research_project_id: Identifier | None = None
    scope: str | None = Field(default=None, max_length=64)
    method: str | None = Field(default=None, max_length=8)
    path: str | None = Field(default=None, max_length=200)
    request_id: str | None = Field(default=None, max_length=64)
    # Why it was decided that way, in the words the caller was given. Never an
    # exception message, which can carry a connection string.
    reason: str | None = Field(default=None, max_length=MAX_REASON)
    actor: ActorProvenance = Field(default_factory=system_actor)

    @field_validator("path")
    @classmethod
    def validate_path(cls, value: str | None) -> str | None:
        """Keep the query string out of the record.

        A path is a route; a query string is a caller's data, and a search
        query is the one field that can describe the person who ran it.
        """
        if value is not None and "?" in value:
            raise ValueError("an audit path must not carry a query string")
        return value
