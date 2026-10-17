"""Generic auth errors and input-free identity validation failures."""

from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from starlette.requests import Request
from starlette.responses import JSONResponse, Response


async def auth_rejected(request: Request, exception: Exception) -> JSONResponse:
    return JSONResponse({"detail": "authentication was not accepted"}, status_code=401)


async def auth_busy(request: Request, exception: Exception) -> JSONResponse:
    return JSONResponse(
        {"detail": "authentication is temporarily busy"},
        status_code=429,
        headers={"Retry-After": "60"},
    )


async def private_validation_error(request: Request, exception: Exception) -> Response:
    if request.url.path.startswith("/api/v6/"):
        return JSONResponse({"detail": "invalid request"}, status_code=422)
    if not isinstance(exception, RequestValidationError):
        return JSONResponse({"detail": "invalid request"}, status_code=422)
    return await request_validation_exception_handler(request, exception)


async def policy_denied(request: Request, exception: Exception) -> JSONResponse:
    from app.security.policy.evaluation import PolicyConflict

    return JSONResponse(
        {
            "detail": "policy changed; refresh effective access"
            if isinstance(exception, PolicyConflict)
            else "operation is outside effective access"
        },
        status_code=409 if isinstance(exception, PolicyConflict) else 403,
    )
