"""Compile content's transitive project envelope before query selection."""

from collections.abc import Mapping
from typing import Any

from app.db.client_types import MongoDocument
from app.db.tenant_inventory import references

# Reference compilation never needs prose, vectors, replay bodies or arbitrary
# metadata. Keep a broad candidate universe from loading every document body.
REFERENCE_PROJECTION = dict.fromkeys(
    (
        "content",
        "body",
        "embedding",
        "title",
        "description",
        "summary",
        "question",
        "statement",
        "proposed_change",
        "proposed_patch",
        "reason",
        "notes",
        "review_notes",
        "rationale",
        "confidence_rationale",
        "evidence_excerpt",
        "metadata",
        "provider_metadata",
        "result_data",
        "metrics",
        "voice",
        "audiences",
        "domains",
        "trust_metadata",
    ),
    0,
)


def direct_projects(row: Mapping[str, Any]) -> set[str]:
    result = set(row.get("resource_project_ids") or [])
    if isinstance(row.get("project_id"), str):
        result.add(row["project_id"])
    result.update(row.get("project_ids") or [])
    authorization = row.get("authorization")
    if isinstance(authorization, Mapping):
        result.update(authorization.get("project_ids") or [])
    origin = row.get("origin")
    if isinstance(origin, Mapping) and isinstance(origin.get("project_id"), str):
        result.add(origin["project_id"])
    for source in row.get("source_refs") or []:
        if isinstance(source, Mapping) and isinstance(source.get("project_id"), str):
            result.add(source["project_id"])
    return result


async def content_projects(
    database: Any,
    collection: str,
    row: MongoDocument,
    session: Any = None,
    seen: frozenset[tuple[str, str]] = frozenset(),
) -> list[str]:
    key = (collection, str(row["_id"]))
    if key in seen:
        return sorted(direct_projects(row))
    seen = seen | {key}
    result = direct_projects({**row, "resource_project_ids": []})
    for reference in references(collection, row):
        target = await database[reference.target].find_one(
            {"$and": [reference.query, {"org_id": row["org_id"]}]},
            projection=REFERENCE_PROJECTION,
            session=session,
        )
        if target is None:
            raise ValueError("content reference is unavailable in this organization")
        result.update(direct_projects(target))
        if reference.target.startswith("content_"):
            result.update(await content_projects(database, reference.target, target, session, seen))
    return sorted(result)


async def content_envelope(
    database: Any,
    collection: str,
    row: MongoDocument,
    session: Any = None,
    seen: frozenset[tuple[str, str]] = frozenset(),
) -> tuple[list[str], list[str]]:
    """Read live source authority; cached project metadata is never authority.

    Re-evaluating the bounded graph also fences ACL changes after content was
    written. Private agent memories never become public via a content link.
    """
    key = (collection, str(row["_id"]))
    projects = direct_projects({**row, "resource_project_ids": []})
    clients: set[str] = set()
    if row.get("scope") in {"agent", "project_agent"} and row.get("client_id"):
        clients.add(str(row["client_id"]))
    if key in seen:
        return sorted(projects), sorted(clients)
    seen = seen | {key}
    for reference in references(collection, row):
        if reference.target.startswith("_"):
            raise ValueError("unclassified content reference")
        target = await database[reference.target].find_one(
            {"$and": [reference.query, {"org_id": row["org_id"]}]},
            projection=REFERENCE_PROJECTION,
            session=session,
        )
        if target is None:
            raise ValueError("content reference is unavailable in this organization")
        target_projects, target_clients = await content_envelope(
            database, reference.target, target, session, seen
        )
        projects.update(target_projects)
        clients.update(target_clients)
    return sorted(projects), sorted(clients)
