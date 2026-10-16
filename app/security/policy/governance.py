"""Governance changes, audit and outbox commit together under the org fence."""

from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta
from typing import Any, Literal
from uuid import uuid4

from pymongo.errors import PyMongoError

from app.identity.repository import Row
from app.models.common import utc_now
from app.security.policy.catalog import HUMAN_ROLES, ROLE_PERMISSIONS, can_appoint
from app.security.policy.engine import PolicyEngine
from app.security.policy.evaluation import (
    PolicyConflict,
    PolicyDenied,
    active_chain,
    grants,
    validate_issue,
)
from app.security.policy.models import Grant, Issue, Selector, ceiling_hash, covered
from app.security.scopes import ALL_SCOPES


class Governance:
    def __init__(self, engine: PolicyEngine) -> None:
        self.engine, self.store = engine, engine.store

    async def operator_recover_corpus(
        self,
        org_id: str,
        user_id: str,
        tuples: tuple[Grant, ...],
        expires_at: datetime,
        approval: str,
    ) -> Row:
        """The documented independent operator exception; never a browser API.

        A use-only root cannot change the owner's existing delegation ceiling.
        Ownership transfer does not transfer these personal corpus assignments.
        """
        from app.security.tenant import require_tenant
        from app.security.tenant_policy import current_policy

        if require_tenant().org_id != org_id or current_policy() is not None:
            raise PolicyDenied("explicit host operator recovery is required")
        if (
            not approval
            or not tuples
            or any(
                g.action not in ALL_SCOPES
                or g.action
                in {"agent:manage", "system:read", "agent:self", "session:write", "audit:self:read"}
                for g in tuples
            )
            or not utc_now() < expires_at <= utc_now() + timedelta(days=30)
        ):
            raise PolicyDenied("bounded independently approved corpus tuples are required")
        async with self.engine.transaction() as tx:
            snapshot = await self.engine.snapshot(org_id, user_id, tx)
            if snapshot["member"]["role"] != "org_owner":
                raise PolicyDenied("owner recovery requires a verified active owner")
            await self.engine.validate_resources(org_id, tuples, tx)
            await self.engine.fence(snapshot["anchor"], tx, mutation=True)
            row = self.assignment(
                "assignment_" + uuid4().hex,
                org_id,
                snapshot["member"],
                "installation_operator",
                tuples,
                (),
                Selector(),
                (),
                0,
                expires_at,
            )
            await self.store.insert("policy_assignments", row, tx)
            await self.engine.audit(
                org_id,
                "installation_operator",
                "owner_corpus_recovery",
                row["_id"],
                tx,
                approval=approval,
                change={"after": row},
            )
            return row

    async def mutate(
        self, org_id: str, subject_id: str, operation: Callable[[Row, Any], Awaitable[Row]]
    ) -> Row:
        # Only governance commands retry; EVERY retry rereads policy and ancestry.
        # Expected resource/generation revisions in the command remain mandatory.
        for attempt in range(3):
            try:
                async with self.engine.transaction() as tx:
                    from app.security.tenant_policy import current_policy

                    context = current_policy()
                    if context is not None and context.managed:
                        if (
                            context.org_id != org_id
                            or context.subject_id != subject_id
                            or context.transaction_check is None
                        ):
                            raise PolicyDenied("trusted current human context is required")
                        await context.transaction_check(tx)
                    snapshot = await self.engine.snapshot(org_id, subject_id, tx)
                    await self.engine.fence(snapshot["anchor"], tx, mutation=True)
                    return await operation(snapshot, tx)
            except PyMongoError as error:
                if attempt == 2 or not error.has_error_label("TransientTransactionError"):
                    raise PolicyConflict("governance could not commit; refresh access") from None
        raise PolicyConflict("governance could not commit")

    async def bootstrap(
        self,
        org_id: str,
        user_id: str,
        *,
        targets: Selector,
        allowed_roles: tuple[str, ...],
        expires_at: datetime,
        approval: str,
        approve_management_template: bool,
    ) -> Row:
        await self.engine.enabled()
        if (
            not approval
            or not approve_management_template
            or not utc_now() < expires_at <= utc_now() + timedelta(days=365)
            or any(r not in HUMAN_ROLES or r == "org_owner" for r in allowed_roles)
        ):
            raise PolicyDenied("explicit bounded operator bootstrap approval is required")
        async with self.engine.transaction() as tx:
            member = await self.store.find(
                "organization_memberships", {"org_id": org_id, "user_id": user_id}, tx
            )
            user = await self.store.find("users", {"_id": user_id, "status": "active"}, tx)
            org = await self.store.find("organizations", {"org_id": org_id, "status": "active"}, tx)
            intent = (member or {}).get("bootstrap_intent")
            if (
                not member
                or not user
                or not org
                or member["status"] != "active"
                or not member["verified_at"]
                or not member["bootstrap_owner"]
                or member["role"] != "org_owner"
                or not intent
            ):
                raise PolicyDenied("verified recorded owner intent is required")
            existing = await self.store.find("policy_anchors", {"_id": org_id}, tx)
            if existing:
                if intent["state"] == "activated_phase5":
                    return {"assignment_id": intent["assignment_id"], "already_activated": True}
                raise PolicyDenied("organization policy is already initialized")
            if intent["state"] != "pending_phase5" or not intent.get("approval_reference"):
                raise PolicyDenied("recorded operator approval is required")
            for identifier in targets.ids:
                if not await self.store.find(
                    "organization_memberships", {"org_id": org_id, "user_id": identifier}, tx
                ):
                    raise PolicyDenied("bootstrap target is outside the organization")
            root = tuple(Grant.model_validate(g) for g in intent["root_delegation"])
            own = tuple(Grant.model_validate(g) for g in intent["bootstrap_policy"])
            await self.engine.validate_resources(org_id, root + own, tx)
            management: list[Grant] = []
            kind: Literal["organization", "member"]
            for action in sorted(ROLE_PERMISSIONS["org_owner"]):
                if action.startswith(("teams:",)):
                    # No team management runtime exists until Phase 6.
                    continue
                if action.startswith(("members:", "grants:")) and action != "grants:all-in-org":
                    selection, kind = targets, "member"
                elif action.endswith(":own") or action == "activity:read:self":
                    selection, kind = Selector(mode="include", ids=(user_id,)), "member"
                else:
                    selection, kind = Selector(mode="include", ids=(org_id,)), "organization"
                management.append(
                    Grant(
                        action=action,
                        resource_kind=kind,
                        selector=selection.mode,
                        ids=list(selection.ids),
                    )
                )
            management.append(
                Grant(
                    action="grants:read", resource_kind="member", selector="include", ids=[user_id]
                )
            )
            identifier = "assignment_" + uuid4().hex
            row = self.assignment(
                identifier,
                org_id,
                member,
                "installation_operator",
                own + tuple(management),
                root,
                targets,
                allowed_roles,
                3,
                expires_at,
            )
            await self.store.insert(
                "policy_anchors",
                {
                    "_id": org_id,
                    "org_id": org_id,
                    "revision": 1,
                    "fence": 1,
                    "status": "active",
                    "created_at": utc_now(),
                },
                tx,
            )
            await self.store.insert("policy_assignments", row, tx)
            intent = {
                **intent,
                "state": "activated_phase5",
                "assignment_id": identifier,
                "activation_approval": approval,
                "activated_at": utc_now(),
            }
            await self.store.update(
                "organization_memberships",
                {"_id": member["_id"], "revision": member["revision"]},
                {"$set": {"bootstrap_intent": intent}},
                tx,
            )
            await self.engine.audit(
                org_id,
                "installation_operator",
                "bootstrap_activated",
                identifier,
                tx,
                approval=approval,
                change={"after": row},
            )
            return {"assignment_id": identifier, "already_activated": False}

    @staticmethod
    def assignment(
        identifier: str,
        org_id: str,
        member: Row,
        issuer: str,
        tuples: tuple[Grant, ...],
        ceiling: tuple[Grant, ...],
        targets: Selector,
        roles: tuple[str, ...],
        depth: int,
        expiry: datetime,
        parent: Row | None = None,
    ) -> Row:
        return {
            "_id": identifier,
            "org_id": org_id,
            "subject_kind": "human",
            "subject_id": member["user_id"],
            "subject_revision": member["revision"],
            "role": member["role"],
            "tuples": [g.model_dump() for g in tuples],
            "ceiling": [g.model_dump() for g in ceiling],
            "ceiling_hash": ceiling_hash(ceiling),
            "targets": targets.model_dump(mode="json"),
            "allowed_roles": list(roles),
            "remaining_depth": depth,
            "parent_id": parent["_id"] if parent else None,
            "parent_generation": parent["generation"] if parent else 0,
            "issuer_id": issuer,
            "generation": 1,
            "revision": 1,
            "status": "active",
            "starts_at": utc_now(),
            "expires_at": expiry,
            "created_at": utc_now(),
        }

    async def issue(self, org_id: str, issuer_id: str, request: Issue) -> Row:
        async def operation(snapshot: Row, tx: Any) -> Row:
            self.engine.require(snapshot, "grants:delegate", "member", request.subject_id)
            parent = snapshot["assignments"].get(request.parent_id)
            if not parent:
                raise PolicyDenied("parent authority is unavailable")
            active_chain(
                parent, snapshot["assignments"], snapshot["members"], snapshot["users"], utc_now()
            )
            validate_issue(parent, request, issuer_id, utc_now())
            await self.engine.validate_resources(org_id, request.tuples + request.ceiling, tx)
            member = await self.store.find(
                "organization_memberships",
                {"org_id": org_id, "user_id": request.subject_id, "status": "active"},
                tx,
            )
            user = await self.store.find(
                "users", {"_id": request.subject_id, "status": "active"}, tx
            )
            if not member or not user or not member["verified_at"]:
                raise PolicyDenied("verified organization target is required")
            for target in request.targets.ids:
                if not await self.store.find(
                    "organization_memberships", {"org_id": org_id, "user_id": target}, tx
                ):
                    raise PolicyDenied("delegation target is outside the organization")
            if member["role"] != request.role:
                if member["role"] == "org_owner":
                    raise PolicyDenied("ownership requires an accepted transfer")
                member = await self.store.update(
                    "organization_memberships",
                    {"_id": member["_id"], "revision": member["revision"]},
                    {"$set": {"role": request.role}, "$inc": {"revision": 1}},
                    tx,
                )
                if member is None:
                    raise PolicyConflict("membership changed")
            row = self.assignment(
                "assignment_" + uuid4().hex,
                org_id,
                member,
                issuer_id,
                request.tuples,
                request.ceiling,
                request.targets,
                request.allowed_roles,
                request.remaining_depth,
                request.expires_at,
                parent,
            )
            await self.store.insert("policy_assignments", row, tx)
            await self.engine.audit(
                org_id, issuer_id, "assignment_issued", row["_id"], tx, change={"after": row}
            )
            return row

        return await self.mutate(org_id, issuer_id, operation)

    async def change(
        self,
        org_id: str,
        issuer_id: str,
        assignment_id: str,
        expected_revision: int,
        *,
        tuples: tuple[Grant, ...] | None = None,
        revoke: bool = False,
    ) -> Row:
        async def operation(snapshot: Row, tx: Any) -> Row:
            row = snapshot["assignments"].get(assignment_id)
            if not row or row["subject_id"] == issuer_id or row["revision"] != expected_revision:
                raise PolicyDenied("assignment change was not accepted")
            self.engine.require(snapshot, "grants:delegate", "member", row["subject_id"])
            # Only the issuer or an active ancestor may narrow/revoke. No widening
            # endpoint exists: regrant creates a new ID and fresh lineage.
            chain = active_chain(
                row, snapshot["assignments"], snapshot["members"], snapshot["users"], utc_now()
            )
            if not any(p["subject_id"] == issuer_id for p in chain[1:]):
                raise PolicyDenied("assignment is outside the current delegation lineage")
            changes: Row = {"status": "revoked" if revoke else "active"}
            if not revoke:
                if tuples is None or any(not covered(g, grants(row)) for g in tuples):
                    raise PolicyDenied("widening requires a new independently approved assignment")
                changes["tuples"] = [g.model_dump() for g in tuples]
                changes["ceiling"] = [
                    g.model_dump() for g in tuples if covered(g, grants(row, "ceiling"))
                ]
                changes["ceiling_hash"] = ceiling_hash(
                    tuple(Grant.model_validate(g) for g in changes["ceiling"])
                )
            updated = await self.store.update(
                "policy_assignments",
                {"_id": assignment_id, "org_id": org_id, "revision": expected_revision},
                {"$set": changes, "$inc": {"revision": 1, "generation": 1}},
                tx,
            )
            if not updated:
                raise PolicyConflict("assignment changed")
            await self.engine.audit(
                org_id,
                issuer_id,
                "assignment_revoked" if revoke else "assignment_narrowed",
                assignment_id,
                tx,
                change={"before": row, "after": updated},
            )
            return updated

        return await self.mutate(org_id, issuer_id, operation)

    async def membership_status(
        self,
        org_id: str,
        issuer_id: str,
        target_id: str,
        expected_revision: int,
        *,
        suspended: bool,
    ) -> Row:
        async def operation(snapshot: Row, tx: Any) -> Row:
            if issuer_id == target_id:
                raise PolicyDenied("self authority changes are refused")
            action = "members:suspend" if suspended else "members:reinstate"
            self.engine.require(snapshot, action, "member", target_id)
            target = await self.store.find(
                "organization_memberships",
                {"org_id": org_id, "user_id": target_id, "revision": expected_revision},
                tx,
            )
            if not target or not can_appoint(snapshot["member"]["role"], target["role"]):
                # Owners can only be demoted by accepted transfer, even when a
                # second owner exists. This also enforces the last-owner rule.
                raise PolicyDenied("membership authority change is refused")
            if (suspended and target["status"] != "active") or (
                not suspended and target["status"] != "suspended"
            ):
                raise PolicyDenied("membership state changed")
            updated = await self.store.update(
                "organization_memberships",
                {"_id": target["_id"], "revision": expected_revision},
                {
                    "$set": {"status": "suspended" if suspended else "active"},
                    "$inc": {"revision": 1},
                },
                tx,
            )
            await self.engine.audit(
                org_id,
                issuer_id,
                action,
                target["_id"],
                tx,
                change={
                    "before_status": target["status"],
                    "after_status": (updated or {}).get("status"),
                    "before_revision": expected_revision,
                    "after_revision": expected_revision + 1,
                },
            )
            return updated or {}

        return await self.mutate(org_id, issuer_id, operation)

    async def offer_transfer(self, org_id: str, owner_id: str, target_id: str) -> Row:
        async def operation(snapshot: Row, tx: Any) -> Row:
            self.engine.require(snapshot, "org:transfer-owner", "organization", org_id)
            if owner_id == target_id or snapshot["member"]["role"] != "org_owner":
                raise PolicyDenied("distinct verified owner acceptance is required")
            member = await self.store.find(
                "organization_memberships",
                {"org_id": org_id, "user_id": target_id, "status": "active"},
                tx,
            )
            user = await self.store.find("users", {"_id": target_id, "status": "active"}, tx)
            if not member or not member["verified_at"] or not user or member["role"] == "org_owner":
                raise PolicyDenied("verified target membership is required")
            row = {
                "_id": "transfer_" + uuid4().hex,
                "org_id": org_id,
                "owner_id": owner_id,
                "target_id": target_id,
                "owner_revision": snapshot["member"]["revision"],
                "target_revision": member["revision"],
                "policy_revision": snapshot["anchor"]["revision"] + 1,
                "status": "pending",
                "expires_at": utc_now() + timedelta(minutes=10),
            }
            await self.store.insert("ownership_transfers", row, tx)
            await self.engine.audit(
                org_id,
                owner_id,
                "ownership_transfer_offered",
                row["_id"],
                tx,
                change={"after": row},
            )
            return row

        return await self.mutate(org_id, owner_id, operation)

    async def accept_transfer(self, org_id: str, target_id: str, transfer_id: str) -> Row:
        async def operation(snapshot: Row, tx: Any) -> Row:
            offer = await self.store.find(
                "ownership_transfers",
                {"_id": transfer_id, "org_id": org_id, "target_id": target_id, "status": "pending"},
                tx,
            )
            if (
                not offer
                or offer["expires_at"] <= utc_now()
                or offer["target_revision"] != snapshot["member"]["revision"]
                or offer["policy_revision"] != snapshot["anchor"]["revision"]
            ):
                raise PolicyDenied("ownership transfer is stale")
            owner = await self.store.find(
                "organization_memberships",
                {
                    "org_id": org_id,
                    "user_id": offer["owner_id"],
                    "revision": offer["owner_revision"],
                    "status": "active",
                    "role": "org_owner",
                },
                tx,
            )
            if not owner:
                raise PolicyDenied("current owner is unavailable")
            roots = [
                r
                for r in snapshot["assignments"].values()
                if r["subject_id"] == owner["user_id"]
                and r["parent_id"] is None
                and r["remaining_depth"] > 0
            ]
            if len(roots) != 1:
                raise PolicyDenied(
                    "explicit operator recovery is required for this ownership lineage"
                )
            root = roots[0]
            active_chain(
                root, snapshot["assignments"], snapshot["members"], snapshot["users"], utc_now()
            )
            member = await self.store.update(
                "organization_memberships",
                {"_id": snapshot["member"]["_id"], "revision": offer["target_revision"]},
                {"$set": {"role": "org_owner"}, "$inc": {"revision": 1}},
                tx,
            )
            if not member:
                raise PolicyConflict("target membership changed")
            # Transfer the existing explicit root ceiling and directory policy;
            # no new corpus tuples are invented by the owner title.
            new = self.assignment(
                "assignment_" + uuid4().hex,
                org_id,
                member,
                "installation_operator",
                grants(root),
                grants(root, "ceiling"),
                Selector.model_validate(root["targets"]),
                tuple(root["allowed_roles"]),
                root["remaining_depth"],
                root["expires_at"],
            )
            # Own-subject metadata stays with the accepted owner. The corpus
            # and delegation ceilings retain their exact existing resources.
            for entry in new["tuples"]:
                if (
                    entry["action"].endswith(":own")
                    or entry["action"] == "activity:read:self"
                    or (entry["action"] == "grants:read" and entry["ids"] == [owner["user_id"]])
                ):
                    entry["ids"] = [target_id]
            await self.store.insert("policy_assignments", new, tx)
            await self.store.update(
                "policy_assignments",
                {"_id": root["_id"]},
                {"$set": {"status": "revoked"}, "$inc": {"revision": 1, "generation": 1}},
                tx,
            )
            await self.store.update(
                "organization_memberships",
                {"_id": owner["_id"], "revision": owner["revision"]},
                {"$set": {"role": "contributor"}, "$inc": {"revision": 1}},
                tx,
            )
            await self.store.update(
                "ownership_transfers",
                {"_id": transfer_id, "status": "pending"},
                {"$set": {"status": "accepted"}},
                tx,
            )
            await self.engine.audit(
                org_id,
                target_id,
                "ownership_transfer_accepted",
                transfer_id,
                tx,
                change={"previous_root": root, "new_root": new},
            )
            return {"assignment_id": new["_id"], "reauthentication_required": True}

        return await self.mutate(org_id, target_id, operation)
