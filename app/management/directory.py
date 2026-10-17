"""Organization-only directory queries, filtered before pagination and projection."""

import re

from app.identity.repository import Row
from app.management.boundaries import has, permits_member
from app.models.common import InvalidCursorError
from app.security.policy.engine import PolicyEngine
from app.security.policy.evaluation import PolicyConflict, PolicyDenied
from app.security.tenant_cursor import binding, decode_directory_cursor, encode_directory_cursor

MEMBER_FIELDS = ("org_id", "user_id", "display_name", "role", "status", "revision")
TEAM_FIELDS = ("org_id", "team_id", "name", "status", "revision", "generation")


def member_dto(row: Row) -> Row:
    return {key: row[key] for key in MEMBER_FIELDS}


def member_query(snapshot: Row, action: str = "members:read") -> Row:
    org_id = snapshot["anchor"]["org_id"]
    direct = (
        [g for g in snapshot["tuples"] if g.action == action and g.resource_kind == "member"]
        if snapshot["member"]["role"] != "team_lead"
        else []
    )
    if any(g.selector == "all_in_org" for g in direct):
        return {"org_id": org_id}
    identifiers = {i for g in direct if g.selector == "include" for i in g.ids}
    identifiers.update(
        user_id
        for (_, user_id) in snapshot["team_links"]
        if permits_member(snapshot, action, user_id)
    )
    return {"org_id": org_id, "user_id": {"$in": sorted(identifiers)}}


class Directory:
    def __init__(self, engine: PolicyEngine) -> None:
        self.engine, self.store = engine, engine.store

    async def members(
        self,
        org_id: str,
        actor: str,
        *,
        prefix: str = "",
        limit: int = 50,
        cursor: str | None = None,
    ) -> Row:
        if not 1 <= limit <= 100 or len(prefix) > 128:
            raise PolicyDenied("bounded directory query is required")
        snapshot = await self.engine.snapshot(org_id, actor)
        query = member_query(snapshot)
        if prefix:
            query["display_name"] = {"$regex": "^" + re.escape(prefix), "$options": "i"}
        return await self.page(
            "organization_memberships", query, "user_id", MEMBER_FIELDS, snapshot, limit, cursor
        )

    async def teams(
        self, org_id: str, actor: str, *, limit: int = 50, cursor: str | None = None
    ) -> Row:
        if not 1 <= limit <= 100:
            raise PolicyDenied("bounded directory query is required")
        snapshot = await self.engine.snapshot(org_id, actor)
        query: Row = {"org_id": org_id}
        if snapshot["member"]["role"] == "team_lead" or not has(
            snapshot, "teams:read", "organization", org_id
        ):
            query["team_id"] = {
                "$in": sorted(
                    team_id
                    for team_id in snapshot["teams"]
                    if has(snapshot, "teams:read", "team", team_id)
                )
            }
        return await self.page("teams", query, "team_id", TEAM_FIELDS, snapshot, limit, cursor)

    async def page(
        self,
        name: str,
        query: Row,
        key: str,
        fields: tuple[str, ...],
        snapshot: Row,
        limit: int,
        cursor: str | None,
    ) -> Row:
        scope = binding(
            name,
            {
                "query": query,
                "revision": snapshot["anchor"]["revision"],
                "subject": snapshot["member"]["user_id"],
            },
        )
        try:
            selected: Row = (
                {"$and": [query, {key: {"$gt": decode_directory_cursor(cursor, scope)}}]}
                if cursor
                else query
            )
        except InvalidCursorError:
            raise PolicyConflict("directory cursor changed; start a fresh page") from None
        rows = (
            await self.store._collection(name)
            .find(selected, {field: 1 for field in fields})
            .sort(key, 1)
            .limit(limit + 1)
            .to_list(limit + 1)
        )
        return {
            "items": [{field: row[field] for field in fields} for row in rows[:limit]],
            "next_cursor": encode_directory_cursor(rows[limit - 1][key], scope)
            if len(rows) > limit
            else None,
            "policy_revision": snapshot["anchor"]["revision"],
        }

    async def effective_access(self, org_id: str, actor: str, target: str) -> Row:
        observer = await self.engine.snapshot(org_id, actor)
        if actor != target:
            self.engine.require(observer, "grants:read", "member", target)
        subject = await self.engine.snapshot(org_id, target)
        # A delegated lead must not learn another team's assignments or ancestor IDs.
        allowed_teams = {t for t in observer["teams"] if has(observer, "grants:read", "team", t)}
        explanations = [
            e
            for e in subject["explanations"]
            if actor == target
            or observer["member"]["role"] != "team_lead"
            or subject["assignments"][e["assignment_id"]].get("team_id") in allowed_teams
        ]
        if actor != target:
            explanations = [{k: v for k, v in e.items() if k != "lineage"} for e in explanations]
        tuples = [g for e in explanations if e["effective"] for g in e["tuples"]]
        return {
            "org_id": org_id,
            "subject_id": target,
            "policy_revision": subject["anchor"]["revision"],
            "tuples": tuples,
            "assignments": explanations,
            "role_has_corpus_bypass": False,
        }

    async def assignments(self, org_id: str, actor: str, target: str) -> Row:
        snapshot = await self.engine.snapshot(org_id, actor)
        if actor != target:
            self.engine.require(snapshot, "grants:read", "member", target)
        rows = [row for row in snapshot["assignments"].values() if row["subject_id"] == target]
        if snapshot["member"]["role"] == "team_lead" and actor != target:
            rows = [
                row
                for row in rows
                if row.get("team_id") and has(snapshot, "grants:read", "team", row["team_id"])
            ]
        return {"org_id": org_id, "subject_id": target, "assignments": rows}

    async def team_members(
        self, org_id: str, actor: str, team_id: str, *, limit: int = 50, cursor: str | None = None
    ) -> Row:
        if not 1 <= limit <= 100:
            raise PolicyDenied("bounded directory query is required")
        snapshot = await self.engine.snapshot(org_id, actor)
        team = snapshot["teams"].get(team_id)
        if not team:
            raise PolicyDenied("team is unavailable")
        if not has(snapshot, "members:read", "team", team_id):
            if snapshot["member"]["role"] == "team_lead":
                raise PolicyDenied("team is outside delegated authority")
            self.engine.require(snapshot, "teams:read", "organization", org_id)
        allowed = member_query(snapshot)
        users = [u for (t, u) in snapshot["team_links"] if t == team_id]
        query: Row = {"$and": [allowed, {"user_id": {"$in": users}}]}
        result = await self.page(
            "organization_memberships", query, "user_id", MEMBER_FIELDS, snapshot, limit, cursor
        )

        for row in result["items"]:
            link = snapshot["team_links"][(team_id, row["user_id"])]
            row.update(
                team_id=team_id,
                team_membership_status=link["status"],
                team_membership_revision=link["revision"],
            )
        return result
