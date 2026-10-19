"""Authorized, MongoDB-computed read models; no process metric is a history source."""

from collections.abc import Callable
from typing import Any, cast

from app.identity.repository import Row
from app.models.common import InvalidCursorError, utc_now
from app.security.policy.engine import PolicyEngine
from app.security.policy.evaluation import PolicyConflict, PolicyDenied
from app.security.tenant import require_tenant
from app.security.tenant_cursor import binding, decode_directory_cursor, encode_directory_cursor
from app.summaries import measures
from app.summaries.coverage import CHECKPOINT_ACTION, coverage
from app.summaries.models import AuditQuery, SummaryQuery
from app.summaries.projection import projection
from app.summaries.reads import retry_read
from app.summaries.visibility import SOURCES, visibility

MAX_TIME_MS = 3000


class Summaries:
    def __init__(self, engine: PolicyEngine) -> None:
        if not engine.managed_agents:
            raise PolicyDenied("verified Phase7 machine sponsorship is required")
        self.engine, self.db = engine, engine.store.database

    @staticmethod
    def context(org: str, actor: str) -> None:
        from app.security.tenant_policy import current_policy

        policy = current_policy()
        if require_tenant().org_id != org or (
            policy is not None
            and (
                policy.org_id != org or policy.subject_id != actor or policy.subject_kind != "human"
            )
        ):
            raise PolicyDenied("trusted current human organization context is required")

    async def ready(self, tx: Any) -> None:
        if not await self.engine.store.find(
            "_managed_agents_control", {"_id": "installation", "activated": True}, tx
        ):
            raise PolicyDenied("verified machine activation is required")

    async def checkpoint(self, org: str, source: str, tx: Any) -> Row | None:
        return await self.db["governance_events"].find_one(
            {"org_id": org, "action": CHECKPOINT_ACTION, "target_id": source},
            sort=[("created_at", -1), ("_id", -1)],
            session=tx,
            max_time_ms=MAX_TIME_MS,
        )

    async def aggregate(
        self,
        snapshot: Row,
        query: SummaryQuery,
        tx: Any,
        *,
        limit: int = 50,
        cursor: str | None = None,
        overview: bool = False,
    ) -> Row:
        now = utc_now()
        query.current(now)
        if not 1 <= limit <= 100:
            raise ValueError("page size must be between 1 and 100")
        predicate = visibility(snapshot, query)
        org = snapshot["anchor"]["org_id"]
        checkpoint = await self.checkpoint(org, query.source, tx)
        # Retention is enforced before every count/detail/export, even if raw
        # installation archives retain older events. Never rewrite old actors.
        if checkpoint:
            predicate = {
                "$and": [predicate, {"created_at": {"$gte": checkpoint["change"]["retained_from"]}}]
            }
        revision = snapshot["anchor"]["revision"]
        scope = binding(
            "summary:" + query.source,
            {
                "query": query.model_dump(mode="json"),
                "visibility": predicate,
                "revision": revision,
                "subject": snapshot["member"]["user_id"],
            },
        )
        after: list[Row] = []
        if cursor:
            try:
                identifier = decode_directory_cursor(cursor, scope)
            except InvalidCursorError:
                raise PolicyConflict("activity cursor changed; start a new page") from None
            after = [{"$match": {"event_id": {"$gt": identifier}}}]
        facets: Row = {
            "total": [{"$count": "count"}],
            "outcomes": [{"$group": {"_id": "$outcome", "count": {"$sum": 1}}}],
        }
        if not overview:
            facets["items"] = [*after, {"$sort": {"event_id": 1}}, {"$limit": limit + 1}]
        rows = await (
            await self.db[SOURCES[query.source]].aggregate(
                [
                    {"$match": predicate},
                    *(
                        measures.overview_pipeline(query)
                        if overview
                        else [{"$project": projection(query.source)}, {"$facet": facets}]
                    ),
                ],
                session=tx,
                maxTimeMS=MAX_TIME_MS,
                allowDiskUse=False,
            )
        ).to_list(1)
        result = cast(Row, rows[0])
        count = result["total"][0]["count"] if result["total"] else 0
        common = {
            "observed_count": count,
            "policy_revision": revision,
            "coverage": coverage(query, checkpoint, now).model_dump(),
        }
        if not overview:
            items = result["items"]
            return {
                **common,
                "items": items[:limit],
                "next_cursor": encode_directory_cursor(items[limit - 1]["event_id"], scope)
                if len(items) > limit
                else None,
            }
        return {
            **common,
            "org_id": org,
            "query": query.model_dump(),
            "generated_at": now,
            "observation_state": "recorded_events" if count else "no_recorded_events",
            "outcomes": {r["_id"]: r["count"] for r in result["outcomes"]},
            "buckets": [
                {"start": r["_id"], "count": r["count"], "end": r["end"]} for r in result["buckets"]
            ],
            "measures": measures.metrics(result, query.source, count),
            "sparse_series": True,
        }

    async def confirm_current(
        self,
        original: Row,
        query: SummaryQuery,
        *,
        authorize: Callable[[Row, SummaryQuery], Row] | None = None,
    ) -> None:
        # Recheck time-based expiry, global user status and the exact visibility
        # envelope. A subsequent atomic anchor CAS closes the snapshot/revoke
        # race without making every concurrent aggregate hold a write lock.
        org, actor = original["anchor"]["org_id"], original["member"]["user_id"]
        async with self.engine.transaction() as tx:
            await self.ready(tx)
            current = await self.engine.snapshot(org, actor, tx)
            if authorize is not None:
                authorize(current, query)
            if current["anchor"]["revision"] != original["anchor"]["revision"] or visibility(
                current, query
            ) != visibility(original, query):
                raise PolicyConflict("policy changed; refresh bounded filters")
        await self.engine.confirm_read(current["anchor"])

    @retry_read
    async def read(
        self,
        org: str,
        actor: str,
        query: SummaryQuery,
        *,
        overview: bool = False,
        limit: int = 50,
        cursor: str | None = None,
    ) -> Row:
        self.context(org, actor)
        async with self.engine.transaction() as tx:
            await self.ready(tx)
            snapshot = await self.engine.snapshot(org, actor, tx)
            result = await self.aggregate(
                snapshot, query, tx, overview=overview, limit=limit, cursor=cursor
            )
        await self.confirm_current(snapshot, query)
        return result

    @retry_read
    async def audit_diff(self, org: str, actor: str, query: AuditQuery) -> Row:
        from app.summaries.audit import REDACTION, diff

        self.context(org, actor)
        filters = query.filters()
        filters.current(utc_now())
        async with self.engine.transaction() as tx:
            await self.ready(tx)
            snapshot = await self.engine.snapshot(org, actor, tx)
            predicate = visibility(snapshot, filters)
            declared = await self.checkpoint(org, "governance", tx)
            parts = [predicate, {"_id": query.event_id}]
            if declared:
                parts.append({"created_at": {"$gte": declared["change"]["retained_from"]}})
            rows = await (
                await self.db["governance_events"].aggregate(
                    [
                        {"$match": {"$and": parts}},
                        {"$limit": 1},
                        {"$project": {**projection("governance"), "_change": "$change"}},
                    ],
                    session=tx,
                    maxTimeMS=MAX_TIME_MS,
                    allowDiskUse=False,
                )
            ).to_list(1)
            if not rows:
                raise PolicyDenied("audit event is unavailable in the permitted window")
            event = rows[0]
            change = event.pop("_change", {})
            fields = (
                diff(change)
                if isinstance(change, dict) and event["attribution"] == "verified_event_time"
                else []
            )
            result = {
                "event": event,
                "fields": fields,
                "state": "recorded_fields" if fields else "unavailable",
                "redaction": REDACTION,
                "coverage": coverage(filters, declared, utc_now()).model_dump(),
            }
        await self.confirm_current(snapshot, filters)
        return result
