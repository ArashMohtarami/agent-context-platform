"""Stable, client-safe failures produced by the MCP gateway."""

from enum import StrEnum


class GatewayErrorCode(StrEnum):
    """The small public vocabulary for gateway authentication and policy failures."""

    UNAUTHENTICATED = "UNAUTHENTICATED"
    FORBIDDEN = "FORBIDDEN"
    CLIENT_DISABLED = "CLIENT_DISABLED"
    CREDENTIAL_EXPIRED = "CREDENTIAL_EXPIRED"
    PROJECT_FORBIDDEN = "PROJECT_FORBIDDEN"


class GatewayError(Exception):
    """A refusal that may safely be returned to an MCP client."""

    def __init__(self, code: GatewayErrorCode, message: str) -> None:
        self.code = code
        super().__init__(message)
