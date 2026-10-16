"""Explicit self-identity exceptions; no identity/email/claim grants corpus access."""

from datetime import timedelta
from typing import Any
from urllib.parse import urlencode
from uuid import uuid4

from app.identity.crypto import AuthRejected, SeedVault, digest, random_token, valid_token
from app.identity.oidc import OIDCClient, authentication, provider_mfa
from app.identity.org_policy import approved_provider, check_policy
from app.identity.providers import Registry
from app.identity.service import LocalIdentity, member_dto
from app.identity.sessions import IssuedSession
from app.models.common import utc_now

type Row = dict[str, Any]


def policy_context(row: Row) -> Row:
    return {
        "org_id": row["org_id"],
        "session_id": row["public_id"],
        "membership_revision": row["membership_revision"],
        "policy_version": row["policy_version"],
        "context_id": digest(
            "policy-context",
            ":".join(
                str(row[key])
                for key in ("public_id", "org_id", "membership_revision", "policy_version")
            ),
        ),
    }


class SSOIdentity:
    def __init__(self, local: LocalIdentity, registry: Registry, vault: SeedVault) -> None:
        self.local, self.store, self.registry, self.vault = local, local.store, registry, vault
        self.client = OIDCClient()
        local.sessions.providers = registry

    async def enabled(self) -> None:
        if not await self.store.find(
            "_sso_identity_control",
            {"_id": "installation", "schema_version": 40 if self.local.policy_engine else 39},
        ):
            raise AuthRejected("SSO is unavailable")

    async def organization(self, org_id: str, tx: Any = None) -> Row:
        org = await self.store.find("organizations", {"org_id": org_id, "status": "active"}, tx)
        if not org:
            raise AuthRejected("authentication was not accepted")
        return org

    async def membership(self, user_id: str, org_id: str, tx: Any = None) -> Row:
        member = await self.store.find(
            "organization_memberships",
            {
                "user_id": user_id,
                "org_id": org_id,
                "status": "active",
                "verified_at": {"$ne": None},
            },
            tx,
        )
        if not member:
            raise AuthRejected("authentication was not accepted")
        return member

    async def configure(
        self, org_id: str, registration_id: str, policy: str, approval: str
    ) -> None:
        await self.enabled()
        registration = self.registry.get(registration_id)
        if registration.org_id != org_id or policy not in {
            "local_only",
            "sso_preferred",
            "sso_required",
        }:
            raise AuthRejected("configuration was not accepted")
        if not 1 <= len(approval) <= 512:
            raise AuthRejected("an explicit operator approval reference is required")
        async with self.store.transaction() as tx:
            org = await self.organization(org_id, tx)
            config = await self.store.find("organization_oidc", {"org_id": org_id}, tx)
            values = {
                "registration_id": registration_id,
                "registration_version": registration.version,
                "approval_reference": approval,
            }
            if config:
                await self.store.update(
                    "organization_oidc", {"_id": config["_id"]}, {"$set": values}, tx
                )
            else:
                await self.store.insert(
                    "organization_oidc",
                    {"_id": uuid4().hex, "org_id": org_id, **values, "created_at": utc_now()},
                    tx,
                )
            if not await self.store.update(
                "organizations",
                {"org_id": org_id, "policy_version": org["policy_version"]},
                {"$set": {"auth_policy": policy}, "$inc": {"policy_version": 1}},
                tx,
            ):
                raise AuthRejected("configuration was not accepted")
            await self.local.audit(
                "oidc_configured",
                None,
                org_id,
                tx,
                actor={"actor_type": "installation_operator"},
                target_id=registration_id,
                approval_reference=approval,
            )

    async def break_glass(self, org_id: str, user_id: str, minutes: int, approval: str) -> None:
        await self.enabled()
        if not 1 <= minutes <= 60 or not 1 <= len(approval) <= 512:
            raise AuthRejected("bounded operator approval is required")
        async with self.store.transaction() as tx:
            org = await self.organization(org_id, tx)
            member = await self.membership(user_id, org_id, tx)
            factor = await self.store.find("mfa_factors", {"_id": user_id, "status": "active"}, tx)
            if org["auth_policy"] != "sso_required" or not factor:
                raise AuthRejected("verified local MFA membership is required")
            values = {
                "membership_revision": member["revision"],
                "policy_version": org["policy_version"],
                "expires_at": utc_now() + timedelta(minutes=minutes),
                "approval_reference": approval,
            }
            existing = await self.store.find(
                "organization_break_glass", {"org_id": org_id, "user_id": user_id}, tx
            )
            if existing:
                await self.store.update(
                    "organization_break_glass", {"_id": existing["_id"]}, {"$set": values}, tx
                )
            else:
                await self.store.insert(
                    "organization_break_glass",
                    {
                        "_id": uuid4().hex,
                        "org_id": org_id,
                        "user_id": user_id,
                        **values,
                        "created_at": utc_now(),
                    },
                    tx,
                )
            await self.local.audit(
                "break_glass_approved",
                user_id,
                org_id,
                tx,
                actor={"actor_type": "installation_operator"},
                approval_reference=approval,
            )

    async def memberships(self, row: Row) -> list[Row]:
        await self.local.sessions.authority(row)
        members = await self.store.list(
            "organization_memberships",
            {"user_id": row["user_id"], "status": "active", "verified_at": {"$ne": None}},
        )
        return [member_dto(member) for member in members]

    async def switch(self, row: Row, target: str) -> IssuedSession:
        async with self.store.transaction() as tx:
            user, _, _ = await self.local.sessions.fence(row, tx)
            member = await self.membership(user["_id"], target, tx)
            org = await self.organization(target, tx)
            candidate = {
                **row,
                "org_id": target,
                "membership_id": member["_id"],
                "membership_revision": member["revision"],
                "policy_version": org["policy_version"],
            }
            if (
                org["auth_policy"] == "sso_required"
                and (row.get("authentication") or {}).get("method", "local") == "local"
            ):
                self.local.sessions.recent(row)
            await check_policy(self.store, self.registry, candidate, org, tx)
            if not await self.store.update(
                "organization_memberships",
                {"_id": member["_id"], "revision": member["revision"], "status": "active"},
                {"$inc": {"fence": 1}},
                tx,
            ):
                raise AuthRejected("authentication was not accepted")
            await self.store.update(
                "browser_sessions",
                {"_id": row["_id"], "revoked": False},
                {"$set": {"revoked": True}},
                tx,
            )
            issued = await self.local.sessions.issue(
                user,
                member,
                org,
                row["amr"],
                row["password_at"],
                row["mfa_at"],
                expires_at=row["expires_at"],
                authentication=row.get("authentication"),
                tx=tx,
            )
            await self.local.audit(
                "organization_switched", user["_id"], target, tx, actor=self.local.actor(row)
            )
            return issued

    async def start(self, org_id: str, row: Row, browser_token: str, purpose: str) -> str:
        await self.enabled()
        if purpose not in {"login", "link", "switch", "reauth"}:
            raise AuthRejected("authentication was not accepted")
        if purpose == "login":
            if row["kind"] != "preauth":
                raise AuthRejected("authentication was not accepted")
        else:
            await self.local.sessions.authority(row)
            await self.membership(row["user_id"], org_id)
            if purpose == "link":
                self.local.sessions.recent(row)
            if purpose == "reauth" and row["org_id"] != org_id:
                raise AuthRejected("authentication was not accepted")
        org = await self.organization(org_id)
        if org["auth_policy"] == "local_only":
            raise AuthRejected("authentication was not accepted")
        registration = await approved_provider(self.store, self.registry, org)
        await self.local.throttle("oidc-start", row["user_id"] or row["_id"], 10)
        state, nonce, verifier = random_token(), random_token(), random_token()
        transaction_id = digest("oidc-state", state)
        url = await self.client.start(registration, state, nonce, verifier)
        await self.store.insert(
            "oidc_transactions",
            {
                "_id": transaction_id,
                "purpose": purpose,
                "org_id": org_id,
                "registration_id": registration.registration_id,
                "registration_version": registration.version,
                "policy_version": org["policy_version"],
                "browser_id": digest("oidc-browser", browser_token),
                "session_id": row["_id"],
                "user_id": row["user_id"],
                "auth_epoch": row["auth_epoch"],
                "nonce": nonce,
                "encrypted_verifier": self.vault.encrypt(
                    verifier, transaction_id, org_id, purpose="oidc-pkce"
                ),
                "used_at": None,
                "created_at": utc_now(),
                "expires_at": utc_now() + timedelta(minutes=5),
            },
        )
        return url

    async def callback(self, state: str, code: str, browser_token: str) -> IssuedSession | None:
        await self.enabled()
        if not valid_token(state) or not 1 <= len(code) <= 4096:
            raise AuthRejected("authentication was not accepted")
        # Consume before network work. A failed exchange cannot reuse this state.
        flow = await self.store.update(
            "oidc_transactions",
            {
                "_id": digest("oidc-state", state),
                "used_at": None,
                "browser_id": digest("oidc-browser", browser_token),
                "expires_at": {"$gt": utc_now()},
                "purpose": {"$in": ["login", "link", "switch", "reauth"]},
            },
            {"$set": {"used_at": utc_now()}},
        )
        if not flow:
            raise AuthRejected("authentication was not accepted")
        registration = self.registry.get(flow["registration_id"])
        org = await self.organization(flow["org_id"])
        approved = await approved_provider(self.store, self.registry, org)
        if (
            approved.version != flow["registration_version"]
            or org["policy_version"] != flow["policy_version"]
        ):
            raise AuthRejected("authentication was not accepted")
        claims = await self.client.exchange(
            registration,
            code,
            flow["nonce"],
            self.vault.decrypt(
                flow["encrypted_verifier"], flow["_id"], flow["org_id"], purpose="oidc-pkce"
            ),
        )
        if registration.require_mfa and not provider_mfa(registration, claims):
            raise AuthRejected("authentication was not accepted")
        async with self.store.transaction() as tx:
            current = await self.local.sessions.authenticate(
                browser_token,
                kind="preauth" if flow["purpose"] == "login" else "human",
                tx=tx,
                touch=False,
            )
            if current["_id"] != flow["session_id"] or current["auth_epoch"] != flow["auth_epoch"]:
                raise AuthRejected("authentication was not accepted")
            org = await self.organization(flow["org_id"], tx)
            approved = await approved_provider(self.store, self.registry, org, tx)
            if (
                org["policy_version"] != flow["policy_version"]
                or approved.version != registration.version
            ):
                raise AuthRejected("authentication was not accepted")
            identity = await self.store.find(
                "user_identities",
                {
                    "provider": registration.issuer,
                    "subject": claims["sub"],
                    "verified_at": {"$ne": None},
                },
                tx,
            )
            if flow["purpose"] == "link":
                user, _, _ = await self.local.sessions.fence(current, tx)
                self.local.sessions.recent(current)
                await self.membership(user["_id"], org["org_id"], tx)
                if identity and identity["user_id"] != user["_id"]:
                    raise AuthRejected("authentication was not accepted")
                if not identity:
                    await self.store.insert(
                        "user_identities",
                        {
                            "_id": uuid4().hex,
                            "user_id": user["_id"],
                            "provider": registration.issuer,
                            "subject": claims["sub"],
                            "verified_at": utc_now(),
                            "created_at": utc_now(),
                        },
                        tx,
                    )
                await self.store.update(
                    "users",
                    {"_id": user["_id"], "auth_epoch": user["auth_epoch"]},
                    {"$inc": {"auth_epoch": 1}},
                    tx,
                )
                await self.local.audit(
                    "identity_linked",
                    user["_id"],
                    org["org_id"],
                    tx,
                    actor=self.local.actor(current),
                    target_id=registration.registration_id,
                )
                return None
            if not identity:
                # Deliberately never search by email, even if verified by this IdP.
                raise AuthRejected("authentication was not accepted")
            found_user = await self.store.find(
                "users", {"_id": identity["user_id"], "status": "active"}, tx
            )
            if not found_user or (
                flow["purpose"] != "login" and found_user["_id"] != current["user_id"]
            ):
                raise AuthRejected("authentication was not accepted")
            user = found_user
            if flow["purpose"] != "login":
                await self.local.sessions.fence(current, tx)
            member = await self.membership(user["_id"], org["org_id"], tx)
            trusted_mfa = provider_mfa(registration, claims)
            if registration.require_mfa and not trusted_mfa:
                raise AuthRejected("authentication was not accepted")
            await self.store.update(
                "users",
                {"_id": user["_id"], "auth_epoch": user["auth_epoch"]},
                {"$inc": {"fence": 1}},
                tx,
            )
            await self.store.update(
                "organization_memberships",
                {"_id": member["_id"], "revision": member["revision"], "status": "active"},
                {"$inc": {"fence": 1}},
                tx,
            )
            await self.store.update(
                "browser_sessions",
                {"_id": current["_id"], "revoked": False},
                {"$set": {"revoked": True}},
                tx,
            )
            evidence = authentication(registration, claims, identity["_id"])
            issued = await self.local.sessions.issue(
                user,
                member,
                org,
                ["oidc"],
                None,
                evidence["auth_time"] if trusted_mfa else None,
                expires_at=min(current["expires_at"], evidence["valid_until"])
                if flow["purpose"] != "login"
                else evidence["valid_until"],
                authentication=evidence,
                tx=tx,
            )
            await self.local.audit(
                "oidc_login" if flow["purpose"] == "login" else "oidc_" + flow["purpose"],
                user["_id"],
                org["org_id"],
                tx,
                actor=self.local.actor(issued.row),
                target_id=registration.registration_id,
            )
            return issued

    async def logout(self, row: Row) -> str:
        evidence = row.get("authentication")
        if not evidence or evidence["method"] != "oidc":
            raise AuthRejected("authentication was not accepted")
        _, _, org = await self.local.sessions.authority(row)
        registration = await approved_provider(self.store, self.registry, org)
        state = random_token()
        async with self.store.transaction() as tx:
            await self.local.sessions.fence(row, tx)
            await self.store.insert(
                "oidc_transactions",
                {
                    "_id": digest("oidc-state", state),
                    "purpose": "logout",
                    "org_id": row["org_id"],
                    "registration_id": registration.registration_id,
                    "registration_version": registration.version,
                    "policy_version": row["policy_version"],
                    "browser_id": "logged-out",
                    "session_id": row["_id"],
                    "user_id": row["user_id"],
                    "auth_epoch": row["auth_epoch"],
                    "nonce": random_token(),
                    "encrypted_verifier": {},
                    "used_at": None,
                    "created_at": utc_now(),
                    "expires_at": utc_now() + timedelta(minutes=5),
                },
                tx,
            )
            await self.store.update(
                "browser_sessions", {"_id": row["_id"]}, {"$set": {"revoked": True}}, tx
            )
            await self.local.audit(
                "oidc_logout_started",
                row["user_id"],
                row["org_id"],
                tx,
                actor=self.local.actor(row),
            )
        # Keycloak supports client_id + exact post_logout_redirect_uri, with confirmation.
        return (
            registration.end_session_endpoint
            + "?"
            + urlencode(
                {
                    "client_id": registration.client_id,
                    "post_logout_redirect_uri": registration.post_logout_redirect_uri,
                    "state": state,
                }
            )
        )

    async def logged_out(self, state: str) -> None:
        if not valid_token(state) or not await self.store.update(
            "oidc_transactions",
            {
                "_id": digest("oidc-state", state),
                "purpose": "logout",
                "used_at": None,
                "expires_at": {"$gt": utc_now()},
            },
            {"$set": {"used_at": utc_now()}},
        ):
            raise AuthRejected("authentication was not accepted")
