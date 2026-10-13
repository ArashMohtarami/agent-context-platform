"""Conversation-history persistence model."""

from typing import Literal

from pydantic import Field

from app.models.common import ContentText, Identifier, Metadata, MongoDocumentModel

ConversationRole = Literal["system", "user", "assistant", "tool"]


class ConversationDocument(MongoDocumentModel):
    """One project-bound conversation message, kept separate from memory."""

    project_id: Identifier
    session_id: Identifier
    role: ConversationRole
    content: ContentText
    metadata: Metadata = Field(default_factory=dict)
