"""What every MCP tool is made of.

A tool is a name, a scope it requires, an arguments model, and one coroutine
that turns validated arguments into a result. It holds no state, reads no
configuration, and reaches the platform only through the client it is handed —
which is what keeps a tool small enough to read in one sitting and to test
without a server.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal, Protocol

from app.mcp.auth import Scope
from app.mcp.client import ContextApiClient
from app.mcp.formatting import compact_result, render_results
from app.mcp.gateway_controls import ExecutionClass
from app.mcp.schemas import ToolArguments, json_schema


@dataclass(frozen=True, slots=True)
class ToolResult:
    """One tool's answer, in both the forms MCP delivers.

    `data` is what a client acts on; `text` is what a model reads. They
    describe the same answer, so a client never has to parse prose and a model
    never has to parse JSON to find the content it was asking for.
    """

    text: str
    data: dict[str, Any]


class ToolHandler(Protocol):
    """The one coroutine behind a tool."""

    async def __call__(
        self,
        client: ContextApiClient,
        arguments: Any,
    ) -> ToolResult: ...


@dataclass(frozen=True, slots=True)
class ToolDefinition:
    """One registered MCP tool."""

    name: str
    title: str
    description: str
    scope: Scope
    arguments: type[ToolArguments]
    handler: ToolHandler
    writes: bool = False
    stable_envelope: bool = False
    session_lifecycle: Literal["begin", "end"] | None = None
    execution_class: ExecutionClass = "fast"

    def __post_init__(self) -> None:
        """Reject a definition a client could not use."""
        if not self.name or not self.description:
            raise ValueError("a tool requires a name and a description")

    @property
    def input_schema(self) -> dict[str, object]:
        """Return the JSON Schema this tool advertises for its arguments."""
        return json_schema(self.arguments)

    def parse(self, arguments: dict[str, Any]) -> ToolArguments:
        """Validate one call's arguments against this tool's model."""
        return self.arguments.model_validate(arguments)


def retrieval_result(body: Mapping[str, Any], *, heading: str) -> ToolResult:
    """Shape one retrieval answer, whichever sources produced it.

    Every search tool returns this, so a caller reads one shape whether it
    asked for skills, one project's context, memory, or all of them. The
    platform's own retrieval metadata is passed through as it arrived: it is
    what makes a surprising answer explainable rather than a guess.
    """
    results = [compact_result(item) for item in body.get("results", [])]
    data: dict[str, Any] = {
        "query": body.get("query"),
        "project_id": body.get("project_id"),
        "results": results,
    }
    for field in ("token_count", "token_budget", "retrieval"):
        if field in body:
            data[field] = body[field]
    return ToolResult(render_results(results, heading=heading), data)


def tool(
    name: str,
    *,
    title: str,
    description: str,
    scope: Scope,
    arguments: type[ToolArguments],
    handler: ToolHandler,
    writes: bool = False,
    stable_envelope: bool = False,
    session_lifecycle: Literal["begin", "end"] | None = None,
    execution_class: ExecutionClass = "fast",
) -> ToolDefinition:
    """Define one tool, keeping every definition in the same shape."""
    return ToolDefinition(
        name=name,
        title=title,
        description=description,
        scope=scope,
        arguments=arguments,
        handler=handler,
        writes=writes,
        stable_envelope=stable_envelope,
        session_lifecycle=session_lifecycle,
        execution_class=execution_class,
    )
