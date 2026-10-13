"""Common, credential-free provenance for resource mutations."""

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, model_validator

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
        return self


def system_actor() -> ActorProvenance:
    """Return the explicit provenance used for legacy and internal writes."""
    return ActorProvenance(actor_type=ActorType.SYSTEM)
