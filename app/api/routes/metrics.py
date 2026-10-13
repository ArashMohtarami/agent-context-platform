"""What this process has measured, for whatever collects it.

One endpoint, rendering the in-process registry in the Prometheus text format.
Nothing is pushed anywhere and no collector is required: a self-hosted scraper
reads this, and a person with `curl` reads the same thing.

It is behind `system:read` rather than open. A metrics body is not knowledge —
there is no query, no document, and no project in it — but it does describe the
shape and health of a deployment, and an endpoint that answers anybody is an
endpoint that tells anybody when the platform is struggling.

Reading this runs no dependency checks. A scrape must stay cheap enough to
happen every fifteen seconds forever, so the readiness gauges are written by
`/health/ready` when it runs rather than probed here. What a scrape costs is
one dictionary walk.
"""

from fastapi import APIRouter, Depends, Request, Response, status

from app.api.security import requires
from app.core.config import Settings
from app.observability.instruments import snapshot
from app.observability.metrics import CONTENT_TYPE

router = APIRouter(tags=["observability"])


@router.get(
    "/metrics",
    dependencies=[Depends(requires("system:read"))],
    response_class=Response,
    responses={
        200: {"content": {CONTENT_TYPE: {}}, "description": "The Prometheus text exposition."},
        404: {"description": "This deployment publishes no metrics."},
    },
)
async def metrics(request: Request) -> Response:
    """Render every metric this process holds.

    A deployment with metrics switched off answers 404 rather than an empty
    body, so a scraper configured against it fails visibly instead of recording
    that everything is quiet.
    """
    settings: Settings = request.app.state.settings
    if not settings.metrics_enabled:
        return Response(status_code=status.HTTP_404_NOT_FOUND)
    return Response(content=snapshot(), media_type=CONTENT_TYPE)
