"""Skill-version persistence model."""

from typing import Annotated, Literal

from pydantic import AfterValidator, Field, StringConstraints, model_validator

from app.models.common import (
    Identifier,
    MongoDocumentModel,
    ShortText,
    Tags,
    deterministic_object_id,
    populate_deterministic_id,
)


def validate_semantic_version(value: str) -> str:
    """Reject empty identifiers and leading zeroes forbidden by SemVer 2.0."""
    without_build, _, build = value.partition("+")
    _, separator, prerelease = without_build.partition("-")
    if build and any(not identifier for identifier in build.split(".")):
        raise ValueError("semantic version build identifiers must not be empty")
    if separator:
        identifiers = prerelease.split(".")
        if any(not identifier for identifier in identifiers):
            raise ValueError("semantic version prerelease identifiers must not be empty")
        if any(
            identifier.isdigit() and len(identifier) > 1 and identifier.startswith("0")
            for identifier in identifiers
        ):
            raise ValueError("numeric prerelease identifiers must not contain leading zeroes")
    return value


SemanticVersion = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        max_length=64,
        pattern=r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$",
    ),
    AfterValidator(validate_semantic_version),
]
SkillTrustLevel = Literal["trusted", "user", "external", "generated"]
LifecycleStatus = Literal["active", "archived", "superseded"]


class SkillDocument(MongoDocumentModel):
    """One immutable natural-keyed version of a canonical Git skill."""

    skill_id: Identifier
    name: ShortText
    version: SemanticVersion
    description: str = Field(min_length=1, max_length=4_000)
    category: Identifier
    tags: Tags = Field(default_factory=list)
    trust_level: SkillTrustLevel
    status: LifecycleStatus = "active"
    source_path: str = Field(min_length=1, max_length=2_048)
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="before")
    @classmethod
    def assign_id(cls, value: object) -> object:
        """Derive `_id` from skill ID and semantic version."""
        return populate_deterministic_id(
            value,
            namespace="skill",
            fields=("skill_id", "version"),
        )

    @model_validator(mode="after")
    def validate_id(self) -> "SkillDocument":
        """Prevent a natural key from diverging from its stable `_id`."""
        expected = deterministic_object_id("skill", self.skill_id, self.version)
        if self.id != expected:
            raise ValueError("_id does not match skill_id and version")
        return self
