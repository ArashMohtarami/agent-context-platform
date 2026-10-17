"""Explicit stream completeness. An empty retained log is not collection evidence."""

from datetime import datetime, timedelta

from app.identity.repository import Row
from app.summaries.models import CoverageDTO, SummaryQuery

CHECKPOINT_ACTION = "summary.checkpoint.v1"
REPORT_ACTION = "summary.report.v1"


def coverage(query: SummaryQuery, checkpoint: Row | None, now: datetime) -> CoverageDTO:
    population = (
        "permitted retained gateway-reported events"
        if query.source == "gateway_activity"
        else "permitted retained transactional governance events"
    )
    contract = (
        "best_effort_gateway_reports; not all REST calls or employee activity"
        if query.source == "gateway_activity"
        else "committed governance commands; not best-effort domain audit or security login history"
    )
    base = dict(source=query.source, population=population, collection_contract=contract)
    if checkpoint is None:
        return CoverageDTO.model_validate(
            dict(
                **base,
                state="missing",
                watermark=None,
                coverage_start=None,
                retained_from=None,
                checked_at=None,
                storage_retention="undeclared",
            )
        )
    declared = checkpoint["change"]
    start, retained, through = (
        declared["coverage_start"],
        declared["retained_from"],
        declared["through"],
    )
    gaps = [
        g for g in declared.get("gaps", []) if g["start"] < query.end and g["end"] > query.start
    ]
    if not declared["available"]:
        state = "unavailable"
    elif query.end <= retained:
        state = "absent_history"
    elif through < now - timedelta(minutes=15):
        state = "stale"
    elif (
        query.source == "gateway_activity"
        or query.start < max(start, retained)
        or query.end > through
        or gaps
    ):
        state = "partial"
    else:
        state = "complete"
    return CoverageDTO.model_validate(
        dict(
            **base,
            state=state,
            watermark=through,
            coverage_start=start,
            retained_from=retained,
            checked_at=checkpoint["created_at"],
            storage_retention=declared["storage_retention"],
            gaps=gaps,
        )
    )
