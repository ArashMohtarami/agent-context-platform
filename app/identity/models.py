"""Bounded browser inputs. Secrets never appear in model reprs or validation logs."""

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr, model_validator

from app.models.common import Identifier
from app.security.scopes import ALL_SCOPES

Token = Annotated[str, Field(min_length=32, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")]


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid")


class EmailInput(Input):
    email: str = Field(min_length=3, max_length=254)


class LoginInput(EmailInput):
    password: SecretStr = Field(max_length=1024)
    org_id: Identifier


class ChallengeInput(Input):
    token: Token = Field(repr=False)


class PasswordChallenge(ChallengeInput):
    password: SecretStr = Field(min_length=12, max_length=1024)
    code: SecretStr = Field(default=SecretStr(""), max_length=128)


class FactorInput(Input):
    code: SecretStr = Field(min_length=6, max_length=128)
    recovery: bool = False


class FinishLogin(ChallengeInput):
    code: SecretStr = Field(default=SecretStr(""), max_length=128)
    recovery: bool = False


class ChangePassword(Input):
    old_password: SecretStr = Field(max_length=1024)
    new_password: SecretStr = Field(min_length=12, max_length=1024)


# Names shared by recorded bootstrap approvals and the Phase 5 catalog.
MANAGEMENT_ACTIONS = frozenset(
    {
        "org:read",
        "org:manage",
        "org:transfer-owner",
        "members:read",
        "members:invite",
        "members:suspend",
        "members:reinstate",
        "teams:read",
        "teams:manage",
        "grants:read",
        "grants:delegate",
        "grants:all-in-org",
        "clients:read:own",
        "clients:read:team",
        "clients:read:org",
        "clients:manage:own",
        "clients:manage:team",
        "clients:manage:org",
        "credentials:issue:own",
        "credentials:revoke:own",
        "credentials:revoke:team",
        "credentials:revoke:org",
        "activity:read:self",
        "activity:read:team",
        "activity:read:org",
        "audit:read:org",
        "reports:export",
        "reviews:decide",
    }
)


class RootTuple(Input):
    action: str = Field(max_length=128)
    resource_kind: Literal[
        "organization",
        "team",
        "member",
        "project_directory",
        "project",
        "organization_knowledge",
        "creator_profile",
        "client",
        "client_memory",
        "skill",
        "research_project",
    ]
    selector: Literal["none", "include", "all_in_org"]
    ids: list[Identifier] = Field(default_factory=list, max_length=50)
    conditions: dict[str, Any] = Field(default_factory=dict, max_length=0)

    @model_validator(mode="after")
    def validate_pending_tuple(self) -> "RootTuple":
        if self.action not in {*ALL_SCOPES, *MANAGEMENT_ACTIONS}:
            raise ValueError("unrecognized action in pending root delegation")
        if len(set(self.ids)) != len(self.ids) or bool(self.ids) != (self.selector == "include"):
            raise ValueError("selector requires explicit unique include IDs, or no IDs")
        return self
