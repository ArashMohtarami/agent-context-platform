"""Reviewed policy and issuance are separate bounded commands."""

from typing import Literal

from pydantic import AwareDatetime, Field, field_validator, model_validator

from app.identity.models import Input
from app.models.agent import AgentClientType
from app.models.common import Identifier
from app.security.policy.models import Grant
from app.security.scopes import ALL_SCOPES


class ClientApproval(Input):
    client_id: Identifier
    name: str = Field(min_length=1, max_length=500)
    client_type: AgentClientType = AgentClientType.AUTOMATION
    expected_revision: int = Field(default=0, ge=0)
    subject_kind: Literal["human", "service_account"] = "human"
    subject_id: Identifier
    parent_id: Identifier
    expected_parent_generation: int = Field(ge=1)
    team_id: Identifier | None = None
    expected_team_generation: int | None = Field(default=None, ge=1)
    tuples: tuple[Grant, ...] = Field(default=(), max_length=100)
    expires_at: AwareDatetime
    profile_id: Identifier | None = None
    expected_profile_revision: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def bounded(self) -> "ClientApproval":
        if (self.team_id is None) != (self.expected_team_generation is None):
            raise ValueError("team requires its reviewed generation")
        if (self.profile_id is None) != (self.expected_profile_revision is None):
            raise ValueError("template assignment requires its exact revision")
        if any(
            g.action not in {*ALL_SCOPES, "reviews:decide"}
            or g.action in {"agent:manage", "system:read"}
            for g in self.tuples
        ):
            raise ValueError("machines cannot administer platform authority")
        return self


class CredentialIssue(Input):
    issuance_id: Identifier
    expected_client_revision: int = Field(ge=1)
    tuples: tuple[Grant, ...] = Field(default=(), max_length=100)
    expires_at: AwareDatetime
    replaces_key_id: str | None = Field(default=None, pattern=r"^[0-9a-f]{16}$")


class ServiceCreate(Input):
    service_account_id: Identifier
    name: str = Field(min_length=1, max_length=500)
    custodian_id: Identifier
    team_id: Identifier
    expected_team_generation: int = Field(ge=1)
    expires_at: AwareDatetime


class ReviewerApproval(Input):
    expected_revision: int = Field(ge=1)
    parent_id: Identifier
    expected_parent_generation: int = Field(ge=1)
    tuples: tuple[Grant, ...] = Field(min_length=1, max_length=100)
    expires_at: AwareDatetime


class Revision(Input):
    expected_revision: int = Field(ge=1)


class TemplatePut(Input):
    expected_revision: int = Field(ge=0)
    name: str = Field(min_length=1, max_length=500)
    scopes: tuple[str, ...] = Field(min_length=1, max_length=32)
    status: Literal["active", "disabled"] = "active"
    rate_limit_profile: Identifier | None = None

    @field_validator("scopes")
    @classmethod
    def known_scopes(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        from app.models.client_profile import ClientProfileDocument

        ClientProfileDocument.validate_scopes(list(value))
        return value


class LegacyClaim(Input):
    key_id: str = Field(pattern=r"^[0-9a-f]{16}$")
    client_id: Identifier
    expected_client_revision: int = Field(ge=1)
    tuples: tuple[Grant, ...] = Field(min_length=1, max_length=100)
    expires_at: AwareDatetime
