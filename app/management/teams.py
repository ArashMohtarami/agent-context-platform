"""Team lifecycle and membership edges; reinstatement never rewinds a generation."""

from typing import Any
from uuid import uuid4

from app.identity.repository import Row
from app.management.boundaries import has
from app.management.models import TeamChange, TeamCreate, TeamMemberChange
from app.models.common import utc_now
from app.security.policy.catalog import can_appoint
from app.security.policy.evaluation import PolicyConflict, PolicyDenied
from app.security.policy.governance import Governance


class Teams(Governance):
    def require_manage(self, snapshot: Row, team_id: str) -> None:
        if not has(snapshot, "teams:manage", "organization", snapshot["anchor"]["org_id"]):
            self.engine.require(snapshot, "teams:manage", "team", team_id)

    async def create(self, org_id: str, actor: str, command: TeamCreate) -> Row:
        async def operation(snapshot: Row, tx: Any) -> Row:
            self.engine.require(snapshot, "teams:manage", "organization", org_id)
            if await self.store.find("teams", {"org_id": org_id, "team_id": command.team_id}, tx):
                raise PolicyConflict("team already exists")
            row = {
                "_id": "team_" + uuid4().hex,
                "org_id": org_id,
                **command.model_dump(),
                "status": "active",
                "revision": 1,
                "generation": 1,
                "created_at": utc_now(),
            }
            await self.store.insert("teams", row, tx)
            await self.engine.audit(org_id, actor, "team_created", row["team_id"], tx)
            return row

        return await self.mutate(org_id, actor, operation)

    async def change_team(self, org_id: str, actor: str, team_id: str, command: TeamChange) -> Row:
        async def operation(snapshot: Row, tx: Any) -> Row:
            self.require_manage(snapshot, team_id)
            team = snapshot["teams"].get(team_id)
            if not team or team["revision"] != command.expected_revision:
                raise PolicyConflict("team changed or is unavailable")
            changes = command.model_dump(exclude_none=True, exclude={"expected_revision"})
            if not changes:
                raise PolicyDenied("empty team change")
            status_change = "status" in changes and changes["status"] != team["status"]
            updated = await self.store.update(
                "teams",
                {"_id": team["_id"], "org_id": org_id, "revision": command.expected_revision},
                {
                    "$set": changes,
                    "$inc": {"revision": 1, **({"generation": 1} if status_change else {})},
                },
                tx,
            )
            if not updated:
                raise PolicyConflict("team changed")
            if status_change:
                users = [
                    user
                    for (t, user), link in snapshot["team_links"].items()
                    if t == team_id and link["status"] == "active"
                ]
                await self.store._collection("browser_sessions").update_many(
                    {"org_id": org_id, "user_id": {"$in": users}, "revoked": False},
                    {"$set": {"revoked": True}},
                    session=tx,
                )
            await self.engine.audit(
                org_id,
                actor,
                "team_changed",
                team_id,
                tx,
                change={"before": team, "after": updated},
            )
            return updated

        return await self.mutate(org_id, actor, operation)

    async def change_member(
        self, org_id: str, actor: str, team_id: str, user_id: str, command: TeamMemberChange
    ) -> Row:
        async def operation(snapshot: Row, tx: Any) -> Row:
            team = snapshot["teams"].get(team_id)
            if (
                not team
                or team["status"] != "active"
                or team["revision"] != command.expected_team_revision
            ):
                raise PolicyConflict("team changed or is unavailable")
            if actor == user_id:
                raise PolicyDenied("self team authority changes are refused")
            manage = has(snapshot, "teams:manage", "organization", org_id) or has(
                snapshot, "teams:manage", "team", team_id
            )
            if not manage:
                self.engine.require(
                    snapshot,
                    (
                        "members:reinstate"
                        if snapshot["team_links"].get((team_id, user_id))
                        else "members:invite"
                    )
                    if command.status == "active"
                    else "members:suspend",
                    "team",
                    team_id,
                )
            member = await self.store.find(
                "organization_memberships",
                {
                    "org_id": org_id,
                    "user_id": user_id,
                    "status": "active",
                    "verified_at": {"$ne": None},
                },
                tx,
            )
            if not member or not can_appoint(snapshot["member"]["role"], member["role"]):
                raise PolicyDenied("team target is outside the approved membership boundary")
            existing = snapshot["team_links"].get((team_id, user_id))
            # Leads may enroll a new invite at acceptance, but cannot move an
            # existing person into their authority without administrator approval.
            if not manage and (not existing or existing["status"] == "revoked"):
                raise PolicyDenied(
                    "existing member team placement requires explicit team management"
                )
            if (existing["revision"] if existing else 0) != command.expected_revision:
                raise PolicyConflict("team membership changed")
            if not existing:
                if command.status != "active":
                    raise PolicyDenied("team membership is unavailable")
                new_row: Row = {
                    "_id": "team_member_" + uuid4().hex,
                    "org_id": org_id,
                    "team_id": team_id,
                    "user_id": user_id,
                    "revision": 1,
                    "status": "active",
                    "created_at": utc_now(),
                }
                await self.store.insert("team_memberships", new_row, tx)
                row: Row | None = new_row
            else:
                if command.status == existing["status"]:
                    raise PolicyDenied("team membership state is unchanged")
                row = await self.store.update(
                    "team_memberships",
                    {
                        "_id": existing["_id"],
                        "org_id": org_id,
                        "revision": command.expected_revision,
                    },
                    {"$set": {"status": command.status}, "$inc": {"revision": 1}},
                    tx,
                )
                if not row:
                    raise PolicyConflict("team membership changed")
            if row is None:
                raise PolicyConflict("team membership changed")
            await self.store._collection("browser_sessions").update_many(
                {"org_id": org_id, "user_id": user_id, "revoked": False},
                {"$set": {"revoked": True}},
                session=tx,
            )
            await self.engine.audit(
                org_id,
                actor,
                "team_membership_changed",
                row["_id"],
                tx,
                change={"before_revision": command.expected_revision, "after": row},
            )
            return row

        return await self.mutate(org_id, actor, operation)
