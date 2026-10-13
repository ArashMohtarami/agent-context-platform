"""Safe, meaningful failures for MCP callers.

An MCP client is a language model. What it is told about a failure becomes part
of a prompt, so every message here is written to be read by one: it says what
went wrong and what would fix it, and it never carries a stack trace, a URL, a
database error, or anything else about how the platform is put together.
"""

from typing import Final

from app.concurrency import REVISION_CONFLICT_CODE, REVISION_CONFLICT_MESSAGE

# What the Context API's status codes mean to a tool caller. Anything not named
# here is reported as an unavailable platform rather than guessed at.
_MESSAGES: Final[dict[int, str]] = {
    400: "the request was rejected as malformed",
    401: "the platform refused this call as unauthenticated",
    403: "the platform refused this call as unauthorized",
    404: "no such record exists, or it is outside the project that was named",
    409: "the record changed after it was read, or that change was already made",
    422: "the arguments were rejected as invalid",
    429: "the platform is rate limiting requests; try again shortly",
    503: "the platform cannot serve this request right now",
}
_UNKNOWN: Final = "the platform could not serve this request"


class ToolError(Exception):
    """A tool call that failed for a reason worth telling the caller.

    Everything that reaches a caller is one of these. Anything else is a defect
    in this server and is reported as an unavailable platform instead of being
    described, because an unexpected exception's text is not written for a
    model to read and may name internals.
    """


class UpstreamError(ToolError):
    """The Context API refused or could not serve the request."""

    def __init__(self, status_code: int, detail: str | None = None) -> None:
        super().__init__(describe_status(status_code, detail))
        self.status_code = status_code


class UnavailableError(ToolError):
    """The Context API could not be reached at all."""


class OperationTimeoutError(ToolError):
    """The Context API did not answer before the configured request deadline."""


class IdempotencyConflictToolError(ToolError):
    """A retry key was reused for different work and must not be replayed."""


class RevisionConflictToolError(ToolError):
    """A guarded update lost a race and must be reloaded before retrying."""

    code: Final = REVISION_CONFLICT_CODE
    message: Final = REVISION_CONFLICT_MESSAGE

    def __init__(self) -> None:
        super().__init__(self.message)


def describe_status(status_code: int, detail: str | None = None) -> str:
    """Describe one upstream status in words written for the caller.

    The API's own `detail` is included when it has one, because those messages
    are already written for clients and say which field or record was at fault.
    """
    message = _MESSAGES.get(status_code, _UNKNOWN)
    if detail:
        return f"{message}: {detail}"
    return message
