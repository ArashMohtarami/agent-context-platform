"""Transport admission rules for the one remote Streamable HTTP MCP edge."""

from dataclasses import dataclass
from ipaddress import ip_address
from typing import TYPE_CHECKING

from starlette.types import Scope

if TYPE_CHECKING:
    from app.core.config import Settings


@dataclass(frozen=True, slots=True)
class RemoteMcpTransportPolicy:
    """Require HTTPS remotely while preserving an explicitly local dev path.

    ``scope["scheme"]`` is authoritative here.  A reverse proxy may set it
    from ``X-Forwarded-Proto`` only when Uvicorn was separately configured to
    trust that proxy; this policy never reads a caller-provided forwarded
    header itself.  That keeps an internet client from upgrading its own HTTP
    request merely by adding a header.
    """

    allow_loopback_http: bool = True
    max_sessions: int = 100

    def __post_init__(self) -> None:
        if self.max_sessions < 1:
            raise ValueError("remote MCP maximum sessions must be positive")

    @classmethod
    def from_settings(cls, settings: "Settings") -> "RemoteMcpTransportPolicy":
        """Build production-safe admission from the application deployment mode."""
        development = settings.app_env.lower() in {"development", "test", "testing"}
        return cls(
            allow_loopback_http=development and settings.mcp_remote_allow_loopback_http,
            max_sessions=settings.mcp_remote_max_sessions,
        )

    def permits(self, scope: Scope) -> bool:
        """Allow TLS, or a direct loopback HTTP peer in controlled development."""
        scheme = scope.get("scheme")
        if scheme == "https":
            return True
        if scheme != "http" or not self.allow_loopback_http:
            return False
        return _is_loopback_peer(scope.get("client"))


def _is_loopback_peer(value: object) -> bool:
    """Return whether ASGI's direct transport peer is a loopback address."""
    if not isinstance(value, tuple) or not value or not isinstance(value[0], str):
        return False
    try:
        return ip_address(value[0]).is_loopback
    except ValueError:
        # ASGI servers conventionally pass an IP address.  A hostname or an
        # unparsable value must not turn into a local-development exception.
        return False


__all__ = ["RemoteMcpTransportPolicy"]
