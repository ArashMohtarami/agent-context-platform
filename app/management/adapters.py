"""Bounded session-console projections over the existing governance engine.

No identity discovery, new permission model, or browser-to-bearer adapter.
"""

import re
from datetime import timedelta
from typing import Any

from app.identity.repository import Row
from app.managed_agents.service import ManagedAgents, authority
from app.management.boundaries import has, validate_parent
from app.management.directory import Directory
from app.models.common import utc_now
from app.security.policy.catalog import HUMAN_ROLES, can_appoint
from app.security.policy.engine import PolicyEngine
from app.security.policy.evaluation import PolicyDenied, active_chain
from app.security.policy.models import Issue

CLIENT_FIELDS = (
    "org_id",
    "client_id",
    "subject_kind",
    "subject_id",
    "sponsor_id",
    "team_id",
    "parent_id",
    "parent_generation",
    "issuer_id",
    "tuples",
    "scopes",
    "profile_id",
    "profile_revision",
    "revision",
    "status",
    "expires_at",
)
SERVICE_FIELDS = (
    "org_id",
    "service_account_id",
    "name",
    "custodian_id",
    "team_id",
    "revision",
    "status",
    "expires_at",
)
TEMPLATE_FIELDS = ("profile_id", "name", "scopes", "status", "revision", "rate_limit_profile")
SESSION_FIELDS = (
    "session_id",
    "client_id",
    "client_type",
    "project_id",
    "started_at",
    "last_seen_at",
    "ended_at",
    "status",
    "revision",
)
BINDING_FIELDS = ("binding_id", "project_id", "binding_type", "value", "revision")
PARENT_FIELDS = (
    "_id",
    "generation",
    "revision",
    "tuples",
    "ceiling",
    "targets",
    "allowed_roles",
    "remaining_depth",
    "expires_at",
)


def client_boundary(snapshot: Row, *, service: bool = False) -> Row:
    """Prefilter by current action-specific boundary, before counts/pagination."""
    org_id, actor = snapshot["anchor"]["org_id"], snapshot["member"]["user_id"]
    clauses: list[Row] = []
    if has(snapshot, "clients:read:org", "organization", org_id):
        clauses.append({})
    if has(snapshot, "clients:read:own", "member", actor):
        clauses.append({"custodian_id" if service else "sponsor_id": actor})
    if not service:
        ids = sorted(
            {
                i
                for g in snapshot["tuples"]
                if g.action in {"clients:read:org", "clients:read:own"}
                and g.resource_kind == "client"
                and g.selector == "include"
                for i in g.ids
            }
        )
        if ids:
            clauses.append({"client_id": {"$in": ids}})
    teams = [
        t
        for t, row in snapshot["teams"].items()
        if row["status"] == "active" and has(snapshot, "clients:read:team", "team", t)
    ]
    if teams:
        # A changed/revoked sponsor edge cannot expose metadata through a list.
        for t in teams:
            users = [
                u
                for (team, u), link in snapshot["team_links"].items()
                if team == t and link["status"] == "active"
            ]
            clauses.append(
                {
                    "team_id": t,
                    "team_generation": snapshot["teams"][t]["generation"],
                    "custodian_id" if service else "sponsor_id": {"$in": users},
                }
            )
    return {"org_id": org_id, "$or": clauses} if clauses else {"org_id": org_id, "_id": {"$in": []}}


def project_boundary(snapshot: Row, action: str) -> Row:
    choices = [
        g
        for g in snapshot["tuples"]
        if g.action == action and g.resource_kind in {"project", "project_directory"}
    ]
    if any(g.selector == "all_in_org" for g in choices):
        return {}
    return {
        "project_id": {
            "$in": sorted({i for g in choices if g.selector == "include" for i in g.ids})
        }
    }


def template_authority(snapshot: Row) -> None:
    if not any(
        g.action.startswith("clients:manage:") and g.selector != "none" for g in snapshot["tuples"]
    ):
        raise PolicyDenied("template metadata is outside current client management authority")


