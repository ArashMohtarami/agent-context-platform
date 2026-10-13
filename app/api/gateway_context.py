"""Safe optional gateway provenance supplied with an MCP-originated API call."""

from dataclasses import dataclass

from fastapi import Request

from app.models.agent import AgentClientType
from app.models.common import validate_identifier
from app.security.principal import Principal


@dataclass(frozen=True, slots=True)
class GatewayRetrievalMetadata:
    """Attribution metadata; it is never used to authorize a request."""

    client_id: str | None = None
    client_type: AgentClientType | None = None
    session_id: str | None = None


def retrieval_metadata(request: Request, principal: Principal) -> GatewayRetrievalMetadata:
    """Derive retrieval provenance from the authenticated credential.

    Headers can carry only the optional session correlation. They never get to
    name the client or client type: accepting that would allow one bearer to
    create retrieval logs that appeared to belong to another agent.
    """
    # Some dependency-wiring callers intentionally construct the smallest
    # request double possible.  Missing optional provenance must remain
    # equivalent to absent gateway headers.
    headers = getattr(request, "headers", {})
    client_id = principal.client_id
    client_type = principal.client_type
    session_id = headers.get("x-agent-context-session-id")
    if client_id is None or client_type is None:
        return GatewayRetrievalMetadata()
    try:
        parsed_client_type = AgentClientType(client_type)
        parsed_client_id = validate_identifier(client_id)
    except ValueError:
        return GatewayRetrievalMetadata()
    # A remote MCP request carries identity headers from its gateway context.
    # A direct REST caller may send them too, but they are only a consistency
    # assertion and can never replace the credential's identity.
    if headers.get("x-agent-context-client-id") not in {None, parsed_client_id} or headers.get(
        "x-agent-context-client-type"
    ) not in {None, parsed_client_type.value}:
        return GatewayRetrievalMetadata(
            client_id=parsed_client_id,
            client_type=parsed_client_type,
        )
    try:
        parsed_session_id = None if session_id is None else validate_identifier(session_id)
    except ValueError:
        parsed_session_id = None
    return GatewayRetrievalMetadata(
        client_id=parsed_client_id,
        client_type=parsed_client_type,
        session_id=parsed_session_id,
    )
