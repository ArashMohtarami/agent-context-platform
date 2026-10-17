"""Maintained TOTP implementation with persisted counter and recovery-code CAS."""

import hmac
from datetime import timedelta
from typing import Any
from uuid import uuid4

import pyotp
from pymongo.asynchronous.client_session import AsyncClientSession

from app.identity.crypto import AuthRejected, SeedVault, digest, random_token
from app.identity.repository import IdentityStore
from app.models.common import utc_now

type Row = dict[str, Any]


class LocalMFA:
    def __init__(self, store: IdentityStore, vault: SeedVault) -> None:
        self.store, self.vault = store, vault

    async def begin(self, user_id: str, tx: AsyncClientSession) -> tuple[str, str]:
        current = await self.store.find("mfa_factors", {"_id": user_id}, tx)
        if current and current["status"] == "active":
            raise AuthRejected("remove the active factor with recent MFA before replacing it")
        seed, factor_id = pyotp.random_base32(), "factor_" + uuid4().hex
        now = utc_now()
        row: Row = {
            "_id": user_id,
            "user_id": user_id,
            "factor_id": factor_id,
            "status": "pending",
            "encrypted_seed": self.vault.encrypt(seed, user_id, factor_id),
            "recovery_hashes": [],
            "last_step": -1,
            "revision": (current["revision"] + 1) if current else 1,
            "expires_at": now + timedelta(minutes=10),
            "created_at": now,
        }
        if current:
            changes = {key: value for key, value in row.items() if key != "_id"}
            if not await self.store.update(
                "mfa_factors",
                {"_id": user_id, "revision": current["revision"]},
                {"$set": changes},
                tx,
            ):
                raise AuthRejected("authentication was not accepted")
        else:
            await self.store.insert("mfa_factors", row, tx)
        return seed, pyotp.TOTP(seed, digits=6, interval=30).provisioning_uri(
            user_id, issuer_name="Agent Context Platform"
        )

    async def consume(
        self,
        user_id: str,
        code: str,
        tx: AsyncClientSession,
        *,
        recovery: bool = False,
        pending: bool = False,
    ) -> Row:
        factor = await self.store.find(
            "mfa_factors", {"_id": user_id, "status": "pending" if pending else "active"}, tx
        )
        if not factor or (pending and factor["expires_at"] <= utc_now()):
            raise AuthRejected("authentication was not accepted")
        query: Row = {"_id": user_id, "revision": factor["revision"], "status": factor["status"]}
        change: Row = {"$inc": {"revision": 1}}
        if recovery and not pending:
            hashed = digest("recovery:" + user_id, code)
            query["recovery_hashes"] = hashed
            change["$pull"] = {"recovery_hashes": hashed}
        else:
            if len(code) != 6 or not code.isascii() or not code.isdigit():
                raise AuthRejected("authentication was not accepted")
            seed = self.vault.decrypt(factor["encrypted_seed"], user_id, factor["factor_id"])
            totp = pyotp.TOTP(seed, digits=6, interval=30)
            current = int(utc_now().timestamp()) // 30
            accepted = next(
                (
                    step
                    for step in (current, current - 1, current + 1)
                    if step > factor["last_step"] and hmac.compare_digest(totp.at(step * 30), code)
                ),
                None,
            )
            if accepted is None:
                raise AuthRejected("authentication was not accepted")
            query["last_step"] = {"$lt": accepted}
            change["$set"] = {"last_step": accepted}
        updated = await self.store.update("mfa_factors", query, change, tx)
        if updated is None:
            raise AuthRejected("authentication was not accepted")
        return updated

    async def recovery_codes(
        self, user_id: str, tx: AsyncClientSession, *, activate: bool = False
    ) -> list[str]:
        codes = [random_token() for _ in range(10)]
        changes: Row = {"recovery_hashes": [digest("recovery:" + user_id, code) for code in codes]}
        if activate:
            changes.update(status="active", expires_at=None)
        if not await self.store.update(
            "mfa_factors", {"_id": user_id}, {"$set": changes, "$inc": {"revision": 1}}, tx
        ):
            raise AuthRejected("authentication was not accepted")
        return codes
