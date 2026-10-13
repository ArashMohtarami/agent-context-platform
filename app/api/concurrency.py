"""One stable HTTP response for optimistic-concurrency conflicts."""

from fastapi import HTTPException, Request, status
from starlette.responses import JSONResponse

from app.concurrency import REVISION_CONFLICT_CODE, REVISION_CONFLICT_MESSAGE


class RevisionConflict(HTTPException):
    """A conditional update lost a race after the caller read its resource."""

    def __init__(self) -> None:
        # The ordinary FastAPI shape remains safe in narrow router-only tests
        # that do not install the application exception handler. Production
        # installs ``revision_conflict_handler`` below for the V4 contract.
        super().__init__(status_code=status.HTTP_409_CONFLICT, detail=REVISION_CONFLICT_MESSAGE)


async def revision_conflict_handler(request: Request, exception: Exception) -> JSONResponse:
    """Return a machine-readable conflict without exposing persistence details."""
    _ = request, exception
    return JSONResponse(
        status_code=status.HTTP_409_CONFLICT,
        content={
            "error": {
                "code": REVISION_CONFLICT_CODE,
                "message": REVISION_CONFLICT_MESSAGE,
            }
        },
    )
