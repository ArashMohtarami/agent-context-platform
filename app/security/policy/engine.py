"""Uncached policy resolution and a concrete shared MongoDB serialization fence."""

from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import timedelta
from hashlib import sha256
from json import dumps
from typing import Any
from uuid import uuid4

from pymongo.read_concern import ReadConcern
from pymongo.write_concern import WriteConcern

from app.identity.repository import IdentityStore, Row
from app.models.common import utc_now
from app.security.policy.evaluation import PolicyConflict, PolicyDenied, effective
from app.security.policy.models import Grant
from app.security.principal import Principal
from app.security.tenant_policy import ResourceGrant, TenantPolicy

MAX_ASSIGNMENTS = 1000


class PolicyEngine:
    def __init__(self, store: IdentityStore) -> None:
        self.store = store

    async def enabled(self) -> None:
        if not await self.store.find(
            "_management_policy_control", {"_id": "installation", "schema_version": 40}
        ):
            raise PolicyDenied("management policy is unavailable")

    async def validate_resources(self, org_id: str, tuples: tuple[Grant, ...], tx: Any) -> None:
        boundaries = {
            "project_directory": ("projects", "project_id"),
            "project": ("projects", "project_id"),
            "creator_profile": ("creator_profiles", "profile_id"),
            "client": ("agent_clients", "client_id"),
            "client_memory": ("agent_clients", "client_id"),
            "skill": ("skills", "skill_id"),
            "research_project": ("research_projects", "research_project_id"),
            "member": ("organization_memberships", "user_id"),
        }
        for grant in tuples:
            if grant.selector != "include":
                continue
            if grant.resource_kind in {"organization", "organization_knowledge"}:
                if set(grant.ids) != {org_id}:
                    raise PolicyDenied("resource is outside the organization")
                continue
            boundary = boundaries.get(grant.resource_kind)
            if boundary is None:
                raise PolicyDenied("resource boundary is not active in this phase")
            collection, field = boundary
            identifiers = await self.store.database[collection].distinct(
                field, {"org_id": org_id, field: {"$in": grant.ids}}, session=tx
            )
            if set(identifiers) != set(grant.ids):
                raise PolicyDenied("resource is unavailable in the organization")

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[Any]:
        async with self.store.database.client.start_session() as tx:
            async with await tx.start_transaction(
                read_concern=ReadConcern("snapshot"), write_concern=WriteConcern("majority")
            ):
                yield tx

    async def anchor(self, org_id: str, tx: Any = None) -> Row:
        row = await self.store.find("policy_anchors", {"_id": org_id, "org_id": org_id}, tx)
        if not row or row["status"] != "active":
            raise PolicyDenied("organization policy is not activated")
        return row

    async def fence(self, anchor: Row, tx: Any, *, mutation: bool = False) -> None:
        # This MUST change a field: a no-op update does not acquire the required fence.
        result = await self.store.update(
            "policy_anchors",
            {
                "_id": anchor["_id"],
                "org_id": anchor["org_id"],
                "revision": anchor["revision"],
                "status": "active",
            },
            {"$inc": {"fence": 1, **({"revision": 1} if mutation else {})}},
            tx,
        )
        if not result:
            raise PolicyConflict("policy changed; refresh effective access")

    async def snapshot(self, org_id: str, subject_id: str, tx: Any = None) -> Row:
        await self.enabled()
        anchor = await self.anchor(org_id, tx)
        organization = await self.store.find(
            "organizations", {"org_id": org_id, "status": "active"}, tx
        )
        if not organization:
            raise PolicyDenied("organization is unavailable")
        rows = (
            await self.store._collection("policy_assignments")
            .find({"org_id": org_id}, session=tx)
            .limit(MAX_ASSIGNMENTS + 1)
            .to_list(MAX_ASSIGNMENTS + 1)
        )
        if len(rows) > MAX_ASSIGNMENTS:
            raise PolicyDenied("organization policy exceeds its bounded evaluation capacity")
        assignments = {r["_id"]: r for r in rows}
        ids = sorted({subject_id, *(r["subject_id"] for r in rows)})
        members = {
            r["user_id"]: r
            for r in await self.store._collection("organization_memberships")
            .find({"org_id": org_id, "user_id": {"$in": ids}}, session=tx)
            .to_list(MAX_ASSIGNMENTS + 1)
        }
        users = {
            r["_id"]: r
            for r in await self.store._collection("users")
            .find({"_id": {"$in": ids}}, session=tx)
            .to_list(MAX_ASSIGNMENTS + 1)
        }
        member, user = members.get(subject_id), users.get(subject_id)
        if (
            not member
            or not user
            or member["status"] != "active"
            or not member["verified_at"]
            or user["status"] != "active"
        ):
            raise PolicyDenied("verified active membership is required")
        tuples, explanations = effective(org_id, subject_id, assignments, members, users, utc_now())
        return {
            "anchor": anchor,
            "member": member,
            "user": user,
            "assignments": assignments,
            "members": members,
            "users": users,
            "tuples": tuples,
            "explanations": explanations,
        }

    def policy(
        self,
        snapshot: Row,
        *,
        refresh: Callable[[], Awaitable[None]] | None = None,
        transaction_check: Callable[[Any], Awaitable[None]] | None = None,
    ) -> TenantPolicy:
        member, anchor = snapshot["member"], snapshot["anchor"]
        return TenantPolicy(
            anchor["org_id"],
            member["user_id"],
            tuple(
                ResourceGrant(g.action, g.resource_kind, g.selector, tuple(g.ids))
                for g in snapshot["tuples"]
            ),
            managed=True,
            subject_kind="human",
            revision=anchor["revision"],
            subject_revision=member["revision"],
            engine=self,
            refresh=refresh,
            transaction_check=transaction_check,
        )

    @staticmethod
    def require(snapshot: Row, action: str, kind: str, identifier: str) -> None:
        if not any(
            g.action == action and g.resource_kind == kind and g.selection().contains(identifier)
            for g in snapshot["tuples"]
        ):
            raise PolicyDenied("operation is outside effective access")

    async def audit(
        self,
        org_id: str,
        actor_id: str,
        action: str,
        target_id: str,
        tx: Any,
        *,
        approval: str | None = None,
        change: Row | None = None,
    ) -> None:
        event_id = "gov_" + uuid4().hex
        event = {
            "_id": event_id,
            "org_id": org_id,
            "actor_id": actor_id,
            "action": action,
            "target_id": target_id,
            "approval_reference": approval,
            "change": change or {},
            "created_at": utc_now(),
        }
        await self.store.insert("governance_events", event, tx)
        await self.store.insert(
            "governance_outbox",
            {
                "_id": event_id,
                "org_id": org_id,
                "event_id": event_id,
                "status": "pending",
                "created_at": event["created_at"],
            },
            tx,
        )

    async def revalidate(self, policy: TenantPolicy, tx: Any = None) -> Row:
        if tx is not None and policy.transaction_check:
            await policy.transaction_check(tx)
        elif policy.refresh:
            await policy.refresh()
        if not policy.managed:
            anchor = await self.anchor(policy.org_id, tx)
            if anchor["revision"] != policy.revision:
                raise PolicyConflict("policy changed; refresh effective access")
            return {"anchor": anchor}
        snapshot = await self.snapshot(policy.org_id, policy.subject_id, tx)
        if (
            snapshot["anchor"]["revision"] != policy.revision
            or snapshot["member"]["revision"] != policy.subject_revision
        ):
            raise PolicyConflict("policy changed; refresh effective access")
        return snapshot

    @asynccontextmanager
    async def operator_fence(self, session: Any = None) -> AsyncIterator[Any]:
        from app.security.tenant import require_tenant

        async def lock(tx: Any) -> None:
            await self.enabled()
            anchor = await self.anchor(require_tenant().org_id, tx)
            # Operator writes can change credential envelopes; advance policy
            # revision as well as taking the serialization lock.
            await self.fence(anchor, tx, mutation=True)

        if session is not None:
            await lock(session)
            yield session
        else:
            async with self.transaction() as tx:
                await lock(tx)
                yield tx

    @asynccontextmanager
    async def domain_fence(self, policy: TenantPolicy, session: Any = None) -> AsyncIterator[Any]:
        # No automatic replay: a stale human approval must be reviewed again.
        if session is not None:
            snapshot = await self.revalidate(policy, session)
            await self.fence(snapshot["anchor"], session)
            yield session
        else:
            async with self.transaction() as tx:
                snapshot = await self.revalidate(policy, tx)
                await self.fence(snapshot["anchor"], tx)
                yield tx

    @staticmethod
    def credential_hash(principal: Principal) -> str:
        return sha256(
            dumps(principal.describe(), sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()

    async def credential(self, principal: Principal) -> Principal:
        """Phase 7 managed sponsors are deliberately unimplemented and denied.

        Only an explicit, unchanged, expiring adoption-operator adapter survives.
        This decision is shared by ordinary REST, HTTP/stdio MCP and job rechecks.
        """
        from app.security.tenant_policy import policy_of

        row = await self._credential_valid(principal)
        anchor = await self.anchor(str(principal.org_id))

        async def refresh() -> None:
            current = await self._credential_valid(principal)
            if current["revision"] != row["revision"]:
                raise PolicyConflict("credential adapter changed")

        policy = replace(
            policy_of(principal), revision=anchor["revision"], engine=self, refresh=refresh
        )
        return replace(principal, managed_policy=policy, legacy_policy_revision=row["revision"])

    async def _credential_valid(self, principal: Principal) -> Row:
        row = await self.store.find(
            "policy_credentials", {"org_id": principal.org_id, "key_id": principal.key_id}
        )
        if (
            not row
            or row["status"] != "active"
            or row["mode"] != "legacy_adoption"
            or row["expires_at"] <= utc_now()
        ):
            raise PolicyDenied("credential sponsorship is not verified")
        if row["envelope_hash"] != self.credential_hash(principal):
            raise PolicyDenied("credential adapter changed; explicit review is required")
        key = await self.store.database["api_keys"].find_one(
            {"org_id": principal.org_id, "key_id": principal.key_id}
        )
        if not key or key.get("identity_version") != 1 or not key.get("legacy_adoption_id"):
            raise PolicyDenied("only reviewed adopted credentials may use the legacy adapter")
        from app.models.agent import AgentClientDocument
        from app.models.api_key import ApiKeyDocument
        from app.services.auth_service import principal_of

        record = ApiKeyDocument.model_validate(key, context={"from_storage": True})
        if record.status != "active" or record.is_expired(utc_now()):
            raise PolicyDenied("initiating credential is unavailable")
        client = None
        if record.client_id:
            raw = await self.store.database["agent_clients"].find_one(
                {"org_id": record.org_id, "client_id": record.client_id}
            )
            if not raw or raw.get("status") != "active":
                raise PolicyDenied("initiating client is unavailable")
            client = AgentClientDocument.model_validate(raw, context={"from_storage": True})
        if self.credential_hash(principal_of(record, client)) != row["envelope_hash"]:
            raise PolicyDenied("initiating capability changed")
        return row

    async def approve_legacy(self, principal: Principal, approval: str, expires_at: Any) -> None:
        if not approval or not utc_now() < expires_at <= utc_now() + timedelta(days=30):
            raise PolicyDenied("bounded operator approval is required")
        async with self.transaction() as tx:
            anchor = await self.anchor(str(principal.org_id), tx)
            key = await self.store.database["api_keys"].find_one(
                {"org_id": principal.org_id, "key_id": principal.key_id}, session=tx
            )
            if not key or key.get("identity_version") != 1 or not key.get("legacy_adoption_id"):
                raise PolicyDenied("only adopted credentials have a legacy adapter")
            await self.fence(anchor, tx, mutation=True)
            row = await self.store.find(
                "policy_credentials", {"org_id": principal.org_id, "key_id": principal.key_id}, tx
            )
            values = {
                "envelope_hash": self.credential_hash(principal),
                "expires_at": expires_at,
                "approval_reference": approval,
                "status": "active",
            }
            if row:
                await self.store.update(
                    "policy_credentials",
                    {"_id": row["_id"]},
                    {"$set": values, "$inc": {"revision": 1}},
                    tx,
                )
            else:
                await self.store.insert(
                    "policy_credentials",
                    {
                        "_id": uuid4().hex,
                        "org_id": principal.org_id,
                        "key_id": principal.key_id,
                        "mode": "legacy_adoption",
                        "revision": 1,
                        **values,
                    },
                    tx,
                )
            await self.audit(
                str(principal.org_id),
                "installation_operator",
                "legacy_adapter_approved",
                principal.key_id,
                tx,
                approval=approval,
            )
