"""Exact policy proposals and immutable independently authorized decisions."""

from datetime import timedelta
from typing import Any
from uuid import uuid4

from app.identity.repository import Row
from app.management.boundaries import validate_parent
from app.management.models import Decision, RequestCreate, ReviewedPolicy
from app.models.common import utc_now
from app.security.policy.catalog import can_appoint
from app.security.policy.evaluation import PolicyConflict, PolicyDenied
from app.security.policy.governance import Governance


class AccessRequests(Governance):
    async def create(self, org_id: str, actor: str, command: RequestCreate) -> Row:
        if not utc_now() < command.expires_at <= utc_now() + timedelta(days=7):
            raise PolicyDenied("request requires an expiry within seven days")

        async def operation(snapshot: Row, tx: Any) -> Row:
            if command.expected_policy_revision != snapshot["anchor"]["revision"]:
                raise PolicyConflict("policy changed; review and resubmit the exact request")
            request = command.policy.issue(actor)
            parent = snapshot["assignments"].get(request.parent_id)
            if not parent or parent["subject_id"] == actor:
                raise PolicyDenied("an independent current issuer is required")
            issuer = await self.engine.snapshot(org_id, parent["subject_id"], tx)
            validate_parent(issuer, request)
            if not can_appoint(issuer["member"]["role"], snapshot["member"]["role"]):
                raise PolicyDenied("current requesting role is outside the issuer's authority")
            await self.engine.validate_resources(org_id, request.tuples + request.ceiling, tx)
            await self.engine.validate_targets(org_id, request.targets.ids, tx)
            row = {
                "_id": "request_" + uuid4().hex,
                "org_id": org_id,
                "user_id": actor,
                "membership_revision": snapshot["member"]["revision"],
                "policy": command.policy.model_dump(),
                "policy_revision": snapshot["anchor"]["revision"] + 1,
                "status": "pending",
                "revision": 1,
                "created_at": utc_now(),
                "expires_at": command.expires_at,
            }
            await self.store.insert("access_requests", row, tx)
            await self.engine.audit(
                org_id,
                actor,
                "access_requested",
                row["_id"],
                tx,
                change={
                    "policy": row["policy"],
                    "policy_revision": row["policy_revision"],
                    "expires_at": row["expires_at"],
                },
            )
            return row

        return await self.mutate(org_id, actor, operation)

    async def decide(self, org_id: str, actor: str, request_id: str, command: Decision) -> Row:
        async def operation(snapshot: Row, tx: Any) -> Row:
            row = await self.store.find(
                "access_requests",
                {
                    "_id": request_id,
                    "org_id": org_id,
                    "status": "pending",
                    "revision": command.expected_revision,
                },
                tx,
            )
            if not row or row["user_id"] == actor:
                raise PolicyDenied("an independent exact request decision is required")
            if (
                row["expires_at"] <= utc_now()
                or row["policy_revision"] != command.expected_policy_revision
                or snapshot["anchor"]["revision"] != command.expected_policy_revision
            ):
                raise PolicyConflict("request or policy changed; review and resubmit")
            self.engine.require(snapshot, "grants:delegate", "member", row["user_id"])
            member = await self.store.find(
                "organization_memberships",
                {
                    "org_id": org_id,
                    "user_id": row["user_id"],
                    "status": "active",
                    "verified_at": {"$ne": None},
                    "revision": row["membership_revision"],
                },
                tx,
            )
            if not member:
                raise PolicyDenied("requesting membership is unavailable")
            request = ReviewedPolicy.model_validate(row["policy"]).issue(row["user_id"])
            validate_parent(snapshot, request)
            await self.engine.validate_resources(org_id, request.tuples + request.ceiling, tx)
            await self.engine.validate_targets(org_id, request.targets.ids, tx)
            issued = (
                await self.issue_in_transaction(snapshot, tx, request)
                if command.outcome == "approved"
                else None
            )
            decision = {
                "_id": "decision_" + uuid4().hex,
                "org_id": org_id,
                "request_id": request_id,
                "request_revision": command.expected_revision,
                "policy_revision": command.expected_policy_revision,
                "actor_id": actor,
                "outcome": command.outcome,
                "assignment_id": issued["_id"] if issued else None,
                "created_at": utc_now(),
            }
            await self.store.insert("access_decisions", decision, tx)
            if not await self.store.update(
                "access_requests",
                {
                    "_id": request_id,
                    "org_id": org_id,
                    "revision": command.expected_revision,
                    "status": "pending",
                },
                {"$set": {"status": command.outcome}, "$inc": {"revision": 1}},
                tx,
            ):
                raise PolicyConflict("request changed")
            await self.engine.audit(
                org_id,
                actor,
                "access_request_decided",
                request_id,
                tx,
                change={"decision": decision},
            )
            return decision

        return await self.mutate(org_id, actor, operation)

    async def cancel(self, org_id: str, actor: str, request_id: str, revision: int) -> Row:
        async def operation(snapshot: Row, tx: Any) -> Row:
            row = await self.store.update(
                "access_requests",
                {
                    "_id": request_id,
                    "org_id": org_id,
                    "user_id": actor,
                    "revision": revision,
                    "status": "pending",
                },
                {"$set": {"status": "cancelled"}, "$inc": {"revision": 1}},
                tx,
            )
            if not row:
                raise PolicyConflict("request changed or is unavailable")
            await self.engine.audit(org_id, actor, "access_request_cancelled", request_id, tx)
            return row

        return await self.mutate(org_id, actor, operation)

    def visibility(self, snapshot: Row) -> Row:
        actor, org_id = snapshot["member"]["user_id"], snapshot["anchor"]["org_id"]
        clauses: list[Row] = [{"user_id": actor}]
        for grant in snapshot["tuples"]:
            if grant.action != "grants:read" or grant.selector == "none":
                continue
            if grant.resource_kind == "member" and snapshot["member"]["role"] != "team_lead":
                if grant.selector == "all_in_org":
                    return {"org_id": org_id}
                clauses.append({"user_id": {"$in": grant.ids}})
            if grant.resource_kind == "team":
                for team_id, team in snapshot["teams"].items():
                    if team["status"] == "active" and grant.selection().contains(team_id):
                        users = [
                            u
                            for (t, u), link in snapshot["team_links"].items()
                            if t == team_id and link["status"] == "active"
                        ]
                        clauses.append({"policy.team_id": team_id, "user_id": {"$in": users}})
        return {"org_id": org_id, "$or": clauses}

    async def list_requests(
        self, org_id: str, actor: str, *, limit: int = 50, cursor: str | None = None
    ) -> Row:
        from app.management.directory import Directory

        if not 1 <= limit <= 100:
            raise PolicyDenied("bounded request query is required")
        snapshot = await self.engine.snapshot(org_id, actor)
        result = await Directory(self.engine).page(
            "access_requests",
            self.visibility(snapshot),
            "_id",
            (
                "_id",
                "org_id",
                "user_id",
                "policy",
                "policy_revision",
                "status",
                "revision",
                "expires_at",
            ),
            snapshot,
            limit,
            cursor,
        )
        for row in result["items"]:
            row["expired"] = row["expires_at"] <= utc_now()
        return result
