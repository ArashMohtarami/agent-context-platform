"""Opaque sessions with live user/member/policy checks and revocation fences."""

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any
from uuid import uuid4

from pymongo.asynchronous.client_session import AsyncClientSession

from app.identity.crypto import AuthBusy, AuthRejected, csrf, digest, random_token, valid_token
from app.identity.providers import Registry
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
        self.providers: Registry | None = None
        from app.security.policy.engine import PolicyEngine

        self.policy_engine: PolicyEngine | None = None

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
        authentication: Row | None = None,
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
        row: Row = {
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
        if self.providers:
            if user and authentication is None:
                identity = await self.store.find(
                    "user_identities",
                    {"user_id": user["_id"], "provider": "local", "verified_at": {"$ne": None}},
                    tx,
                )
                if not identity:
                    raise AuthRejected("authentication was not accepted")
                authentication = {
                    "method": "local",
                    "identity_id": identity["_id"],
                    "registration_id": None,
                    "registration_version": None,
                    "issuer": None,
                    "client_id": None,
                    "auth_time": password_at or now,
                    "valid_until": expiry,
                    "assurance_version": 0,
                    "acr": None,
                    "verified_amr": [],
                }
            row["authentication"] = authentication
            if authentication:
                row["expires_at"] = min(expiry, authentication["valid_until"])
                row["idle_expires_at"] = min(row["idle_expires_at"], row["expires_at"])
        if user and org:
            from app.identity.org_policy import check_policy

            await check_policy(self.store, self.providers, row, org, tx)
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
        if kind == "human" and tx is None:
            inputs = await self.store.read_human_session(query)
            row = inputs.row if inputs is not None else None
        else:
            inputs = None
            row = await self.store.find("browser_sessions", query, tx)
        if row is None:
            raise AuthRejected("authentication was not accepted")
        if kind == "human":
            if self.providers and not row.get("authentication"):
                raise AuthRejected("authentication was not accepted")
            if inputs is not None:
                await self._policy_authority(
                    row, inputs.user, inputs.member, inputs.org, tx, identity=inputs.identity
                )
            else:
                await self.authority(row, tx)
        # Authority/provider checks may take time. Expiry must still be current
        # at delivery, including refreshes that deliberately do not extend idle.
        now = utc_now()
        if row["expires_at"] <= now or row["idle_expires_at"] <= now:
            raise AuthRejected("authentication was not accepted")
        if touch:
            query = {**query, "idle_expires_at": {"$gt": now}, "expires_at": {"$gt": now}}
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
        user, member, org = await self.store.session_authority(row, tx)
        return await self._policy_authority(row, user, member, org, tx)

    async def _policy_authority(
        self,
        row: Row,
        user: Row | None,
        member: Row | None,
        org: Row | None,
        tx: AsyncClientSession | None,
        *,
        identity: Row | None = None,
    ) -> tuple[Row, Row, Row]:
        if not user or not member or not org:
            raise AuthRejected("authentication was not accepted")
        from app.identity.org_policy import check_policy

        await check_policy(self.store, self.providers, row, org, tx, identity=identity)
        return user, member, org

    async def fence(self, row: Row, tx: AsyncClientSession) -> tuple[Row, Row, Row]:
        now = utc_now()
        inputs = await self.store.read_human_session(
            {
                "_id": row["_id"],
                "kind": "human",
                "revoked": False,
                "user_id": row["user_id"],
                "org_id": row["org_id"],
                "membership_id": row["membership_id"],
                "membership_revision": row["membership_revision"],
                "auth_epoch": row["auth_epoch"],
                "policy_version": row["policy_version"],
                "expires_at": {"$gt": now},
                "idle_expires_at": {"$gt": now},
            },
            tx,
        )
        if inputs is None:
            raise AuthRejected("authentication was not accepted")
        user, member, org = await self._policy_authority(
            row, inputs.user, inputs.member, inputs.org, tx, identity=inputs.identity
        )
        now = utc_now()
        if self.policy_engine:
            anchor = await self.store.find(
                "policy_anchors", {"_id": row["org_id"], "status": "active"}, tx
            )
            if anchor:
                await self.policy_engine.fence(anchor, tx)
            now = utc_now()
            if not await self.store.update(
                "browser_sessions",
                {
                    "_id": row["_id"],
                    "kind": "human",
                    "revoked": False,
                    "expires_at": {"$gt": now},
                    "idle_expires_at": {"$gt": now},
                },
                {"$inc": {"policy_fence": 1}},
                tx,
            ):
                raise AuthRejected("authentication was not accepted")
        elif not await self.store.find(
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
            list(dict.fromkeys([*row["amr"], method])) if strong else row["amr"],
            row["password_at"],
            utc_now() if strong else row["mfa_at"],
            expires_at=row["expires_at"],
            authentication=row.get("authentication"),
            tx=tx,
        )
