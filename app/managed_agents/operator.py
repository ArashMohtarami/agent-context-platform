"""Installation-only legacy reconciliation. No machine key can invoke this."""

from hashlib import sha256
from typing import Any
from uuid import uuid4

from app.managed_agents.models import LegacyClaim
from app.managed_agents.service import ManagedAgents, receipt
from app.models.common import utc_now
from app.security.policy.evaluation import PolicyDenied
from app.security.policy.models import covered
from app.security.tenant import require_tenant
from app.security.tenant_policy import current_policy


class MachineCutover(ManagedAgents):
    def operator(self, org_id: str, approval: str) -> None:
        context = current_policy()
        if (
            require_tenant().org_id != org_id
            or (context is not None and not context.operator)
            or not approval.strip()
            or len(approval) > 512
        ):
            raise PolicyDenied("explicit reviewed installation operator context required")

    async def inventory(self, org_id: str, approval: str) -> dict[str, Any]:
        self.operator(org_id, approval)
        rows = (
            await self.db["api_keys"]
            .find(
                {"org_id": org_id, "status": "active"},
                {"key_id": 1, "client_id": 1, "expires_at": 1},
            )
            .limit(1001)
            .to_list(1001)
        )
        if len(rows) > 1000:
            raise PolicyDenied("bounded operator key inventory exceeded")
        return {
            "org_id": org_id,
            "keys": [{k: v for k, v in r.items() if k != "_id"} for r in rows],
        }

    async def claim(self, org_id: str, command: LegacyClaim, approval: str) -> dict[str, Any]:
        self.operator(org_id, approval)
        async with self.engine.transaction() as tx:
            anchor = await self.engine.anchor(org_id, tx)
            await self.engine.fence(anchor, tx, mutation=True)
            row, _, effective = await self.current(org_id, command.client_id, tx)
            key = await self.db["api_keys"].find_one(
                {"org_id": org_id, "key_id": command.key_id, "status": "active"}, session=tx
            )
            if (
                not key
                or key.get("identity_version") != 1
                or not key.get("legacy_adoption_id")
                or key.get("client_id") not in {None, command.client_id}
                or row["revision"] != command.expected_client_revision
            ):
                raise PolicyDenied("only explicitly reviewed adopted keys may be claimed")
            if (
                not utc_now() < command.expires_at <= row["expires_at"]
                or (key.get("expires_at") and command.expires_at > key["expires_at"])
                or any(
                    not covered(g, effective)
                    or (g.action not in key["scopes"] and g.action != "reviews:decide")
                    for g in command.tuples
                )
            ):
                raise PolicyDenied("legacy claim exceeds key/current sponsor policy")
            await self.engine.validate_resources(org_id, command.tuples, tx)
            existing = await self.store.find(
                "managed_credentials", {"org_id": org_id, "key_id": command.key_id}, tx
            )
            if existing:
                raise PolicyDenied("legacy key already classified; revoke or replace")
            record = {
                "_id": "credential_" + uuid4().hex,
                "org_id": org_id,
                "client_id": command.client_id,
                "key_id": command.key_id,
                "recipient_id": row["sponsor_id"],
                "issuance_id": "claim-" + command.key_id,
                "command_hash": sha256(command.model_dump_json().encode()).hexdigest(),
                "client_revision": row["revision"],
                "tuples": [g.model_dump() for g in command.tuples],
                "status": "active",
                "revision": 1,
                "created_at": utc_now(),
                "expires_at": command.expires_at,
                "replaces_key_id": None,
                "claimed": True,
            }
            await self.store.insert("managed_credentials", record, tx)
            await self.db["api_keys"].update_one(
                {"_id": key["_id"], "org_id": org_id},
                {"$set": {"client_id": command.client_id}, "$inc": {"revision": 1}},
                session=tx,
            )
            await self.engine.audit(
                org_id,
                "installation_operator",
                "legacy_key_verified_claim",
                command.key_id,
                tx,
                approval=approval,
                change={"receipt": receipt(record)},
            )
            return receipt(record)

    async def revoke_legacy(self, org_id: str, key_id: str, approval: str) -> dict[str, Any]:
        self.operator(org_id, approval)
        async with self.engine.transaction() as tx:
            anchor = await self.engine.anchor(org_id, tx)
            await self.engine.fence(anchor, tx, mutation=True)
            key = await self.db["api_keys"].find_one(
                {"org_id": org_id, "key_id": key_id}, session=tx
            )
            if not key:
                raise PolicyDenied("key unavailable")
            await self.db["api_keys"].update_one(
                {"_id": key["_id"], "org_id": org_id},
                {"$set": {"status": "revoked", "revoked_at": utc_now()}, "$inc": {"revision": 1}},
                session=tx,
            )
            await self.engine.audit(
                org_id,
                "installation_operator",
                "unmanaged_key_revoked",
                key_id,
                tx,
                approval=approval,
            )
            return {"key_id": key_id, "status": "revoked"}

    async def activate(self, org_id: str, approval: str) -> dict[str, Any]:
        self.operator(org_id, approval)
        # Installation-wide activation: lock every organization in deterministic
        # order, preventing a key issue/claim/revoke from escaping reconciliation.
        async with self.engine.transaction() as tx:
            anchors = (
                await self.db["policy_anchors"]
                .find({"status": "active"}, session=tx)
                .sort("_id", 1)
                .limit(1001)
                .to_list(1001)
            )
            if len(anchors) > 1000:
                raise PolicyDenied("bounded organization inventory exceeded")
            for anchor in anchors:
                await self.engine.fence(anchor, tx, mutation=True)
            keys = (
                await self.db["api_keys"]
                .find({"status": "active"}, session=tx)
                .limit(1001)
                .to_list(1001)
            )
            if len(keys) > 1000:
                raise PolicyDenied("bounded key inventory exceeded")
            for key in keys:
                if key.get("expires_at") and key["expires_at"] <= utc_now():
                    raise PolicyDenied("expired legacy key metadata must be explicitly revoked")
                cred = await self.store.find(
                    "managed_credentials",
                    {"org_id": key["org_id"], "key_id": key["key_id"], "status": "active"},
                    tx,
                )
                if (
                    not cred
                    or cred["client_id"] != key.get("client_id")
                    or cred["expires_at"] <= utc_now()
                ):
                    raise PolicyDenied(
                        "all active legacy keys must be explicitly claimed or revoked"
                    )
                row, _, _ = await self.current(key["org_id"], cred["client_id"], tx)
                if row["revision"] != cred["client_revision"]:
                    raise PolicyDenied("stale claimed key requires replacement/revocation")
            await self.store.update(
                "_managed_agents_control",
                {"_id": "installation", "schema_version": 42},
                {"$set": {"activated": True}},
                tx,
            )
            await self.engine.audit(
                org_id,
                "installation_operator",
                "managed_machine_access_activated",
                org_id,
                tx,
                approval=approval,
                change={"classified_active_keys": len(keys)},
            )
            return {
                "schema_version": 42,
                "managed_machine_access": True,
                "classified_active_keys": len(keys),
            }
