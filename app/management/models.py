"""Bounded commands. Neither role names nor submitted org/team IDs convey authority."""

from datetime import datetime
from typing import Literal, Self

from pydantic import Field, model_validator

from app.identity.models import EmailInput, Input
from app.models.common import Identifier
from app.security.policy.models import Grant, Issue, Selector


class Revision(Input):
    expected_revision: int = Field(ge=1)


class TeamCreate(Input):
    team_id: Identifier
    name: str = Field(min_length=1, max_length=128)


class TeamChange(Revision):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    status: Literal["active", "suspended"] | None = None


class TeamMemberChange(Input):
    expected_revision: int = Field(ge=0)
    status: Literal["active", "suspended", "revoked"]
    expected_team_revision: int = Field(ge=1)


class ReviewedPolicy(Input):
    """A stored proposal, never effective until independently issued."""

    role: str = "contributor"
    parent_id: Identifier
    expected_parent_generation: int = Field(ge=1)
    tuples: tuple[Grant, ...] = Field(default=(), max_length=100)
    ceiling: tuple[Grant, ...] = Field(default=(), max_length=100)
    targets: Selector = Field(default_factory=Selector)
    allowed_roles: tuple[str, ...] = Field(default=(), max_length=8)
    remaining_depth: int = Field(default=0, ge=0, le=2)
    expires_at: datetime
    team_id: Identifier | None = None
    expected_team_generation: int | None = Field(default=None, ge=1)

    def issue(self, subject_id: str) -> Issue:
        return Issue(subject_id=subject_id, **self.model_dump())

    @model_validator(mode="after")
    def valid(self) -> Self:
        self.issue("validation-only-subject")
        return self


class InviteCreate(EmailInput):
    display_name: str = Field(min_length=1, max_length=128)
    policy: ReviewedPolicy
    expires_at: datetime

    @model_validator(mode="after")
    def valid(self) -> Self:
        if self.expires_at.tzinfo is None or self.expires_at > self.policy.expires_at:
            raise ValueError("invitation requires an aware expiry within the policy lifetime")
        return self


class RequestCreate(Input):
    policy: ReviewedPolicy
    expected_policy_revision: int = Field(ge=1)
    expires_at: datetime

    @model_validator(mode="after")
    def valid(self) -> Self:
        if self.expires_at.tzinfo is None or self.expires_at > self.policy.expires_at:
            raise ValueError("request requires an aware expiry within the policy lifetime")
        return self


class Decision(Revision):
    expected_policy_revision: int = Field(ge=1)
    outcome: Literal["approved", "denied"]


class RootApproval(Input):
    tuples: tuple[Grant, ...] = Field(default=(), max_length=100)
    ceiling: tuple[Grant, ...] = Field(default=(), max_length=100)
    targets: Selector = Field(default_factory=Selector)
    allowed_roles: tuple[str, ...] = Field(default=(), max_length=8)
    expires_at: datetime
