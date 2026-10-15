"""What this process has measured, for whatever collects it.

One endpoint, rendering the in-process registry in the Prometheus text format.
Nothing is pushed anywhere and no collector is required: a self-hosted scraper
reads this, and a person with `curl` reads the same thing.

It requires a separate installation-operator credential. A metrics body is not knowledge —
there is no query, no document, and no project in it — but it does describe the
shape and health of a deployment, and an endpoint that answers anybody is an
endpoint that tells anybody when the platform is struggling.

Reading this runs no dependency checks. A scrape must stay cheap enough to
happen every fifteen seconds forever, so the readiness gauges are written by
`/health/ready` when it runs rather than probed here. What a scrape costs is
one dictionary walk.
"""

from hmac import compare_digest

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status

from app.core.config import Settings
from app.observability.instruments import snapshot
from app.observability.metrics import CONTENT_TYPE

router = APIRouter(tags=["observability"])


async def installation_operator(request: Request) -> None:
    """A separate operator credential; tenant system:read never grants raw metrics."""
    settings: Settings = request.app.state.settings
    path = settings.operator_metrics_token_file
    token = request.headers.get("authorization", "").removeprefix("Bearer ")
    if path is None:
        raise HTTPException(status_code=403, detail="installation operator required")
    expected = path.read_text(encoding="utf-8").strip()
    if len(expected) < 32 or not compare_digest(token, expected):
        raise HTTPException(status_code=403, detail="installation operator required")


@router.get("/metrics", dependencies=[Depends(installation_operator)], response_class=Response)
async def metrics(request: Request) -> Response:
    """Render installation-wide measurements to the installation operator."""
    settings: Settings = request.app.state.settings
    if not settings.metrics_enabled:
        return Response(status_code=status.HTTP_404_NOT_FOUND)
    return Response(content=snapshot(), media_type=CONTENT_TYPE)
