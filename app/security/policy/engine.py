"""Uncached policy resolution and a concrete shared MongoDB serialization fence."""

import asyncio
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
    def __init__(
        self, store: IdentityStore, *, human_management: bool = False, managed_agents: bool = False
    ) -> None:
        self.store = store
        self.human_management = human_management or managed_agents
        self.managed_agents = managed_agents
        self.schema_version = 42 if managed_agents else 41 if human_management else 40

    async def enabled(self) -> None:
        if not await self.store.find(
            "_management_policy_control",
            {"_id": "installation", "schema_version": self.schema_version},
        ):
            raise PolicyDenied("management policy is unavailable")
        if self.human_management and not await self.store.find(
            "_human_management_control",
            {"_id": "installation", "schema_version": self.schema_version},
        ):
            raise PolicyDenied("human management migration is incomplete")

        if self.managed_agents and not await self.store.find(
            "_managed_agents_control", {"_id": "installation", "schema_version": 42}
        ):
            raise PolicyDenied("managed agents migration is incomplete")

    async def validate_resources(self, org_id: str, tuples: tuple[Grant, ...], tx: Any) -> None:
        boundaries = {
            **({"team": ("teams", "team_id")} if self.human_management else {}),
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

    async def validate_targets(self, org_id: str, identifiers: tuple[str, ...], tx: Any) -> None:
        if identifiers:
            found = await self.store._collection("organization_memberships").distinct(
                "user_id", {"org_id": org_id, "user_id": {"$in": list(identifiers)}}, session=tx
            )
            if set(found) != set(identifiers):
                raise PolicyDenied("delegation target is outside the organization")

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

    async def confirm_read(self, anchor: Row) -> None:
        """Linearize read delivery against governance without holding a long lock.

        An atomic majority write checks the current anchor, rather than a
        possibly stale transaction snapshot. Governance still fences mutations
        inside its transaction; either operation must observe the other's write.
        """
        collection = self.store._collection("policy_anchors").with_options(
            write_concern=WriteConcern("majority", wtimeout=3000)
        )
        try:
            async with asyncio.timeout(3):
                result = await collection.update_one(
                    {
                        "_id": anchor["_id"],
                        "org_id": anchor["org_id"],
                        "revision": anchor["revision"],
                        "status": "active",
                    },
                    {"$inc": {"fence": 1}},
                )
        except TimeoutError:
            raise PolicyConflict("policy confirmation timed out; refresh bounded filters") from None
        if result.matched_count != 1:
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
        teams: dict[str, Row] = {}
        links: dict[tuple[str, str], Row] = {}
        if self.human_management:
            team_records = (
                await self.store._collection("teams")
                .find({"org_id": org_id}, session=tx)
                .limit(MAX_ASSIGNMENTS + 1)
                .to_list(MAX_ASSIGNMENTS + 1)
            )
            link_records = (
                await self.store._collection("team_memberships")
                .find({"org_id": org_id}, session=tx)
                .limit(MAX_ASSIGNMENTS + 1)
                .to_list(MAX_ASSIGNMENTS + 1)
            )
            if max(len(team_records), len(link_records)) > MAX_ASSIGNMENTS:
                raise PolicyDenied("organization directory exceeds bounded policy capacity")
            teams = {r["team_id"]: r for r in team_records}
            links = {(r["team_id"], r["user_id"]): r for r in link_records}
        tuples, explanations = effective(
            org_id,
            subject_id,
            assignments,
            members,
            users,
            utc_now(),
            teams if self.human_management else None,
            links if self.human_management else None,
        )
        return {
            "anchor": anchor,
            **({"teams": teams, "team_links": links} if self.human_management else {}),
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
        session_id: str | None = None,
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
            session_id=session_id,
            engine=self,
            refresh=refresh,
            transaction_check=transaction_check,
        )

    @staticmethod
    def require(snapshot: Row, action: str, kind: str, identifier: str) -> None:
        from app.management.boundaries import permits_member

        if (
            kind == "member"
            and snapshot.get("teams") is not None
            and "team_links" in snapshot
            and permits_member(snapshot, action, identifier)
        ):
            return
        if not any(
            g.action == action
            and g.resource_kind == kind
            and g.selection().contains(identifier)
            and not (
                kind == "member"
                and "team_links" in snapshot
                and self_scoped_lead(snapshot, action, identifier)
            )
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
        event: Row = {
            "_id": event_id,
            "org_id": org_id,
            "actor_id": actor_id,
            "action": action,
            "target_id": target_id,
            "approval_reference": approval,
            "change": dict(change or {}),
            "created_at": utc_now(),
        }
        if self.managed_agents:
            # New attribution is captured in the committing transaction. Never
            # reconstruct historical team labels by joining today's directory.
            member = await self.store.find(
                "organization_memberships",
                {"org_id": org_id, "user_id": actor_id, "status": "active"},
                tx,
            )
            links = (
                await self.store.list(
                    "team_memberships",
                    {"org_id": org_id, "user_id": actor_id, "status": "active"},
                    tx,
                )
                if member
                else []
            )
            team = (
                await self.store.find(
                    "teams",
                    {"org_id": org_id, "team_id": links[0]["team_id"], "status": "active"},
                    tx,
                )
                if len(links) == 1
                else None
            )
            event["change"]["_event_v1"] = {
                "verified": bool(member and member.get("verified_at"))
                or actor_id == "installation_operator",
                "actor_kind": "operator" if actor_id == "installation_operator" else "human",
                "team_id": team["team_id"] if team else None,
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
        if policy.subject_kind == "machine":
            if tx is None or policy.transaction_check is None:
                raise PolicyDenied("current machine transaction check is required")
            return {"anchor": await self.anchor(policy.org_id, tx)}
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
        """Resolve current verified machines, or the sealed earlier-phase adapter.

        The schema42 path never falls back to a legacy admin adapter. Ordinary
        REST, HTTP/stdio MCP and job rechecks share this decision.
        """
        from app.security.tenant_policy import policy_of

        if self.managed_agents:
            from app.managed_agents.service import ManagedAgents

            return await ManagedAgents(self).resolve(principal)
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
            or row["mode"]
            != ("legacy_adoption_v41" if self.human_management else "legacy_adoption")
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
        if self.managed_agents:
            raise PolicyDenied(
                "legacy adapters are disabled; claim or revoke through machine cutover"
            )
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
                "mode": "legacy_adoption_v41" if self.human_management else "legacy_adoption",
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


def self_scoped_lead(snapshot: Row, action: str, identifier: str) -> bool:
    return (
        snapshot["member"]["role"] == "team_lead"
        and action.startswith(("members:", "grants:"))
        and not (action == "grants:read" and identifier == snapshot["member"]["user_id"])
    )
