"""Bounded CSV reports. Receipts contain filters, never payloads or bearer secrets."""

import csv
from datetime import timedelta
from io import StringIO
from typing import Any, cast
from uuid import uuid4

from app.identity.repository import Row
from app.models.common import utc_now
from app.security.policy.engine import PolicyEngine
from app.security.policy.evaluation import PolicyDenied
from app.security.policy.governance import Governance
from app.summaries.coverage import REPORT_ACTION, coverage
from app.summaries.models import (
    MAX_REPORT_BYTES,
    MAX_REPORT_ROWS,
    REPORT_TTL_SECONDS,
    EventDTO,
    ReportDTO,
    SummaryQuery,
)
from app.summaries.projection import projection
from app.summaries.reads import retry_read
from app.summaries.service import MAX_TIME_MS, Summaries
from app.summaries.visibility import SOURCES, visibility


def csv_cell(value: Any) -> str:
    text = "" if value is None else str(value)
    # Cover leading ASCII/Unicode whitespace, controls, BOM and Excel formula
    # prefixes. Quoting alone does not stop spreadsheet formula evaluation.
    probe = text
    while probe and (probe[0].isspace() or ord(probe[0]) < 32 or probe[0] == "\ufeff"):
        probe = probe[1:]
    if probe[:1] in {"=", "+", "-", "@"} or text[:1] in {"\t", "\r", "\n"}:
        return "'" + text
    return text


class Reports:
    def __init__(self, engine: PolicyEngine) -> None:
        self.engine, self.service = engine, Summaries(engine)

    def authorize(self, snapshot: Row, query: SummaryQuery) -> Row:
        self.engine.require(
            snapshot, "reports:export", "organization", snapshot["anchor"]["org_id"]
        )
        return visibility(snapshot, query)

    async def create(self, org: str, actor: str, query: SummaryQuery) -> Row:
        self.service.context(org, actor)
        query.current(utc_now())
        report_id = "report_" + uuid4().hex
        expires = utc_now() + timedelta(seconds=REPORT_TTL_SECONDS)

        async def operation(snapshot: Row, tx: Any) -> Row:
            self.authorize(snapshot, query)
            await self.service.ready(tx)
            await self.engine.audit(
                org,
                actor,
                REPORT_ACTION,
                report_id,
                tx,
                change={
                    "query": query.model_dump(mode="json"),
                    "expires_at": expires,
                    "policy_revision": snapshot["anchor"]["revision"] + 1,
                    "requester_revision": snapshot["member"]["revision"],
                    "version": 1,
                },
            )
            return ReportDTO(
                report_id=report_id,
                download_path=f"/api/v6/organizations/{org}/reports/{report_id}/download",
                expires_at=expires,
            ).model_dump()

        return await Governance(self.engine).mutate(org, actor, operation)

    @retry_read
    async def download(self, org: str, actor: str, report_id: str) -> tuple[str, Row]:
        self.service.context(org, actor)
        async with self.engine.transaction() as tx:
            snapshot = await self.engine.snapshot(org, actor, tx)
            await self.service.ready(tx)
            receipt = await self.service.db["governance_events"].find_one(
                {
                    "org_id": org,
                    "actor_id": actor,
                    "action": REPORT_ACTION,
                    "target_id": report_id,
                    "change.expires_at": {"$gt": utc_now()},
                },
                session=tx,
                max_time_ms=MAX_TIME_MS,
            )
            if not receipt:
                raise PolicyDenied("report is unavailable")
            command = cast(Row, receipt["change"])
            if (
                command["version"] != 1
                or command["policy_revision"] != snapshot["anchor"]["revision"]
                or command["requester_revision"] != snapshot["member"]["revision"]
            ):
                raise PolicyDenied("report policy changed; request a new report")
            query = SummaryQuery.model_validate(command["query"])
            query.current(utc_now())
            predicate = self.authorize(snapshot, query)
            declared = await self.service.checkpoint(org, query.source, tx)
            if declared:
                predicate = {
                    "$and": [
                        predicate,
                        {"created_at": {"$gte": declared["change"]["retained_from"]}},
                    ]
                }
            rows = await (
                await self.service.db[SOURCES[query.source]].aggregate(
                    [
                        {"$match": predicate},
                        {"$project": projection(query.source)},
                        {"$sort": {"event_id": 1}},
                        {"$limit": MAX_REPORT_ROWS + 1},
                    ],
                    session=tx,
                    maxTimeMS=MAX_TIME_MS,
                    allowDiskUse=False,
                )
            ).to_list(MAX_REPORT_ROWS + 1)
            if len(rows) > MAX_REPORT_ROWS:
                raise PolicyDenied("report exceeds bounded capacity; narrow the window")
            output = StringIO(newline="")
            writer = csv.writer(output, lineterminator="\r\n")
            fields = tuple(EventDTO.model_fields)
            writer.writerow(fields)
            for row in rows:
                safe = EventDTO.model_validate(row).model_dump(mode="json")
                writer.writerow([csv_cell(safe[field]) for field in fields])
            result = output.getvalue()
            if len(result.encode("utf-8")) > MAX_REPORT_BYTES:
                raise PolicyDenied("report exceeds bounded capacity; narrow the window")
            metadata = {
                "source": query.source,
                "timezone": query.timezone,
                "start": query.start.isoformat(),
                "end": query.end.isoformat(),
                "observed_count": len(rows),
                "coverage": coverage(query, declared, utc_now()).model_dump(mode="json"),
            }
        if utc_now() >= command["expires_at"]:
            raise PolicyDenied("report is unavailable")
        await self.service.confirm_current(snapshot, query, authorize=self.authorize)
        if utc_now() >= command["expires_at"]:
            raise PolicyDenied("report is unavailable")
        return result, metadata
