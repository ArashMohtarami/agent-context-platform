"""Opaque sessions with live user/member/policy checks and revocation fences."""

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any
from uuid import uuid4

from pymongo.asynchronous.client_session import AsyncClientSession

from app.identity.crypto import AuthBusy, AuthRejected, csrf, digest, random_token, valid_token
from app.identity.repository import IdentityStore
from app.models.common import utc_now

type Row = dict[str, Any]


@dataclass(frozen=True)
class IssuedSession:
    token: str = field(repr=False)
    row: Row = field(repr=False)

    @property
    def csrf_token(self) -> str:
        return csrf(self.token)


class BrowserSessions:
    def __init__(self, store: IdentityStore) -> None:
        self.store = store

    async def preauth(self) -> IssuedSession:
        return await self.issue(None, None, None, [], None, None)

    async def issue(
        self,
        user: Row | None,
        member: Row | None,
        org: Row | None,
        amr: list[str],
        password_at: datetime | None,
        mfa_at: datetime | None,
        *,
        expires_at: datetime | None = None,
        tx: AsyncClientSession | None = None,
    ) -> IssuedSession:
        now = utc_now()
        if user is not None:
            active = await self.store.list(
                "browser_sessions",
                {
                    "user_id": user["_id"],
                    "auth_epoch": user["auth_epoch"],
                    "kind": "human",
                    "revoked": False,
                    "expires_at": {"$gt": now},
                    "idle_expires_at": {"$gt": now},
                },
                tx,
            )
            if len(active) >= 20:
                raise AuthBusy("authentication is temporarily busy")
        token = random_token()
        expiry = expires_at or now + timedelta(hours=8 if user else 0, minutes=0 if user else 15)
        row = {
            "_id": digest("session", token),
            "public_id": "sess_" + uuid4().hex,
            "kind": "human" if user else "preauth",
            "user_id": user["_id"] if user else None,
            "org_id": member["org_id"] if member else None,
            "membership_id": member["_id"] if member else None,
            "membership_revision": member["revision"] if member else 0,
            "auth_epoch": user["auth_epoch"] if user else 0,
            "policy_version": org["policy_version"] if org else 0,
            "revoked": False,
            "created_at": now,
            "last_seen": now,
            "idle_expires_at": min(expiry, now + timedelta(minutes=30)),
            "expires_at": expiry,
            "password_at": password_at,
            "mfa_at": mfa_at,
            "amr": amr,
        }
        await self.store.insert("browser_sessions", row, tx)
        return IssuedSession(token, row)

    async def authenticate(
        self,
        token: str,
        *,
        kind: str = "human",
        tx: AsyncClientSession | None = None,
        touch: bool = True,
    ) -> Row:
        now = utc_now()
        if not valid_token(token):
            raise AuthRejected("authentication was not accepted")
        query = {
            "_id": digest("session", token),
            "kind": kind,
            "revoked": False,
            "idle_expires_at": {"$gt": now},
            "expires_at": {"$gt": now},
        }
        row = await self.store.find("browser_sessions", query, tx)
        if row is None:
            raise AuthRejected("authentication was not accepted")
        if kind == "human":
            await self.authority(row, tx)
        if touch:
            updated = await self.store.update(
                "browser_sessions",
                query,
                {
                    "$max": {
                        "last_seen": now,
                        "idle_expires_at": min(row["expires_at"], now + timedelta(minutes=30)),
                    }
                },
                tx,
            )
            if updated is None:
                raise AuthRejected("authentication was not accepted")
            row = updated
        return row

    async def authority(
        self, row: Row, tx: AsyncClientSession | None = None
    ) -> tuple[Row, Row, Row]:
        user = await self.store.find(
            "users",
            {"_id": row["user_id"], "status": "active", "auth_epoch": row["auth_epoch"]},
            tx,
        )
        member = await self.store.find(
            "organization_memberships",
            {
                "_id": row["membership_id"],
                "user_id": row["user_id"],
                "org_id": row["org_id"],
                "revision": row["membership_revision"],
                "status": "active",
                "verified_at": {"$ne": None},
            },
            tx,
        )
        org = await self.store.find(
            "organizations",
            {
                "org_id": row["org_id"],
                "status": "active",
                "policy_version": row["policy_version"],
                "auth_policy": {"$in": ["local_only", "sso_preferred"]},
            },
            tx,
        )
        if not user or not member or not org:
            raise AuthRejected("authentication was not accepted")
        return user, member, org

    async def fence(self, row: Row, tx: AsyncClientSession) -> tuple[Row, Row, Row]:
        user, member, org = await self.authority(row, tx)
        now = utc_now()
        if not await self.store.find(
            "browser_sessions",
            {
                "_id": row["_id"],
                "revoked": False,
                "kind": "human",
                "idle_expires_at": {"$gt": now},
                "expires_at": {"$gt": now},
            },
            tx,
        ):
            raise AuthRejected("authentication was not accepted")
        if not await self.store.update(
            "users",
            {"_id": user["_id"], "auth_epoch": row["auth_epoch"], "status": "active"},
            {"$inc": {"fence": 1}},
            tx,
        ):
            raise AuthRejected("authentication was not accepted")
        if not await self.store.update(
            "organization_memberships",
            {"_id": member["_id"], "revision": row["membership_revision"], "status": "active"},
            {"$inc": {"fence": 1}},
            tx,
        ):
            raise AuthRejected("authentication was not accepted")
        return user, member, org

    @staticmethod
    def recent(row: Row, *, mfa: bool = True) -> None:
        stamp = row["mfa_at"] if mfa else row["password_at"]
        if stamp is None or not timedelta(0) <= utc_now() - stamp <= timedelta(minutes=10):
            raise AuthRejected("recent strong authentication is required")

    async def rotate(
        self,
        row: Row,
        tx: AsyncClientSession,
        *,
        strong: bool = False,
        epoch: int | None = None,
        method: str = "totp",
    ) -> IssuedSession:
        user, member, org = await self.fence(row, tx)
        if not await self.store.update(
            "browser_sessions",
            {"_id": row["_id"], "revoked": False},
            {"$set": {"revoked": True}},
            tx,
        ):
            raise AuthRejected("authentication was not accepted")
        if epoch is not None:
            user["auth_epoch"] = epoch
        return await self.issue(
            user,
            member,
            org,
            ["password", method] if strong else row["amr"],
            row["password_at"],
            utc_now() if strong else row["mfa_at"],
            expires_at=row["expires_at"],
            tx=tx,
        )
