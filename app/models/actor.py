"""Common, credential-free provenance for resource mutations."""

from enum import StrEnum
from typing import Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    SerializerFunctionWrapHandler,
    model_serializer,
    model_validator,
)

from app.models.agent import AgentClientType
from app.models.common import Identifier


class ActorType(StrEnum):
    """The trusted kind of principal that performed a persisted write."""

    HUMAN = "human"
    AGENT = "agent"
    AUTOMATION = "automation"
    SYSTEM = "system"


class ActorProvenance(BaseModel):
    """A minimal actor record that never contains a credential or request body."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    actor_type: ActorType
    client_id: Identifier | None = None
    client_type: AgentClientType | None = None
    sponsor_id: Identifier | None = None
    sponsor_team_id: Identifier | None = None
    service_account_id: Identifier | None = None
    sponsorship_revision: int | None = None
    user_id: Identifier | None = None
    org_id: Identifier | None = None
    auth_method: Literal["local", "oidc"] | None = None
    assurance: Literal["password", "mfa", "authenticated"] | None = None

    @model_serializer(mode="wrap")
    def preserve_legacy_shape(self, handler: SerializerFunctionWrapHandler) -> dict[str, Any]:
        data: dict[str, Any] = handler(self)
        for field in (
            "user_id",
            "org_id",
            "auth_method",
            "assurance",
            "sponsor_id",
            "sponsor_team_id",
            "service_account_id",
            "sponsorship_revision",
        ):
            if data.get(field) is None:
                data.pop(field, None)
        return data

    @model_validator(mode="after")
    def validate_agent_identity(self) -> "ActorProvenance":
        """Only an agent actor may name a client, and it must name both facts."""
        has_client = self.client_id is not None
        has_type = self.client_type is not None
        if self.actor_type is ActorType.AGENT:
            if not has_client or not has_type:
                raise ValueError("an agent actor requires client_id and client_type")
        elif has_client or has_type:
            raise ValueError("only an agent actor may include client identity")
        sponsored = self.sponsor_id is not None
        if sponsored and (
            self.actor_type is not ActorType.AGENT
            or self.org_id is None
            or self.sponsorship_revision is None
            or self.sponsorship_revision < 1
        ):
            raise ValueError("verified machine sponsor requires org and policy revision")
        if not sponsored and any(
            v is not None
            for v in (self.sponsor_team_id, self.service_account_id, self.sponsorship_revision)
        ):
            raise ValueError("machine provenance requires verified sponsorship")
        human = (self.user_id, None if sponsored else self.org_id, self.auth_method, self.assurance)
        if any(value is not None for value in human) and (
            self.actor_type is not ActorType.HUMAN or any(value is None for value in human)
        ):
            raise ValueError(
                "verified human provenance requires user, organization, method and assurance"
            )
        return self


def system_actor() -> ActorProvenance:
    """Return the explicit provenance used for legacy and internal writes."""
    return ActorProvenance(actor_type=ActorType.SYSTEM)
