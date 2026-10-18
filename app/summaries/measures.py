"""Additional metadata facets share the Phase8 predicate, never client aggregation."""

from math import isfinite

from app.identity.repository import Row
from app.summaries.models import Source


def fields(source: Source) -> Row:
    if source == "governance":
        return {"_access_decision": {"$eq": ["$action", "access_request_decided"]}}
    return {
        "_latency": {
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
    }


def facets(source: Source) -> Row:
    if source == "governance":
        return {"reviews": [{"$match": {"_access_decision": True}}, {"$count": "count"}]}
    return {
        "clients": [
            {"$match": {"actor_kind": "machine"}},
            {"$group": {"_id": "$actor_id"}},
            {"$count": "count"},
        ],
        "verified": [{"$match": {"actor_kind": "machine"}}, {"$count": "count"}],
        "latency": [
            {"$match": {"_latency": {"$ne": None}}},
            {"$group": {"_id": None, "count": {"$sum": 1}, "mean": {"$avg": "$_latency"}}},
        ],
        "unattributed": [{"$match": {"attribution": "legacy_unattributed"}}, {"$count": "count"}],
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
