"""Best-effort operational activity reporting for MCP calls."""

import logging

from app.mcp.client import ContextApiClient
from app.mcp.middleware.client_context import ClientRequestContext

logger = logging.getLogger("agent_context.mcp")


class GatewayAuditMiddleware:
    """Record an activity event without making tool availability depend on audit IO."""

    def __init__(self, client: ContextApiClient) -> None:
        self._client = client

    async def record(
        self,
        context: ClientRequestContext | None,
        *,
        operation: str,
        tool: str | None,
        project_id: str | None,
        resource_type: str | None,
        resource_id: str | None,
        outcome: str,
        latency_ms: float,
    ) -> None:
        if context is None or context.client_id is None:
            return
        status = {"ok": "succeeded", "refused": "denied"}.get(outcome, "failed")
        try:
            await self._client.record_gateway_activity(
                operation=operation,
                tool=tool,
                project_id=project_id,
                resource_type=resource_type,
                resource_id=resource_id,
                status=status,
                latency_ms=latency_ms,
            )
        except Exception as error:
            # Auditing is never allowed to turn a successful knowledge request
            # into a failed one.  Do not include exception text: an upstream
            # response is not a trusted log field.
            logger.warning(
                "mcp_activity_record_failed",
                extra={
                    "client_id": context.client_id,
                    "client_type": context.client_type.value if context.client_type else None,
                    "mcp_tool": tool,
                    "error_type": type(error).__name__,
                },
            )
