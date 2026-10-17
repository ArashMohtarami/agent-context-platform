"""Reviewed invitation proposals; authority is issued only after verified acceptance."""

from datetime import timedelta
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from pymongo.errors import PyMongoError

from app.identity.crypto import digest
from app.identity.mail import Message
from app.identity.repository import Row
from app.management.boundaries import has, validate_parent
from app.management.models import InviteCreate, ReviewedPolicy
from app.models.common import utc_now
from app.security.policy.catalog import can_appoint
from app.security.policy.evaluation import PolicyConflict, PolicyDenied, grants
from app.security.policy.governance import Governance
from app.security.policy.models import Issue

if TYPE_CHECKING:
    from app.identity.service import LocalIdentity


class Invitations(Governance):
    def __init__(self, local: "LocalIdentity") -> None:
        if local.policy_engine is None or not local.policy_engine.human_management:
            raise PolicyDenied("human management is disabled")
        super().__init__(local.policy_engine)
        self.local = local

    async def recipient(
        self, org_id: str, email: str, display_name: str, tx: Any
    ) -> tuple[Row, Row]:
        from app.identity.service import local_subject

        subject = local_subject(email)
        identity = await self.store.find(
            "user_identities", {"provider": "local", "subject": subject}, tx
        )
        if identity:
            user = await self.store.find(
                "users",
                {
                    "_id": identity["user_id"],
                    "status": {"$in": ["pending", "verification_pending", "active"]},
                },
                tx,
            )
            if not user:
                raise PolicyDenied("invitation could not be created")
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
        member = await self.store.find(
            "organization_memberships", {"org_id": org_id, "user_id": user["_id"]}, tx
        )
        if member:
            if member["status"] not in {"active", "invited", "verification_pending"} or (
                member["status"] == "active" and not member["verified_at"]
            ):
                raise PolicyDenied("invitation could not be created")
            if member["status"] != "active":
                updated = await self.store.update(
                    "organization_memberships",
                    {"_id": member["_id"], "org_id": org_id, "revision": member["revision"]},
                    {"$set": {"status": "invited"}, "$inc": {"revision": 1}},
                    tx,
                )
                if not updated:
                    raise PolicyConflict("pending membership changed")
                member = updated
        else:
            member = {
                "_id": "member_" + uuid4().hex,
                "org_id": org_id,
                "user_id": user["_id"],
                "display_name": display_name,
                "role": "contributor",
                "status": "invited",
                "verified_at": None,
                "revision": 1,
                "fence": 0,
                "bootstrap_owner": False,
                "bootstrap_intent": None,
                "created_at": utc_now(),
            }
            await self.store.insert("organization_memberships", member, tx)
        return user, member

    async def create(self, org_id: str, actor: str, command: InviteCreate) -> Row:
        from app.identity.service import local_subject

        if not utc_now() < command.expires_at <= utc_now() + timedelta(days=7):
            raise PolicyDenied("invitation requires an expiry within seven days")
        delivery: list[Message] = []

        async def operation(snapshot: Row, tx: Any) -> Row:
            user, member = await self.recipient(org_id, command.email, command.display_name, tx)
            if not can_appoint(snapshot["member"]["role"], member["role"]):
                raise PolicyDenied("current target role is outside the issuer's authority")
            previous = await self.store.list(
                "management_invitations",
                {"org_id": org_id, "membership_id": member["_id"], "status": "pending"},
                tx,
            )
            if len(previous) > 100:
                raise PolicyDenied("pending invitation capacity exceeded")
            await self.store._collection("management_invitations").update_many(
                {"org_id": org_id, "membership_id": member["_id"], "status": "pending"},
                {"$set": {"status": "revoked"}, "$inc": {"revision": 1}},
                session=tx,
            )
            await self.store._collection("auth_challenges").update_many(
                {
                    "org_id": org_id,
                    "membership_id": member["_id"],
                    "purpose": {"$in": ["invite", "verify"]},
                    "used_at": None,
                },
                {"$set": {"used_at": utc_now()}},
                session=tx,
            )
            request = command.policy.issue(user["_id"])
            parent, _, _, _ = validate_parent(snapshot, request, pending_link=True)
            require_parent_invite(parent, request)
            if request.team_id:
                self.engine.require(snapshot, "members:invite", "team", request.team_id)
                existing_link = snapshot["team_links"].get((request.team_id, user["_id"]))
                if existing_link and existing_link["status"] != "active":
                    raise PolicyDenied("an invitation cannot reinstate team authority")
                if (
                    member["status"] == "active"
                    and not existing_link
                    and not (
                        has(snapshot, "teams:manage", "organization", org_id)
                        or has(snapshot, "teams:manage", "team", request.team_id)
                    )
                ):
                    raise PolicyDenied(
                        "existing member placement needs independent team management"
                    )
            else:
                self.engine.require(snapshot, "members:invite", "member", user["_id"])
            await self.engine.validate_resources(org_id, request.tuples + request.ceiling, tx)
            await self.engine.validate_targets(org_id, request.targets.ids, tx)
            token = await self.local.challenge("invite", user, member, tx=tx)
            row = {
                "_id": "invitation_" + uuid4().hex,
                "org_id": org_id,
                "user_id": user["_id"],
                "membership_id": member["_id"],
                "membership_revision": member["revision"],
                "issuer_id": actor,
                "policy": command.policy.model_dump(),
                "token_hash": digest("challenge", token),
                "status": "pending",
                "revision": 1,
                "created_at": utc_now(),
                "expires_at": command.expires_at,
                "accepted_at": None,
                "assignment_id": None,
            }
            await self.store.insert("management_invitations", row, tx)
            await self.engine.audit(
                org_id,
                actor,
                "management_invitation_created",
                row["_id"],
                tx,
                change={
                    "superseded_invitation_ids": [r["_id"] for r in previous],
                    "parent_id": request.parent_id,
                    "parent_generation": request.expected_parent_generation,
                    "team_id": request.team_id,
                    "expires_at": row["expires_at"],
                },
            )
            delivery[:] = [
                Message(
                    local_subject(command.email),
                    "invite" if member["status"] == "invited" else "management_invite",
                    token,
                )
            ]
            return {
                "invitation_id": row["_id"],
                "status": "pending",
                "expires_at": row["expires_at"],
            }

        result = await self.mutate(org_id, actor, operation)
        # No network delivery implementation exists. A failed sink write leaves a
        # revocable pending proposal and never returns or logs its secret.
        await self.local.mail.deliver(delivery[0])
        return result

    async def finish(self, invitation: Row, member: Row, tx: Any, *, enrollment: bool) -> Row:
        if (
            invitation["status"] != "pending"
            or invitation["expires_at"] <= utc_now()
            or member["status"] != "active"
            or not member["verified_at"]
            or member["org_id"] != invitation["org_id"]
            or member["user_id"] != invitation["user_id"]
            or member["_id"] != invitation["membership_id"]
            or member["revision"] != invitation["membership_revision"] + (2 if enrollment else 0)
        ):
            raise PolicyDenied("invitation was not accepted")
        org_id, actor = invitation["org_id"], invitation["issuer_id"]
        snapshot = await self.engine.snapshot(org_id, actor, tx)
        await self.engine.fence(snapshot["anchor"], tx, mutation=True)
        request = ReviewedPolicy.model_validate(invitation["policy"]).issue(member["user_id"])
        parent, _, _, _ = validate_parent(snapshot, request, pending_link=True)
        require_parent_invite(parent, request)
        if request.team_id:
            self.engine.require(snapshot, "members:invite", "team", request.team_id)
            key = (request.team_id, member["user_id"])
            link = snapshot["team_links"].get(key)
            if not link:
                link = {
                    "_id": "team_member_" + uuid4().hex,
                    "org_id": org_id,
                    "team_id": request.team_id,
                    "user_id": member["user_id"],
                    "status": "active",
                    "revision": 1,
                    "created_at": utc_now(),
                }
                await self.store.insert("team_memberships", link, tx)
                snapshot["team_links"][key] = link
            elif link["status"] != "active":
                raise PolicyDenied("invitation cannot restore revoked team authority")
        else:
            self.engine.require(snapshot, "members:invite", "member", member["user_id"])
        issued = await self.issue_in_transaction(snapshot, tx, request)
        updated = await self.store.update(
            "management_invitations",
            {
                "_id": invitation["_id"],
                "org_id": org_id,
                "status": "pending",
                "revision": invitation["revision"],
                "expires_at": {"$gt": utc_now()},
            },
            {
                "$set": {
                    "status": "accepted",
                    "accepted_at": utc_now(),
                    "assignment_id": issued["_id"],
                },
                "$inc": {"revision": 1},
            },
            tx,
        )
        if not updated:
            raise PolicyConflict("invitation changed")
        await self.engine.audit(
            org_id,
            member["user_id"],
            "management_invitation_accepted",
            invitation["_id"],
            tx,
            change={"assignment_id": issued["_id"], "issuer_id": actor},
        )
        return {
            "org_id": org_id,
            "invitation_id": invitation["_id"],
            "status": "accepted",
            "reauthentication_required": True,
        }

    async def finish_enrollment(self, challenge: Row, member: Row, tx: Any) -> None:
        invitation = await self.store.find(
            "management_invitations",
            {
                "org_id": member["org_id"],
                "membership_id": member["_id"],
                "membership_revision": challenge["membership_revision"] - 1,
                "status": "pending",
            },
            tx,
        )
        if invitation:
            await self.finish(invitation, member, tx, enrollment=True)
        elif await self.store.find(
            "management_invitations",
            {"org_id": member["org_id"], "membership_id": member["_id"]},
            tx,
        ):
            raise PolicyDenied("invitation generation is unavailable")

    async def accept(self, row: Row, token: str) -> Row:
        try:
            return await self._accept(row, token)
        except PyMongoError:
            raise PolicyConflict("invitation changed; retry against current authority") from None

    async def _accept(self, row: Row, token: str) -> Row:
        from app.identity.crypto import valid_token

        if not valid_token(token):
            raise PolicyDenied("invitation was not accepted")
        self.local.sessions.recent(row)
        async with self.engine.transaction() as tx:
            await self.local.sessions.fence(row, tx)
            challenge = await self.local.consume(token, "invite", tx)
            invitation = await self.store.find(
                "management_invitations",
                {
                    "org_id": challenge["org_id"],
                    "token_hash": digest("challenge", token),
                    "user_id": row["user_id"],
                },
                tx,
            )
            member = await self.store.find(
                "organization_memberships",
                {"org_id": challenge["org_id"], "user_id": row["user_id"]},
                tx,
            )
            if not invitation or not member:
                raise PolicyDenied("invitation was not accepted")
            return await self.finish(invitation, member, tx, enrollment=False)

    async def revoke(self, org_id: str, actor: str, invitation_id: str, revision: int) -> Row:
        async def operation(snapshot: Row, tx: Any) -> Row:
            row = await self.store.find(
                "management_invitations",
                {"_id": invitation_id, "org_id": org_id, "revision": revision, "status": "pending"},
                tx,
            )
            if not row:
                raise PolicyConflict("invitation changed or is unavailable")
            team_id = row["policy"].get("team_id")
            self.engine.require(
                snapshot,
                "members:invite",
                "team" if team_id else "member",
                team_id or row["user_id"],
            )
            if actor != row["issuer_id"]:
                self.engine.require(snapshot, "grants:delegate", "member", row["user_id"])
            updated = await self.store.update(
                "management_invitations",
                {"_id": row["_id"], "org_id": org_id, "revision": revision, "status": "pending"},
                {"$set": {"status": "revoked"}, "$inc": {"revision": 1}},
                tx,
            )
            await self.engine.audit(org_id, actor, "management_invitation_revoked", row["_id"], tx)
            return {"invitation_id": row["_id"], "status": (updated or {})["status"]}

        return await self.mutate(org_id, actor, operation)

    async def list_invitations(
        self, org_id: str, actor: str, *, limit: int = 50, cursor: str | None = None
    ) -> Row:
        from app.management.directory import Directory, member_query

        if not 1 <= limit <= 100:
            raise PolicyDenied("bounded invitation query is required")
        snapshot = await self.engine.snapshot(org_id, actor)
        members = member_query(snapshot)
        query: Row = {"org_id": org_id}
        if "user_id" in members:
            teams = [
                team_id
                for team_id, team in snapshot["teams"].items()
                if team["status"] == "active" and has(snapshot, "members:read", "team", team_id)
            ]
            query["$or"] = [{"user_id": members["user_id"]}, {"policy.team_id": {"$in": teams}}]
        return await Directory(self.engine).page(
            "management_invitations",
            query,
            "_id",
            (
                "_id",
                "org_id",
                "user_id",
                "issuer_id",
                "policy",
                "status",
                "revision",
                "expires_at",
                "accepted_at",
                "assignment_id",
            ),
            snapshot,
            limit,
            cursor,
        )

    async def validate_enrollment(self, challenge: Row, tx: Any) -> None:
        invitation = await self.store.find(
            "management_invitations",
            {
                "org_id": challenge["org_id"],
                "membership_id": challenge["membership_id"],
                "token_hash": challenge["_id"],
            },
            tx,
        )
        if not invitation:
            if await self.store.find(
                "management_invitations",
                {"org_id": challenge["org_id"], "membership_id": challenge["membership_id"]},
                tx,
            ):
                raise PolicyDenied("invitation generation is unavailable")
            return  # separately approved operator enrollment conveys no effective grants
        if (
            invitation["status"] != "pending"
            or invitation["expires_at"] <= utc_now()
            or invitation["user_id"] != challenge["user_id"]
            or invitation["membership_revision"] != challenge["membership_revision"]
        ):
            raise PolicyDenied("invitation was not accepted")
        snapshot = await self.engine.snapshot(invitation["org_id"], invitation["issuer_id"], tx)
        await self.engine.fence(snapshot["anchor"], tx)
        request = ReviewedPolicy.model_validate(invitation["policy"]).issue(invitation["user_id"])
        parent, _, _, _ = validate_parent(snapshot, request, pending_link=True)
        require_parent_invite(parent, request)
        self.engine.require(
            snapshot,
            "members:invite",
            "team" if request.team_id else "member",
            request.team_id or request.subject_id,
        )
        await self.engine.validate_resources(
            invitation["org_id"], request.tuples + request.ceiling, tx
        )


def require_parent_invite(parent: Row, request: Issue) -> None:
    kind, identifier = (
        ("team", request.team_id) if request.team_id else ("member", request.subject_id)
    )
    if not any(
        g.action == "members:invite"
        and g.resource_kind == kind
        and g.selection().contains(identifier)
        for g in grants(parent)
    ):
        raise PolicyDenied("the reviewed parent must also hold current invitation authority")
