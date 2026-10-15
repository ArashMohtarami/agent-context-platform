"""Invitation-only local identity; no corpus grants or machine-key adapter."""

from datetime import timedelta
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from pymongo.asynchronous.client_session import AsyncClientSession
from pymongo.errors import DuplicateKeyError

from app.identity.crypto import (
    AuthBusy,
    AuthRejected,
    PasswordWork,
    SeedVault,
    digest,
    random_token,
    valid_token,
)
from app.identity.mail import MailDelivery, Message
from app.identity.mfa import LocalMFA
from app.identity.models import RootTuple
from app.identity.repository import IdentityStore
from app.identity.sessions import BrowserSessions, IssuedSession
from app.models.common import utc_now

type Row = dict[str, Any]

if TYPE_CHECKING:
    from app.identity.sso import SSOIdentity


def local_subject(value: str) -> str:
    subject = value.strip().casefold()
    if len(subject) > 254 or subject.count("@") != 1 or any(c.isspace() for c in subject):
        raise AuthRejected("credentials were not accepted")
    local, domain = subject.split("@")
    if not local or "." not in domain or domain.startswith(".") or domain.endswith("."):
        raise AuthRejected("credentials were not accepted")
    return subject


def member_dto(member: Row) -> Row:
    """Organization directory shape: no email, login links, factors or other orgs."""
    return {key: member[key] for key in ("org_id", "display_name", "role", "status")}


