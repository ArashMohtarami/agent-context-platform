"""Narrow private identity store; never exposed by tenant corpus repositories."""

from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, cast

from pymongo import ReturnDocument
from pymongo.asynchronous.client_session import AsyncClientSession
from pymongo.asynchronous.collection import AsyncCollection
from pymongo.asynchronous.database import AsyncDatabase
from pymongo.errors import DuplicateKeyError

from app.db.client_types import MongoDocument
from app.managed_agents.inventory import COLLECTION_OWNERSHIP as MACHINE_COLLECTION_OWNERSHIP
from app.management.inventory import COLLECTION_OWNERSHIP as MANAGEMENT_COLLECTION_OWNERSHIP
from app.security.policy.inventory import COLLECTION_OWNERSHIP as POLICY_COLLECTION_OWNERSHIP

type Row = dict[str, Any]


@dataclass(frozen=True)
class SessionRead:
    """Private inputs from one fresh session-bound read, never a directory DTO."""

    row: Row = field(repr=False)
    user: Row = field(repr=False)
    member: Row = field(repr=False)
    org: Row = field(repr=False)
    identity: Row | None = field(repr=False)


CONTROL_COLLECTIONS = frozenset(
    {
        "_local_identity_control",
        "_sso_identity_control",
        "_tenant_control",
        "_management_policy_control",
        "_human_management_control",
        "_managed_agents_control",
    }
)

LOCAL_COLLECTION_OWNERSHIP = {
    "users": "private global human identity",
    "user_identities": "private global human identity",
    "local_credentials": "private global human identity",
    "mfa_factors": "private global human identity",
    "browser_sessions": "private global human identity",
    "auth_challenges": "private global human identity",
    "auth_throttles": "private global human security state",
    "identity_audit_events": "private global human security history",
    "organization_memberships": "tenant owned; self, authorized management, or operator",
    "_local_identity_control": "installation metadata",
}
SSO_COLLECTION_OWNERSHIP = {
    "organization_oidc": "tenant owned; explicit installation operator configuration",
    "oidc_transactions": "private global human security state; browser and purpose bound",
    "organization_break_glass": "tenant owned; explicit time bounded operator exception",
    "_sso_identity_control": "installation metadata",
}
COLLECTION_OWNERSHIP = {
    **LOCAL_COLLECTION_OWNERSHIP,
    **SSO_COLLECTION_OWNERSHIP,
    **POLICY_COLLECTION_OWNERSHIP,
    **MANAGEMENT_COLLECTION_OWNERSHIP,
    **MACHINE_COLLECTION_OWNERSHIP,
}


