"""Session-only authorized event summaries, audit and bounded report downloads."""

import json
from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Path, Query, Response

from app.api.routes.human_management import management
from app.identity.browser import CurrentBrowser
from app.summaries.models import (
    ActivityDTO,
    ActivityQuery,
    AuditDiffDTO,
    AuditQuery,
    ReportDTO,
    SummaryDTO,
    SummaryQuery,
)
from app.summaries.reports import Reports
from app.summaries.service import Summaries

router = APIRouter(prefix="/api/v6/organizations/{org_id}", tags=["authorized summaries"])
Filters = Annotated[SummaryQuery, Query()]


def service(context: CurrentBrowser) -> Summaries:
    engine = management(context)
    if not engine.managed_agents:
        raise HTTPException(404, "authorized summaries require verified machine management")
    return Summaries(engine)


@router.get("/overview", response_model=SummaryDTO)
async def overview(org_id: str, context: CurrentBrowser, query: Filters) -> Any:
    return await service(context).read(org_id, context.row["user_id"], query, overview=True)


@router.get("/activity", response_model=ActivityDTO)
async def activity(
    org_id: str,
    context: CurrentBrowser,
    query: Annotated[ActivityQuery, Query()],
) -> Any:
    return await service(context).read(
        org_id, context.row["user_id"], query.filters(), limit=query.limit, cursor=query.cursor
    )


@router.post("/reports", response_model=ReportDTO, status_code=201)
async def report(org_id: str, context: CurrentBrowser, body: SummaryQuery) -> Any:
    current = service(context)
    context.service.sessions.recent(context.row)
    return await Reports(current.engine).create(org_id, context.row["user_id"], body)


@router.get("/reports/{report_id}/download")
async def download(
    org_id: str,
    context: CurrentBrowser,
    report_id: Annotated[str, Path(pattern=r"^report_[0-9a-f]{32}$")],
) -> Response:
    current = service(context)
    context.service.sessions.recent(context.row)
    content, metadata = await Reports(current.engine).download(
        org_id, context.row["user_id"], report_id
    )
    return Response(
        content,
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": 'attachment; filename="activity.csv"',
            "X-Content-Type-Options": "nosniff",
            "X-Summary-Metadata": json.dumps(metadata, ensure_ascii=True, separators=(",", ":")),
        },
    )


@router.get("/audit-diff", response_model=AuditDiffDTO)
async def audit_diff(
    org_id: str, context: CurrentBrowser, query: Annotated[AuditQuery, Query()]
) -> Any:
    return await service(context).audit_diff(org_id, context.row["user_id"], query)