class ManagementAdapters:
    def __init__(self, engine: PolicyEngine) -> None:
        self.engine, self.store = engine, engine.store
        self.db: Any = engine.store.database
        self.directory = Directory(engine)

    async def context(self, org_id: str, actor: str) -> Row:
        snapshot = await self.engine.snapshot(org_id, actor)
        parents: list[Row] = []
        requests: list[Row] = []
        for row in snapshot["assignments"].values():
            try:
                active_chain(
                    row,
                    snapshot["assignments"],
                    snapshot["members"],
                    snapshot["users"],
                    utc_now(),
                    snapshot["teams"],
                    snapshot["team_links"],
                )
            except PolicyDenied:
                continue
            if row["subject_id"] == actor:
                parents.append(
                    {**{k: row[k] for k in PARENT_FIELDS}, "team_id": row.get("team_id")}
                )
            else:
                # Expose only a usable request handle, never somebody else's ceiling/lineage.
                expiry = min(row["expires_at"], utc_now() + timedelta(days=1))
                request = Issue(
                    subject_id=actor,
                    role=snapshot["member"]["role"],
                    parent_id=row["_id"],
                    expected_parent_generation=row["generation"],
                    expires_at=expiry,
                    team_id=row.get("team_id"),
                    expected_team_generation=row.get("team_generation"),
                )
                try:
                    issuer = {**snapshot, "member": snapshot["members"][row["subject_id"]]}
                    validate_parent(issuer, request)
                except PolicyDenied:
                    continue
                requests.append(
                    {
                        "id": row["_id"],
                        "generation": row["generation"],
                        "issuer_id": row["subject_id"],
                        "team_id": row.get("team_id"),
                        "expires_at": expiry,
                    }
                )
        teams = [
            {k: row[k] for k in ("team_id", "name", "generation", "revision", "status")}
            for t, row in snapshot["teams"].items()
            if has(snapshot, "teams:read", "organization", org_id)
            or any(
                has(snapshot, a, "team", t)
                for a in ("teams:read", "grants:delegate", "clients:manage:team")
            )
            or snapshot["team_links"].get((t, actor), {}).get("status") == "active"
        ]
        return {
            "policy_revision": snapshot["anchor"]["revision"],
            "parents": parents,
            "request_parents": requests,
            "teams": teams,
            "roles": [r for r in HUMAN_ROLES if can_appoint(snapshot["member"]["role"], r)],
        }

    async def page(
        self,
        org_id: str,
        actor: str,
        kind: str,
        *,
        prefix: str = "",
        status: str | None = None,
        limit: int = 25,
        cursor: str | None = None,
        client_id: str | None = None,
    ) -> Row:
        if not 1 <= limit <= 100 or len(prefix) > 128:
            raise PolicyDenied("bounded console query is required")
        snapshot = await self.engine.snapshot(org_id, actor)
        query: Row = {"org_id": org_id}
        fields: tuple[str, ...]
        if kind == "clients":
            collection, key, fields = "managed_clients", "client_id", CLIENT_FIELDS
            query = client_boundary(snapshot)
        elif kind == "services":
            collection, key, fields = "service_accounts", "service_account_id", SERVICE_FIELDS
            query = client_boundary(snapshot, service=True)
        elif kind == "templates":
            template_authority(snapshot)
            collection, key, fields = "client_profiles", "profile_id", TEMPLATE_FIELDS
        elif kind == "sessions":
            if not client_id:
                raise PolicyDenied("explicit managed client is required")
            row = await ManagedAgents(self.engine).client(org_id, client_id)
            authority(snapshot, "clients:read", row)
            collection, key, fields = "agent_sessions", "session_id", SESSION_FIELDS
            query.update(client_id=client_id, **project_boundary(snapshot, "projects:read"))
        elif kind == "bindings":
            collection, key, fields = "project_bindings", "binding_id", BINDING_FIELDS
            query.update(project_boundary(snapshot, "projects:read"))
        else:
            raise PolicyDenied("unknown console projection")
        if prefix:
            query[key] = {"$regex": "^" + re.escape(prefix), "$options": "i"}
        if status:
            query["status"] = status
        return await self.directory.page(collection, query, key, fields, snapshot, limit, cursor)

    async def template_impact(self, org_id: str, actor: str, profile_id: str) -> Row:
        snapshot = await self.engine.snapshot(org_id, actor)
        self.engine.require(snapshot, "clients:manage:org", "organization", org_id)
        row = await self.db["client_profiles"].find_one(
            {"org_id": org_id, "profile_id": profile_id}
        )
        if not row:
            raise PolicyDenied("template is unavailable")
        count = await self.db["agent_clients"].count_documents(
            {"org_id": org_id, "profile_id": profile_id}
        )
        return {
            "profile_id": profile_id,
            "revision": row["revision"],
            "assigned_client_count": count,
            "assigned_clients_changed": False,
            "rate_limit_profile_enforced": False,
        }

    async def end_session(
        self, org_id: str, actor: str, client_id: str, session_id: str, expected: int
    ) -> Row:
        from app.security.policy.evaluation import PolicyConflict
        from app.security.policy.governance import Governance

        async def operation(snapshot: Row, tx: Any) -> Row:
            client = await ManagedAgents(self.engine).client(org_id, client_id, tx)
            authority(snapshot, "clients:manage", client)
            query = {
                "org_id": org_id,
                "client_id": client_id,
                "session_id": session_id,
                "revision": expected,
                "status": "active",
            }
            row = await self.db["agent_sessions"].find_one(query, session=tx)
            if not row:
                raise PolicyConflict("machine session changed")
            if row["project_id"]:
                require_project(snapshot, "projects:write", row["project_id"])
            now = max(utc_now(), row["last_seen_at"])
            result = await self.db["agent_sessions"].update_one(
                query,
                {
                    "$set": {
                        "status": "ended",
                        "ended_at": now,
                        "last_seen_at": now,
                        "updated_at": now,
                    },
                    "$inc": {"revision": 1},
                },
                session=tx,
            )
            if result.modified_count != 1:
                raise PolicyConflict("machine session changed")
            await self.engine.audit(org_id, actor, "machine_session_ended", session_id, tx)
            return {"session_id": session_id, "status": "ended", "revision": expected + 1}

        return await Governance(self.engine).mutate(org_id, actor, operation)

    async def put_binding(self, org_id: str, actor: str, identifier: str, command: Row) -> Row:
        from app.models.agent import ProjectBindingDocument
        from app.security.policy.evaluation import PolicyConflict
        from app.security.policy.governance import Governance

        async def operation(snapshot: Row, tx: Any) -> Row:
            db = self.db
            old = await db["project_bindings"].find_one(
                {"org_id": org_id, "binding_id": identifier}, session=tx
            )
            if (old["revision"] if old else 0) != command["expected_revision"]:
                raise PolicyConflict("binding revision changed")
            for project_id in {command["project_id"], *((old["project_id"],) if old else ())}:
                require_project(snapshot, "projects:write", project_id)
                if not await db["projects"].find_one(
                    {"org_id": org_id, "project_id": project_id}, session=tx
                ):
                    raise PolicyDenied("project is unavailable")
            values = {
                "binding_id": identifier,
                "project_id": command["project_id"],
                "binding_type": command["binding_type"],
                "value": command["value"],
                "repository_locator": None,
            }
            document = (
                ProjectBindingDocument.model_validate(
                    {**old, **values}, context={"from_storage": True}
                )
                if old
                else ProjectBindingDocument(**values)
            )
            duplicate: Row = {"org_id": org_id, "binding_id": {"$ne": identifier}}
            if document.repository_locator:
                duplicate["repository_locator"] = document.repository_locator
            else:
                duplicate.update(binding_type=document.binding_type, value=document.value)
            if await db["project_bindings"].find_one(duplicate, session=tx):
                raise PolicyConflict("binding already exists")
            row = document.model_dump(by_alias=True)
            row.update(revision=command["expected_revision"] + 1, updated_at=utc_now())
            if old:
                await db["project_bindings"].replace_one(
                    {"_id": old["_id"], "org_id": org_id, "revision": old["revision"]},
                    row,
                    session=tx,
                )
            else:
                await db["project_bindings"].insert_one(row, session=tx)
            await self.engine.audit(org_id, actor, "project_binding_updated", identifier, tx)
            return {k: row[k] for k in BINDING_FIELDS}

        return await Governance(self.engine).mutate(org_id, actor, operation)

    async def delete_binding(self, org_id: str, actor: str, identifier: str, expected: int) -> Row:
        from app.security.policy.evaluation import PolicyConflict
        from app.security.policy.governance import Governance

        async def operation(snapshot: Row, tx: Any) -> Row:
            collection = self.db["project_bindings"]
            query = {"org_id": org_id, "binding_id": identifier, "revision": expected}
            row = await collection.find_one(query, session=tx)
            if not row:
                raise PolicyConflict("binding revision changed")
            require_project(snapshot, "projects:write", row["project_id"])
            await collection.delete_one(query, session=tx)
            await self.engine.audit(org_id, actor, "project_binding_deleted", identifier, tx)
            return {"binding_id": identifier, "deleted": True}

        return await Governance(self.engine).mutate(org_id, actor, operation)


def require_project(snapshot: Row, action: str, identifier: str) -> None:
    if not any(
        has(snapshot, action, kind, identifier) for kind in ("project", "project_directory")
    ):
        raise PolicyDenied("project is outside the current action boundary")