class IdentityStore:
    def __init__(self, database: AsyncDatabase[MongoDocument]) -> None:
        self.database = database

    def _collection(self, name: str) -> AsyncCollection[Row]:
        if name not in COLLECTION_OWNERSHIP and name not in {"organizations", "_tenant_control"}:
            raise ValueError("unclassified identity collection")
        return cast(AsyncCollection[Row], self.database[name])

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[AsyncClientSession]:
        async with self.database.client.start_session() as session:
            async with await session.start_transaction():
                yield session

    async def find(
        self, name: str, query: Row, session: AsyncClientSession | None = None
    ) -> Row | None:
        return await self._collection(name).find_one(query, session=session)

    async def insert(self, name: str, row: Row, session: AsyncClientSession | None = None) -> None:
        await self._collection(name).insert_one(row, session=session)

    async def update(
        self, name: str, query: Row, change: Row, session: AsyncClientSession | None = None
    ) -> Row | None:
        if name == "access_decisions":
            raise ValueError("access decisions are append only")
        return await self._collection(name).find_one_and_update(
            query, change, session=session, return_document=ReturnDocument.AFTER
        )

    async def list(
        self, name: str, query: Row, session: AsyncClientSession | None = None
    ) -> list[Row]:
        return await self._collection(name).find(query, session=session).limit(101).to_list(101)

    async def control_flags(self, checks: Sequence[tuple[str, Row]]) -> dict[str, bool]:
        """Read current installation markers together; retain no rows or cache.

        All lookups are fixed installation metadata, never tenant/user joins.
        Project only marker existence, excluding cursor and encryption secrets.
        """
        if (
            not 1 <= len(checks) <= len(CONTROL_COLLECTIONS)
            or len({name for name, _ in checks}) != len(checks)
            or any(
                name not in CONTROL_COLLECTIONS
                or query.get("_id", "installation") != "installation"
                for name, query in checks
            )
        ):
            raise ValueError("unclassified installation control query")

        def stages(query: Row) -> list[Row]:
            return [
                {"$match": {**query, "_id": "installation"}},
                {"$limit": 1},
                {"$project": {"_id": 1}},
            ]

        first, query = checks[0]
        pipeline = stages(query)
        for index, (name, query) in enumerate(checks[1:]):
            pipeline.append(
                {"$lookup": {"from": name, "pipeline": stages(query), "as": f"control_{index}"}}
            )
        rows = await (await self._collection(first).aggregate(pipeline, maxTimeMS=3000)).to_list(1)
        return {
            name: bool(rows) and (index == 0 or len(rows[0].get(f"control_{index - 1}", [])) == 1)
            for index, (name, _) in enumerate(checks)
        }

    async def session_authority(
        self, row: Row, session: AsyncClientSession | None = None
    ) -> tuple[Row | None, Row | None, Row | None]:
        """Read current user/member/org with exact persisted session bindings.

        The global user join is reachable only through this session's verified
        membership. No email matching, directory DTO, retained rows or authority
        cache. Mutations retain sequential reads on their transaction session.
        """
        user_query: Row = {
            "_id": row["user_id"],
            "status": "active",
            "auth_epoch": row["auth_epoch"],
        }
        member_query: Row = {
            "_id": row["membership_id"],
            "user_id": row["user_id"],
            "org_id": row["org_id"],
            "revision": row["membership_revision"],
            "status": "active",
            "verified_at": {"$ne": None},
        }
        org_query: Row = {
            "org_id": row["org_id"],
            "status": "active",
            "policy_version": row["policy_version"],
        }
        if session is not None:
            user = await self.find("users", user_query, session)
            member = await self.find("organization_memberships", member_query, session)
            org = await self.find("organizations", org_query, session)
            return user, member, org
        pipeline: list[Row] = [{"$match": member_query}, {"$limit": 1}]
        for name, query, alias in (
            ("users", user_query, "session_user"),
            ("organizations", org_query, "session_org"),
        ):
            pipeline.append(
                {
                    "$lookup": {
                        "from": name,
                        "pipeline": [{"$match": query}, {"$limit": 1}],
                        "as": alias,
                    }
                }
            )
        rows = await (
            await self._collection("organization_memberships").aggregate(pipeline, maxTimeMS=3000)
        ).to_list(1)
        if not rows:
            return None, None, None
        member = rows[0]
        users = member.pop("session_user")
        orgs = member.pop("session_org")
        return users[0] if users else None, member, orgs[0] if orgs else None

    async def read_human_session(
        self, query: Row, session: AsyncClientSession | None = None
    ) -> SessionRead | None:
        """Join only a hashed session's exact current authority and login identity.

        The verified same-org membership must exist before global human rows
        can be read. Every joined collection is bounded to one row; no result
        or authorization decision is retained between requests. Transactions
        perform one operation on their existing session, with no parallel I/O.
        """
        if query.get("kind") != "human" or not isinstance(query.get("_id"), str):
            raise ValueError("an exact human session query is required")
        pipeline: list[Row] = [{"$match": query}, {"$limit": 1}]

        def join(
            name: str, alias: str, variables: Row, pairs: Row, *, required: bool = True
        ) -> None:
            predicate: Row = {
                "$expr": {"$and": [{"$eq": ["$" + key, value]} for key, value in pairs.items()]}
            }
            if name in {"users", "organization_memberships", "organizations"}:
                predicate["status"] = "active"
            if name in {"organization_memberships", "user_identities"}:
                predicate["verified_at"] = {"$ne": None}
            stages: list[Row] = [{"$match": predicate}, {"$limit": 1}]
            if name == "user_identities":
                # No login subject/email or unrelated identity attributes leave
                # this lookup, even internally in the authentication boundary.
                stages.append(
                    {"$project": {"_id": 1, "user_id": 1, "provider": 1, "verified_at": 1}}
                )
            pipeline.append(
                {"$lookup": {"from": name, "let": variables, "pipeline": stages, "as": alias}}
            )
            if required:
                pipeline.append({"$match": {f"{alias}.0._id": {"$exists": True}}})

        join(
            "organization_memberships",
            "session_member",
            {
                "id": "$membership_id",
                "user": "$user_id",
                "org": "$org_id",
                "revision": "$membership_revision",
            },
            {"_id": "$$id", "user_id": "$$user", "org_id": "$$org", "revision": "$$revision"},
        )
        join(
            "users",
            "session_user",
            {"user": {"$arrayElemAt": ["$session_member.user_id", 0]}, "epoch": "$auth_epoch"},
            {"_id": "$$user", "auth_epoch": "$$epoch"},
        )
        join(
            "organizations",
            "session_org",
            {"org": "$org_id", "version": "$policy_version"},
            {"org_id": "$$org", "policy_version": "$$version"},
        )
        join(
            "user_identities",
            "session_identity",
            {
                "id": "$authentication.identity_id",
                "user": {"$arrayElemAt": ["$session_member.user_id", 0]},
                "provider": {
                    "$cond": [
                        {"$eq": ["$authentication.method", "oidc"]},
                        "$authentication.issuer",
                        "local",
                    ]
                },
            },
            {"_id": "$$id", "user_id": "$$user", "provider": "$$provider"},
            required=False,
        )
        rows = await (
            await self._collection("browser_sessions").aggregate(
                pipeline, session=session, maxTimeMS=3000, allowDiskUse=False
            )
        ).to_list(1)
        if not rows:
            return None
        row = rows[0]
        member, user, org, identity = (
            row.pop(name)
            for name in ("session_member", "session_user", "session_org", "session_identity")
        )
        return SessionRead(row, user[0], member[0], org[0], identity[0] if identity else None)

    async def throttle_bucket(self, key: str, expires_at: datetime) -> Row | None:
        collection = self._collection("auth_throttles")
        try:
            return await collection.find_one_and_update(
                {"_id": key},
                {"$inc": {"count": 1}, "$setOnInsert": {"expires_at": expires_at}},
                upsert=True,
                return_document=ReturnDocument.AFTER,
            )
        except DuplicateKeyError:
            # A concurrent initial upsert won the unique key. The refused
            # insert did not increment; count this request exactly once.
            return await collection.find_one_and_update(
                {"_id": key}, {"$inc": {"count": 1}}, return_document=ReturnDocument.AFTER
            )
