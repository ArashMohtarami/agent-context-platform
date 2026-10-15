"""Request-scoped, server-derived actor provenance helpers."""

from contextvars import ContextVar, Token
from typing import TypeVar

from app.models.actor import ActorProvenance, ActorType, system_actor
from app.models.agent import AgentClientType
from app.models.common import MongoDocumentModel
from app.security.principal import Principal

_current: ContextVar[ActorProvenance | None] = ContextVar("actor_provenance", default=None)
TDocument = TypeVar("TDocument", bound=MongoDocumentModel)


def begin() -> Token[ActorProvenance | None]:
    """Establish a system actor until authentication resolves the caller."""
    return _current.set(system_actor())


def end(token: Token[ActorProvenance | None]) -> None:
    """Clear a request's actor identity so it cannot cross request boundaries."""
    _current.reset(token)


def actor_from_principal(principal: Principal) -> ActorProvenance:
    """Derive actor provenance from the authenticated credential only."""
    if principal.client_id is not None or principal.client_type is not None:
        if principal.client_id is None or principal.client_type is None:
            raise ValueError("principal has incomplete client identity")
        return ActorProvenance(
            actor_type=ActorType.AGENT,
            client_id=principal.client_id,
            client_type=AgentClientType(principal.client_type),
        )
    return ActorProvenance(
        actor_type=ActorType.HUMAN if principal.authenticated else ActorType.SYSTEM
    )


def set_principal(principal: Principal) -> ActorProvenance:
    """Bind an authenticated request's actor for repositories reached below it."""
    actor = actor_from_principal(principal)
    _current.set(actor)
    return actor


def current() -> ActorProvenance:
    """Return the request actor, falling back to explicit system provenance."""
    return _current.get() or system_actor()


def set_human(user_id: str, org_id: str, assurance: str, method: str = "local") -> ActorProvenance:
    """Bind a verified browser user without manufacturing a machine key/client."""
    from typing import Literal, cast

    actor = ActorProvenance(
        actor_type=ActorType.HUMAN,
        user_id=user_id,
        org_id=org_id,
        auth_method=cast(Literal["local", "oidc"], method),
        assurance=cast(Literal["password", "mfa", "authenticated"], assurance),
    )
    _current.set(actor)
    return actor


def attribute[TDocument: MongoDocumentModel](document: TDocument) -> TDocument:
    """Replace a provenance-enabled document's actor with trusted request identity."""
    if "actor" not in type(document).model_fields:
        return document
    return document.model_copy(update={"actor": current()})
