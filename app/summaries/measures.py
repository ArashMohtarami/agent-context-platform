"""Additional metadata facets share the Phase8 predicate, never client aggregation."""

from math import isfinite

from app.identity.repository import Row
from app.summaries.models import Source, SummaryQuery


def overview_pipeline(query: SummaryQuery) -> list[Row]:
    """Reduce permitted events once before faceting the compact measure groups.

    This is a live bounded aggregation, not a stored rollup or historical cache.
    No actor/content projection or independent full-event facet is needed for
    an overview. Exact distinct-client and weighted-latency semantics survive
    reduction by bucket, outcome and verified client.
    """
    gateway = query.source == "gateway_activity"
    verified = {
        "$and": [
            {"$ne": [{"$ifNull": ["$sponsor_id", None]}, None]},
            {"$gte": [{"$ifNull": ["$sponsorship_revision", 0]}, 1]},
        ]
    }
    latency = (
        {
            "$cond": [
                {
                    "$and": [
                        {"$isNumber": "$latency_ms"},
                        {"$gte": ["$latency_ms", 0]},
                        {"$lt": ["$latency_ms", float("inf")]},
                    ]
                },
                "$latency_ms",
                None,
            ]
        }
        if gateway
        else None
    )
    grouping: Row = {
        "_id": {
            "bucket": {
                "$dateTrunc": {
                    "date": "$created_at",
                    "unit": query.bucket,
                    "timezone": query.timezone,
                }
            },
            "outcome": "$status" if gateway else {"$literal": "committed"},
            "client": {"$cond": [verified, "$client_id", None]} if gateway else None,
        },
        "count": {"$sum": 1},
    }
    if gateway:
        grouping.update(
            {
                "verified": {"$sum": {"$cond": [verified, 1, 0]}},
                "unattributed": {"$sum": {"$cond": [verified, 0, 1]}},
                "latency_count": {"$sum": {"$cond": [{"$ne": [latency, None]}, 1, 0]}},
                "latency_sum": {"$sum": latency},
            }
        )
    else:
        grouping["reviews"] = {
            "$sum": {"$cond": [{"$eq": ["$action", "access_request_decided"]}, 1, 0]}
        }
    result: Row = {
        "total": [{"$group": {"_id": None, "count": {"$sum": "$count"}}}],
        "outcomes": [{"$group": {"_id": "$_id.outcome", "count": {"$sum": "$count"}}}],
        "buckets": [
            {"$group": {"_id": "$_id.bucket", "count": {"$sum": "$count"}}},
            {
                "$set": {
                    "end": {
                        "$dateAdd": {
                            "startDate": "$_id",
                            "unit": query.bucket,
                            "amount": 1,
                            "timezone": query.timezone,
                        }
                    }
                }
            },
            {"$sort": {"_id": 1}},
        ],
    }
    result.update(facets(query.source))
    return [{"$group": grouping}, {"$facet": result}]


def facets(source: Source) -> Row:
    if source == "governance":
        return {"reviews": [{"$group": {"_id": None, "count": {"$sum": "$reviews"}}}]}
    return {
        "clients": [
            {"$match": {"_id.client": {"$ne": None}}},
            {"$group": {"_id": "$_id.client"}},
            {"$count": "count"},
        ],
        "verified": [{"$group": {"_id": None, "count": {"$sum": "$verified"}}}],
        "latency": [
            {"$match": {"latency_count": {"$gt": 0}}},
            {
                "$group": {
                    "_id": None,
                    "count": {"$sum": "$latency_count"},
                    "sum": {"$sum": "$latency_sum"},
                }
            },
            {"$set": {"mean": {"$divide": ["$sum", "$count"]}}},
        ],
        "unattributed": [{"$group": {"_id": None, "count": {"$sum": "$unattributed"}}}],
    }


def metrics(result: Row, source: Source, count: int) -> Row:
    def n(key: str) -> int:
        return result[key][0]["count"] if result.get(key) else 0

    def metric(
        value: int | float | None,
        samples: int,
        definition: str,
        unit: str = "events",
        denominator: int | None = count,
    ) -> Row:
        if isinstance(value, float) and not isfinite(value):
            value = None
        return {
            "value": value,
            "sample_count": samples,
            "denominator": denominator,
            "state": "unavailable"
            if value is None
            else "observed"
            if samples
            else "no_recorded_samples",
            "source": source,
            "unit": unit,
            "definition": definition,
        }

    if source == "governance":
        return {
            "access_reviews": metric(
                n("reviews"),
                n("reviews"),
                "Committed access_request_decided events; not corpus reviews or all review work.",
            )
        }
    latency = result["latency"][0] if result["latency"] else None
    return {
        "calls": metric(count, count, "Persisted gateway reports; not all REST/MCP calls."),
        "clients": metric(
            n("clients"),
            n("verified"),
            "Distinct verified event-time machine clients in permitted reports; "
            "not an inventory or presence count.",
            "clients",
            n("verified"),
        ),
        "latency": metric(
            latency["mean"] if latency else None,
            n("latency"),
            "Mean finite nonnegative gateway-reported latency; missing samples excluded; "
            "not server request timing.",
            "milliseconds",
        ),
        "unattributed": metric(
            n("unattributed"), count, "Legacy events without verified event-time attribution."
        ),
        "access_reviews": metric(
            None,
            0,
            "Unavailable in gateway reports; committed access decisions require "
            "the separately authorized governance stream.",
            denominator=None,
        ),
    }