class LocalIdentity:
    def __init__(self, store: IdentityStore, vault: SeedVault, mail: MailDelivery) -> None:
        self.store, self.mail = store, mail
        self.passwords = PasswordWork()
        self.sessions = BrowserSessions(store)
        self.mfa = LocalMFA(store, vault)
        self.sso: SSOIdentity | None = None

    async def enabled(self) -> None:
        marker = await self.store.find(
            "_local_identity_control",
            {"_id": "installation", "schema_version": 39 if self.sso else 38},
        )
        control = await self.store.find(
            "_tenant_control",
            {
                "_id": "installation",
                "stage": "isolated",
                "domain_traffic_enabled": True,
                "schema_version": 37,
                "worker_schema_versions": [37],
            },
        )
        if not marker or not control:
            raise AuthRejected("local identity is unavailable")
        if self.sso:
            await self.sso.enabled()

    async def throttle(self, namespace: str, value: str, maximum: int) -> None:
        now = utc_now()
        key = digest("throttle", f"{namespace}:{value}:{int(now.timestamp()) // 60}")
        try:
            await self.store.insert(
                "auth_throttles", {"_id": key, "count": 0, "expires_at": now + timedelta(hours=1)}
            )
        except DuplicateKeyError:
            pass
        row = await self.store.update("auth_throttles", {"_id": key}, {"$inc": {"count": 1}})
        if row is None or row["count"] > maximum:
            raise AuthBusy("authentication is temporarily busy")

    async def audit(
        self,
        action: str,
        user_id: str | None,
        org_id: str | None,
        tx: AsyncClientSession,
        *,
        actor: Row,
        target_id: str | None = None,
        approval_reference: str | None = None,
    ) -> None:
        await self.store.insert(
            "identity_audit_events",
            {
                "_id": uuid4().hex,
                "action": action,
                "user_id": user_id,
                "org_id": org_id,
                "target_id": target_id,
                "approval_reference": approval_reference,
                "actor": actor,
                "created_at": utc_now(),
            },
            tx,
        )

    async def challenge(
        self,
        purpose: str,
        user: Row,
        member: Row | None,
        *,
        browser_id: str | None = None,
        tx: AsyncClientSession,
    ) -> str:
        token, now = random_token(), utc_now()
        org = (
            await self.store.find("organizations", {"org_id": member["org_id"]}, tx)
            if member
            else None
        )
        await self.store.insert(
            "auth_challenges",
            {
                "_id": digest("challenge", token),
                "purpose": purpose,
                "provider": "local",
                "user_id": user["_id"],
                "org_id": member["org_id"] if member else None,
                "membership_id": member["_id"] if member else None,
                "membership_revision": member["revision"] if member else 0,
                "policy_version": org["policy_version"] if org else 0,
                "browser_id": browser_id,
                "auth_epoch": user["auth_epoch"],
                "used_at": None,
                "created_at": now,
                "expires_at": now + timedelta(minutes=5 if purpose == "login" else 30),
            },
            tx,
        )
        return token

    async def consume(
        self,
        token: str,
        purpose: str,
        tx: AsyncClientSession,
        *,
        browser_id: str | None = None,
    ) -> Row:
        if not valid_token(token):
            raise AuthRejected("authentication was not accepted")
        query: Row = {
            "_id": digest("challenge", token),
            "purpose": purpose,
            "provider": "local",
            "used_at": None,
            "expires_at": {"$gt": utc_now()},
        }
        if browser_id is not None:
            query["browser_id"] = browser_id
        row = await self.store.update(
            "auth_challenges", query, {"$set": {"used_at": utc_now()}}, tx
        )
        if row is None:
            raise AuthRejected("authentication was not accepted")
        return row

    async def operator_invite(
        self,
        org_id: str,
        email: str,
        display_name: str,
        *,
        approval_reference: str,
        root_delegation: list[Row] | None = None,
    ) -> None:
        """Only the host CLI calls this. Email proof conveys no independent authority."""
        await self.enabled()
        subject = local_subject(email)
        if not approval_reference or not 1 <= len(display_name) <= 128:
            raise ValueError("explicit operator approval and a bounded display name are required")
        if root_delegation is not None:
            if not 1 <= len(root_delegation) <= 100:
                raise ValueError("a bounded nonempty reviewed root delegation is required")
            root_delegation = [
                RootTuple.model_validate(row).model_dump() for row in root_delegation
            ]
            if not any(row["selector"] != "none" for row in root_delegation):
                raise ValueError("root delegation requires an explicit actionable ceiling")
        async with self.store.transaction() as tx:
            if not await self.store.find(
                "organizations", {"org_id": org_id, "status": "active"}, tx
            ):
                raise AuthRejected("organization is unavailable")
            identity = await self.store.find(
                "user_identities", {"provider": "local", "subject": subject}, tx
            )
            if identity:
                user = await self.store.find(
                    "users", {"_id": identity["user_id"], "status": "active"}, tx
                )
                if not user:
                    raise AuthRejected("identity is unavailable; use explicit operator recovery")
            else:
                user = {
                    "_id": "user_" + uuid4().hex,
                    "display_name": display_name,
                    "status": "pending",
                    "auth_epoch": 1,
                    "fence": 0,
                    "created_at": utc_now(),
                }
                await self.store.insert("users", user, tx)
                await self.store.insert(
                    "user_identities",
                    {
                        "_id": digest("identity", "local:" + subject),
                        "provider": "local",
                        "subject": subject,
                        "user_id": user["_id"],
                        "verified_at": None,
                        "created_at": utc_now(),
                    },
                    tx,
                )
            owner = root_delegation is not None
            if owner and not root_delegation:
                raise ValueError(
                    "initial owner requires an explicit nonempty reviewed root delegation"
                )
            intent = (
                None
                if not owner
                else {
                    "state": "pending_phase5",
                    "approval_reference": approval_reference,
                    "approved_at": utc_now(),
                    "root_delegation": root_delegation,
                    "bootstrap_policy": [
                        {
                            "action": action,
                            "resource_kind": "project_directory",
                            "selector": "all_in_org",
                            "ids": [],
                        }
                        for action in ("projects:read", "projects:write")
                    ],
                    "further_self_grants": "independent_approval_or_documented_operator_recovery",
                }
            )
            member = {
                "_id": "member_" + uuid4().hex,
                "org_id": org_id,
                "user_id": user["_id"],
                "display_name": display_name,
                "role": "org_owner" if owner else "contributor",
                "status": "invited",
                "verified_at": None,
                "revision": 1,
                "fence": 0,
                "bootstrap_owner": owner,
                "bootstrap_intent": intent,
                "created_at": utc_now(),
            }
            await self.store.insert("organization_memberships", member, tx)
            token = await self.challenge("invite", user, member, tx=tx)
            await self.audit(
                "owner_provisioned" if owner else "invitation_created",
                user["_id"],
                org_id,
                tx,
                actor={"actor_type": "system", "auth_method": "operator_cli"},
                target_id=member["_id"],
                approval_reference=approval_reference,
            )
        await self.mail.deliver(Message(subject, "invite", token))

    async def accept_invite(
        self, token: str, password: str, browser_id: str, code: str = ""
    ) -> None:
        # Hash before opening a transaction; the token still has to win the atomic consume.
        encoded = await self.passwords.hash(password)
        lookup = await self.store.find(
            "auth_challenges", {"_id": digest("challenge", token), "purpose": "invite"}
        )
        credential = (
            await self.store.find("local_credentials", {"_id": lookup["user_id"]})
            if lookup
            else None
        )
        if credential and not await self.passwords.verify(credential["password_hash"], password):
            raise AuthRejected("authentication was not accepted")
        async with self.store.transaction() as tx:
            invitation = await self.consume(token, "invite", tx)
            user = await self.store.find(
                "users",
                {
                    "_id": invitation["user_id"],
                    "auth_epoch": invitation["auth_epoch"],
                    "status": {"$in": ["pending", "active"]},
                },
                tx,
            )
            member = await self.store.update(
                "organization_memberships",
                {
                    "_id": invitation["membership_id"],
                    "org_id": invitation["org_id"],
                    "user_id": invitation["user_id"],
                    "revision": invitation["membership_revision"],
                    "status": "invited",
                },
                {"$set": {"status": "verification_pending"}, "$inc": {"revision": 1}},
                tx,
            )
            if not user or not member:
                raise AuthRejected("authentication was not accepted")
            existing = await self.store.find("local_credentials", {"_id": user["_id"]}, tx)
            if existing:
                if not credential or existing["password_hash"] != credential["password_hash"]:
                    raise AuthRejected("authentication was not accepted")
                factor = await self.store.find(
                    "mfa_factors", {"_id": user["_id"], "status": "active"}, tx
                )
                if factor:
                    await self.mfa.consume(user["_id"], code, tx)
            else:
                if user["status"] != "pending":
                    raise AuthRejected("authentication was not accepted")
                await self.store.insert(
                    "local_credentials",
                    {
                        "_id": user["_id"],
                        "user_id": user["_id"],
                        "password_hash": encoded,
                        "updated_at": utc_now(),
                    },
                    tx,
                )
                await self.store.update(
                    "users",
                    {"_id": user["_id"], "auth_epoch": user["auth_epoch"]},
                    {"$set": {"status": "verification_pending"}, "$inc": {"fence": 1}},
                    tx,
                )
            verification = await self.challenge(
                "verify", user, member, browser_id=browser_id, tx=tx
            )
            identity = await self.store.find(
                "user_identities", {"user_id": user["_id"], "provider": "local"}, tx
            )
            if identity is None:
                raise AuthRejected("authentication was not accepted")
            await self.audit(
                "invitation_accepted",
                user["_id"],
                member["org_id"],
                tx,
                actor={
                    "actor_type": "human",
                    "user_id": user["_id"],
                    "auth_method": "invite_challenge",
                },
            )
        await self.mail.deliver(Message(identity["subject"], "verify", verification))

    async def verify(self, token: str, browser_id: str) -> None:
        async with self.store.transaction() as tx:
            challenge = await self.consume(token, "verify", tx, browser_id=browser_id)
            user = await self.store.update(
                "users",
                {
                    "_id": challenge["user_id"],
                    "auth_epoch": challenge["auth_epoch"],
                    "status": {"$in": ["verification_pending", "active"]},
                },
                {"$set": {"status": "active"}, "$inc": {"fence": 1}},
                tx,
            )
            member = await self.store.update(
                "organization_memberships",
                {
                    "_id": challenge["membership_id"],
                    "user_id": challenge["user_id"],
                    "org_id": challenge["org_id"],
                    "revision": challenge["membership_revision"],
                    "status": "verification_pending",
                },
                {"$set": {"status": "active", "verified_at": utc_now()}, "$inc": {"revision": 1}},
                tx,
            )
            if not user or not member:
                raise AuthRejected("authentication was not accepted")
            await self.store.update(
                "user_identities",
                {"user_id": user["_id"], "provider": "local"},
                {"$set": {"verified_at": utc_now()}},
                tx,
            )
            await self.audit(
                "email_verified",
                user["_id"],
                member["org_id"],
                tx,
                actor={
                    "actor_type": "human",
                    "user_id": user["_id"],
                    "auth_method": "verification_challenge",
                },
            )

    async def login(
        self, email: str, password: str, org_id: str, browser_id: str
    ) -> tuple[str, bool]:
        subject = local_subject(email)
        await self.throttle("account", subject, 10)
        identity = await self.store.find(
            "user_identities",
            {"provider": "local", "subject": subject, "verified_at": {"$ne": None}},
        )
        credential = (
            await self.store.find("local_credentials", {"_id": identity["user_id"]})
            if identity
            else None
        )
        accepted = await self.passwords.verify(
            credential["password_hash"] if credential else None, password
        )
        if not accepted or identity is None or credential is None:
            raise AuthRejected("credentials were not accepted")
        async with self.store.transaction() as tx:
            user = await self.store.find(
                "users", {"_id": identity["user_id"], "status": "active"}, tx
            )
            member = await self.store.find(
                "organization_memberships",
                {
                    "org_id": org_id,
                    "user_id": identity["user_id"],
                    "status": "active",
                    "verified_at": {"$ne": None},
                },
                tx,
            )
            org = await self.store.find(
                "organizations",
                {
                    "org_id": org_id,
                    "status": "active",
                },
                tx,
            )
            # Password reset and login serialize on the same user fence, including
            # a hash verified just before a concurrent reset transaction committed.
            current_credential = await self.store.find(
                "local_credentials", {"_id": identity["user_id"]}, tx
            )
            if (
                not user
                or not member
                or not org
                or not current_credential
                or current_credential["password_hash"] != credential["password_hash"]
            ):
                raise AuthRejected("credentials were not accepted")
            if org["auth_policy"] == "sso_required":
                if not self.sso or not await self.store.find(
                    "organization_break_glass",
                    {
                        "org_id": org_id,
                        "user_id": user["_id"],
                        "membership_revision": member["revision"],
                        "policy_version": org["policy_version"],
                        "expires_at": {"$gt": utc_now()},
                    },
                    tx,
                ):
                    raise AuthRejected("credentials were not accepted")
            await self.store.update(
                "users",
                {"_id": user["_id"], "auth_epoch": user["auth_epoch"]},
                {"$inc": {"fence": 1}},
                tx,
            )
            token = await self.challenge("login", user, member, browser_id=browser_id, tx=tx)
            factor = await self.store.find(
                "mfa_factors", {"_id": user["_id"], "status": "active"}, tx
            )
        return token, bool(factor)

    async def finish_login(
        self, token: str, browser_id: str, code: str, *, recovery: bool = False
    ) -> IssuedSession:
        await self.throttle("challenge", digest("challenge", token), 5)
        attempt = await self.store.find(
            "auth_challenges",
            {
                "_id": digest("challenge", token),
                "purpose": "login",
                "used_at": None,
                "expires_at": {"$gt": utc_now()},
            },
        )
        if attempt:
            await self.throttle("login-factor", attempt["user_id"], 10)
        async with self.store.transaction() as tx:
            challenge = await self.consume(token, "login", tx, browser_id=browser_id)
            member = await self.store.find(
                "organization_memberships", {"_id": challenge["membership_id"]}, tx
            )
            org = await self.store.find("organizations", {"org_id": challenge["org_id"]}, tx)
            if not member or not org:
                raise AuthRejected("authentication was not accepted")
            snapshot = {
                "user_id": challenge["user_id"],
                "auth_epoch": challenge["auth_epoch"],
                "membership_id": challenge["membership_id"],
                "membership_revision": challenge["membership_revision"],
                "org_id": challenge["org_id"],
                "policy_version": challenge["policy_version"],
            }
            factor = await self.store.find(
                "mfa_factors", {"_id": challenge["user_id"], "status": "active"}, tx
            )
            if factor:
                await self.mfa.consume(challenge["user_id"], code, tx, recovery=recovery)
                snapshot["mfa_at"] = utc_now()
            user, member, org = await self.sessions.authority(snapshot, tx)
            await self.store.update(
                "users",
                {"_id": user["_id"], "auth_epoch": user["auth_epoch"]},
                {"$inc": {"fence": 1}},
                tx,
            )
            await self.store.update(
                "organization_memberships",
                {"_id": member["_id"], "revision": member["revision"]},
                {"$inc": {"fence": 1}},
                tx,
            )
            amr = ["password", "recovery" if recovery else "totp"] if factor else ["password"]
            issued = await self.sessions.issue(
                user,
                member,
                org,
                amr,
                challenge["created_at"],
                utc_now() if factor else None,
                tx=tx,
            )
            await self.audit(
                "recovery_login" if factor and recovery else "local_login",
                user["_id"],
                member["org_id"],
                tx,
                actor={
                    "actor_type": "human",
                    "user_id": user["_id"],
                    "org_id": member["org_id"],
                    "auth_method": "local",
                    "assurance": "mfa" if factor else "password",
                },
            )
        return issued

    async def request_reset(self, email: str) -> None:
        subject = local_subject(email)
        await self.throttle("account", subject, 10)
        identity = await self.store.find(
            "user_identities",
            {"provider": "local", "subject": subject, "verified_at": {"$ne": None}},
        )
        if not identity:
            return
        async with self.store.transaction() as tx:
            user = await self.store.find(
                "users", {"_id": identity["user_id"], "status": "active"}, tx
            )
            if not user:
                return
            token = await self.challenge("reset", user, None, tx=tx)
        await self.mail.deliver(Message(subject, "reset", token))

    async def reset(self, token: str, password: str) -> None:
        encoded = await self.passwords.hash(password)
        async with self.store.transaction() as tx:
            challenge = await self.consume(token, "reset", tx)
            user = await self.store.update(
                "users",
                {
                    "_id": challenge["user_id"],
                    "auth_epoch": challenge["auth_epoch"],
                    "status": "active",
                },
                {"$inc": {"auth_epoch": 1, "fence": 1}},
                tx,
            )
            if not user:
                raise AuthRejected("authentication was not accepted")
            await self.store.update(
                "local_credentials",
                {"_id": user["_id"]},
                {"$set": {"password_hash": encoded, "updated_at": utc_now()}},
                tx,
            )
            await self.audit(
                "password_reset",
                user["_id"],
                None,
                tx,
                actor={
                    "actor_type": "human",
                    "user_id": user["_id"],
                    "auth_method": "reset_challenge",
                },
            )

    async def me(self, row: Row) -> Row:
        user, member, _ = await self.sessions.authority(row)
        result = {
            "user_id": user["_id"],
            "display_name": user["display_name"],
            "membership": member_dto(member),
            "session_id": row["public_id"],
            "auth_method": (row.get("authentication") or {}).get("method", "local"),
            "assurance": self.assurance(row),
            "mfa_enrollment_required": row["mfa_at"] is None,
            "effective_corpus_access": [],
            "policy_engine_available": False,
            **({"policy_context": self.policy_context(row)} if self.sso else {}),
        }
        if self.sso:
            factor = await self.store.find("mfa_factors", {"_id": user["_id"], "status": "active"})
            result["mfa_enrollment_required"] = factor is None and row["mfa_at"] is None
            result["step_up_required"] = row["mfa_at"] is None or not (
                timedelta(0) <= utc_now() - row["mfa_at"] <= timedelta(minutes=10)
            )
        return result

    async def begin_mfa(self, row: Row) -> tuple[str, str]:
        self.sessions.recent(row, mfa=False)
        async with self.store.transaction() as tx:
            await self.sessions.fence(row, tx)
            result = await self.mfa.begin(row["user_id"], tx)
            await self.audit(
                "mfa_enrollment_started", row["user_id"], row["org_id"], tx, actor=self.actor(row)
            )
        return result

    @staticmethod
    def policy_context(row: Row) -> Row:
        from app.identity.sso import policy_context

        return policy_context(row)

    @staticmethod
    def assurance(row: Row) -> str:
        if row["mfa_at"]:
            return "mfa"
        return (
            "authenticated"
            if (row.get("authentication") or {}).get("method") == "oidc"
            else "password"
        )

    @staticmethod
    def actor(row: Row) -> Row:
        return {
            "actor_type": "human",
            "user_id": row["user_id"],
            "org_id": row["org_id"],
            "auth_method": (row.get("authentication") or {}).get("method", "local"),
            "assurance": LocalIdentity.assurance(row),
        }

    async def confirm_mfa(self, row: Row, code: str) -> tuple[IssuedSession, list[str]]:
        self.sessions.recent(row, mfa=False)
        async with self.store.transaction() as tx:
            await self.sessions.fence(row, tx)
            await self.mfa.consume(row["user_id"], code, tx, pending=True)
            codes = await self.mfa.recovery_codes(row["user_id"], tx, activate=True)
            issued = await self.sessions.rotate(row, tx, strong=True, epoch=row["auth_epoch"] + 1)
            await self.store.update(
                "users",
                {"_id": row["user_id"], "auth_epoch": row["auth_epoch"]},
                {"$inc": {"auth_epoch": 1, "fence": 1}},
                tx,
            )
            await self.audit(
                "mfa_enrolled", row["user_id"], row["org_id"], tx, actor=self.actor(issued.row)
            )
        return issued, codes

    async def step_up(self, row: Row, code: str, recovery: bool = False) -> IssuedSession:
        async with self.store.transaction() as tx:
            await self.sessions.fence(row, tx)
            await self.mfa.consume(row["user_id"], code, tx, recovery=recovery)
            issued = await self.sessions.rotate(
                row, tx, strong=True, method="recovery" if recovery else "totp"
            )
            await self.audit(
                "mfa_recovery_used" if recovery else "mfa_step_up",
                row["user_id"],
                row["org_id"],
                tx,
                actor=self.actor(issued.row),
            )
        return issued

    async def rotate(self, row: Row) -> IssuedSession:
        async with self.store.transaction() as tx:
            issued = await self.sessions.rotate(row, tx)
            await self.audit(
                "session_rotated", row["user_id"], row["org_id"], tx, actor=self.actor(row)
            )
        return issued

    async def logout(self, row: Row) -> None:
        # Logout must remain possible after the step-up window has elapsed.
        async with self.store.transaction() as tx:
            await self.store.update(
                "browser_sessions",
                {"_id": row["_id"], "user_id": row["user_id"]},
                {"$set": {"revoked": True}},
                tx,
            )
            await self.audit(
                "session_logout", row["user_id"], row["org_id"], tx, actor=self.actor(row)
            )

    async def list_sessions(self, row: Row) -> list[Row]:
        sessions = await self.store.list(
            "browser_sessions", {"user_id": row["user_id"], "kind": "human"}
        )
        fields = (
            "public_id",
            "org_id",
            "created_at",
            "last_seen",
            "expires_at",
            "idle_expires_at",
            "revoked",
        )
        return [{key: session[key] for key in fields} for session in sessions[:100]]

    async def revoke_session(self, row: Row, public_id: str) -> None:
        self.sessions.recent(row)
        async with self.store.transaction() as tx:
            await self.sessions.fence(row, tx)
            target = await self.store.update(
                "browser_sessions",
                {"user_id": row["user_id"], "public_id": public_id, "kind": "human"},
                {"$set": {"revoked": True}},
                tx,
            )
            if not target:
                raise AuthRejected("session was not found")
            await self.audit(
                "session_revoked",
                row["user_id"],
                row["org_id"],
                tx,
                actor=self.actor(row),
                target_id=public_id,
            )

    async def remove_mfa(self, row: Row) -> None:
        self.sessions.recent(row)
        async with self.store.transaction() as tx:
            await self.sessions.fence(row, tx)
            if not await self.store.update(
                "mfa_factors",
                {"_id": row["user_id"], "status": "active"},
                {"$set": {"status": "disabled", "recovery_hashes": []}, "$inc": {"revision": 1}},
                tx,
            ):
                raise AuthRejected("authentication was not accepted")
            await self.store.update(
                "users",
                {"_id": row["user_id"], "auth_epoch": row["auth_epoch"]},
                {"$inc": {"auth_epoch": 1, "fence": 1}},
                tx,
            )
            await self.audit(
                "mfa_removed", row["user_id"], row["org_id"], tx, actor=self.actor(row)
            )

    async def renew_recovery(self, row: Row, code: str) -> tuple[IssuedSession, list[str]]:
        self.sessions.recent(row)
        async with self.store.transaction() as tx:
            await self.sessions.fence(row, tx)
            await self.mfa.consume(row["user_id"], code, tx)
            codes = await self.mfa.recovery_codes(row["user_id"], tx)
            issued = await self.sessions.rotate(row, tx, strong=True, epoch=row["auth_epoch"] + 1)
            await self.store.update(
                "users",
                {"_id": row["user_id"], "auth_epoch": row["auth_epoch"]},
                {"$inc": {"auth_epoch": 1, "fence": 1}},
                tx,
            )
            await self.audit(
                "recovery_codes_replaced",
                row["user_id"],
                row["org_id"],
                tx,
                actor=self.actor(issued.row),
            )
        return issued, codes

    async def change_password(self, row: Row, old: str, new: str) -> None:
        self.sessions.recent(row)
        credential = await self.store.find("local_credentials", {"_id": row["user_id"]})
        accepted = await self.passwords.verify(
            credential["password_hash"] if credential else None, old
        )
        if not accepted or credential is None:
            raise AuthRejected("authentication was not accepted")
        encoded = await self.passwords.hash(new)
        async with self.store.transaction() as tx:
            await self.sessions.fence(row, tx)
            if not await self.store.update(
                "local_credentials",
                {"_id": row["user_id"], "password_hash": credential["password_hash"]},
                {"$set": {"password_hash": encoded, "updated_at": utc_now()}},
                tx,
            ):
                raise AuthRejected("authentication was not accepted")
            await self.store.update(
                "users",
                {"_id": row["user_id"], "auth_epoch": row["auth_epoch"]},
                {"$inc": {"auth_epoch": 1, "fence": 1}},
                tx,
            )
            await self.audit(
                "password_changed", row["user_id"], row["org_id"], tx, actor=self.actor(row)
            )
