"""Organization membership lifecycle, independent of global user status."""

from datetime import timedelta
from typing import Any, Literal

from app.identity.repository import Row
from app.management.boundaries import has
from app.management.directory import member_dto
from app.management.models import RootApproval
from app.models.common import utc_now
from app.security.policy.catalog import can_appoint, role_allows
from app.security.policy.evaluation import PolicyConflict, PolicyDenied, grants
from app.security.policy.governance import Governance
from app.security.policy.models import ceiling_hash


class Memberships(Governance):
    async def change_status(
        self,
        org_id: str,
        actor: str,
        user_id: str,
        revision: int,
        status: Literal["active", "suspended", "revoked"],
    ) -> Row:
        async def operation(snapshot: Row, tx: Any) -> Row:
            action = "members:reinstate" if status == "active" else "members:suspend"
            self.engine.require(snapshot, action, "member", user_id)
            if actor == user_id:
                raise PolicyDenied("self membership authority changes are refused")
            target = await self.store.find(
                "organization_memberships",
                {"org_id": org_id, "user_id": user_id, "revision": revision},
                tx,
            )
            if not target or not can_appoint(snapshot["member"]["role"], target["role"]):
                raise PolicyDenied("protected or unavailable membership")
            if status == target["status"] or (
                status == "active"
                and (target["status"] not in {"suspended", "revoked"} or not target["verified_at"])
            ):
                raise PolicyDenied("membership state changed or is unverified")
            if snapshot["member"]["role"] == "team_lead":
                if any(
                    link["status"] == "active"
                    and user == user_id
                    and not has(snapshot, action, "team", team)
                    for (team, user), link in snapshot["team_links"].items()
                ):
                    raise PolicyDenied(
                        "organization offboarding exceeds the delegated team boundary"
                    )
            updated = await self.store.update(
                "organization_memberships",
                {"_id": target["_id"], "org_id": org_id, "revision": revision},
                {"$set": {"status": status}, "$inc": {"revision": 1}},
                tx,
            )
            if not updated:
                raise PolicyConflict("membership changed")
            await self.store._collection("browser_sessions").update_many(
                {"org_id": org_id, "user_id": user_id, "revoked": False},
                {"$set": {"revoked": True}},
                session=tx,
            )
            await self.engine.audit(
                org_id,
                actor,
                "membership_" + status,
                target["_id"],
                tx,
                change={
                    "before_status": target["status"],
                    "after_status": status,
                    "before_revision": revision,
                    "after_revision": updated["revision"],
                },
            )
            return member_dto(updated)

        return await self.mutate(org_id, actor, operation)

    async def operator_approve_root(
        self, org_id: str, user_id: str, command: RootApproval, approval: str
    ) -> Row:
        """Explicit independent operator ceiling change. Not a browser self-grant.

        Replacing the reviewed root generation permanently invalidates existing
        descendants. It does not fabricate data-use rights from an owner role.
        """
        from app.security.policy.models import Issue
        from app.security.tenant import require_tenant
        from app.security.tenant_policy import current_policy

        if (
            require_tenant().org_id != org_id
            or current_policy() is not None
            or not approval
            or not self.engine.human_management
        ):
            raise PolicyDenied("explicit host operator review is required")
        Issue(
            subject_id=user_id,
            parent_id="operator-review",
            expected_parent_generation=1,
            **command.model_dump(exclude={"expires_at"}),
            expires_at=command.expires_at,
        )
        if not utc_now() < command.expires_at <= utc_now() + timedelta(days=365):
            raise PolicyDenied("bounded root approval expiry is required")
        if any(
            not role_allows("org_owner", g.action, explicit=True)
            for g in command.tuples
            if g.selector != "none"
        ):
            raise PolicyDenied(
                "operator management approval may assign only owner management capabilities"
            )
        async with self.engine.transaction() as tx:
            snapshot = await self.engine.snapshot(org_id, user_id, tx)
            if snapshot["member"]["role"] != "org_owner":
                raise PolicyDenied("verified active owner is required")
            roots = [
                r
                for r in snapshot["assignments"].values()
                if r["subject_id"] == user_id
                and r["parent_id"] is None
                and r["remaining_depth"] > 0
                and r["status"] == "active"
            ]
            if len(roots) != 1:
                raise PolicyDenied("a unique reviewed governance root is required")
            for target in command.targets.ids:
                if not await self.store.find(
                    "organization_memberships", {"org_id": org_id, "user_id": target}, tx
                ):
                    raise PolicyDenied("root target is outside the organization")
            await self.engine.validate_resources(org_id, command.tuples + command.ceiling, tx)
            await self.engine.fence(snapshot["anchor"], tx, mutation=True)
            root = roots[0]
            from app.identity.models import MANAGEMENT_ACTIONS

            preserved = tuple(g for g in grants(root) if g.action not in MANAGEMENT_ACTIONS)
            changes = {
                "tuples": [g.model_dump() for g in (*preserved, *command.tuples)],
                "ceiling": [g.model_dump() for g in command.ceiling],
                "ceiling_hash": ceiling_hash(command.ceiling),
                "targets": command.targets.model_dump(),
                "allowed_roles": list(command.allowed_roles),
                "expires_at": command.expires_at,
            }
            updated = await self.store.update(
                "policy_assignments",
                {"_id": root["_id"], "org_id": org_id, "revision": root["revision"]},
                {"$set": changes, "$inc": {"revision": 1, "generation": 1}},
                tx,
            )
            if not updated:
                raise PolicyConflict("root changed")
            await self.engine.audit(
                org_id,
                "installation_operator",
                "human_management_root_approved",
                root["_id"],
                tx,
                approval=approval,
                change={"before": root, "after": updated},
            )
            return {
                "assignment_id": root["_id"],
                "generation": updated["generation"],
                "descendants_require_new_approval": True,
            }
