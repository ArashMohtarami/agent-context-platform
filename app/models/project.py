"""Project persistence model."""

from pydantic import Field, model_validator

from app.models.common import (
    Identifier,
    ShortText,
    TaggedDocument,
    populate_deterministic_id,
)


class ProjectDocument(TaggedDocument):
    """A software repository or system boundary."""

    project_id: Identifier
    name: ShortText
    description: str = Field(min_length=1, max_length=4_000)
    repository: str = Field(min_length=1, max_length=2_048)
    environment: Identifier

    @model_validator(mode="before")
    @classmethod
    def assign_id(cls, value: object) -> object:
        """Derive `_id` from the public project identifier."""
        return populate_deterministic_id(value, namespace="project", fields=("project_id",))

    @model_validator(mode="after")
    def validate_id(self) -> "ProjectDocument":
        """Prevent project identity drift."""
        if self.id != self.identity_id("project", self.project_id):
            raise ValueError("_id does not match project_id")
        return self
