"""Retrieval-debug log persistence model."""

from typing import Literal

from pydantic import Field, field_validator, model_validator

from app.models.agent import AgentClientType
from app.models.common import ContentText, Identifier, MongoDocumentModel

RetrievalMethod = Literal["lexical", "vector", "hybrid"]


class RetrievalLogDocument(MongoDocumentModel):
    """A bounded record of retrieval metadata, without retrieved document bodies."""

    query: ContentText
    project_id: Identifier | None = None
    requested_sources: list[Identifier] = Field(min_length=1, max_length=10)
    result_count: int = Field(ge=0)
    selected_result_ids: list[str] = Field(default_factory=list, max_length=50)
    latency_ms: float = Field(ge=0.0)
    retrieval_method: RetrievalMethod
    client_id: Identifier | None = None
    client_type: AgentClientType | None = None
    session_id: Identifier | None = None

    @field_validator("requested_sources", "selected_result_ids")
    @classmethod
    def unique_values(cls, value: list[str]) -> list[str]:
        """Reject duplicated source or result identifiers."""
        if len(value) != len(set(value)):
            raise ValueError("list values must be unique")
        return value

    @model_validator(mode="after")
    def validate_result_count(self) -> "RetrievalLogDocument":
        """Selected results cannot exceed the total result count."""
        if len(self.selected_result_ids) > self.result_count:
            raise ValueError("selected_result_ids cannot exceed result_count")
        return self
