"""Shared driver-boundary hooks cover REST, MCP, admin and direct services."""

from collections.abc import Awaitable, Callable
from functools import wraps
from typing import Any
from uuid import uuid4

from pymongo.asynchronous.collection import AsyncCollection

from app.models.common import utc_now
from app.security.policy.evaluation import PolicyDenied
from app.security.policy.reviews import require_review
from app.security.tenant_policy import current_policy

AUTHORED_COLLECTIONS = frozenset({"content_items", "content_versions", "knowledge_candidates"})
AUTHORITY_COLLECTIONS = frozenset({"api_keys", "agent_clients", "client_profiles"})


def fenced(method: Callable[..., Awaitable[Any]]) -> Callable[..., Awaitable[Any]]:
    @wraps(method)
    async def operation(self: Any, *args: Any, **kwargs: Any) -> Any:
        policy = current_policy()
        if policy is not None and policy.managed and policy.engine is None:
            raise PolicyDenied("managed writes require a policy fence")
        if (
            policy is not None
            and policy.operator
            and policy.subject_id == "credential-resolution"
            and self.collection == "agent_clients"
            and method.__name__ == "update_one"
            and len(args) == 2
            and set(args[1]) == {"$set"}
            and set(args[1]["$set"]) == {"last_seen_at"}
        ):
            return await method(self, *args, **kwargs)
        engine = policy.engine if policy is not None else None
        # Explicit host operator paths have no bearer policy. They still touch
        # the same anchor on a managed installation, including old admin APIs.
        if engine is None and isinstance(self.raw, AsyncCollection):
            control = await self.raw.database["_tenant_control"].find_one({"_id": "installation"})
            if control and control.get("stage") == "managed":
                from app.identity.repository import IdentityStore
                from app.security.policy.engine import PolicyEngine

                engine = PolicyEngine(IdentityStore(self.raw.database))
        if engine is None:
            return await method(self, *args, **kwargs)
        if self.collection == "api_keys" and method.__name__ in {"insert_one", "insert_many"}:
            raise PolicyDenied("managed credential issuance requires verified Phase 7 sponsorship")
        boundary = (
            engine.domain_fence(policy, kwargs.get("session"))
            if policy is not None and policy.engine
            else engine.operator_fence(kwargs.get("session"))
        )
        async with boundary as tx:
            kwargs["session"] = tx
            result = await method(self, *args, **kwargs)
            # Credential/client envelopes change authority. Their governance
            # event cannot use ordinary best-effort domain audit.
            if self.collection in AUTHORITY_COLLECTIONS:
                from app.security.tenant import require_tenant

                await engine.audit(
                    require_tenant().org_id,
                    policy.subject_id if policy else "installation_operator",
                    method.__name__ + ":" + self.collection,
                    self.collection,
                    tx,
                )
            return result

    return operation


def is_decision(collection: str, changes: dict[str, Any]) -> str | None:
    state = changes.get("status")
    if collection == "content_items" and state == "approved":
        return "content:write"
    if collection == "knowledge_candidates" and state in {"approved", "rejected", "needs_changes"}:
        return "research:approve"
    return None


async def review_gate(
    database: Any, collection: str, existing: dict[str, Any], changes: dict[str, Any], session: Any
) -> bool:
    policy = current_policy()
    operation = is_decision(collection, changes)
    if policy is None or operation is None:
        return False
    if not policy.managed:
        require_review(existing, collection, operation)
        return False
    require_review(existing, collection, operation)
    # Immutable history of author sponsors, recorded by the server in the same
    # fenced transaction as each authored mutation. Last-writer actor is not
    # sufficient proof of independent authorship.
    authors = await database["review_authorship"].find_one(
        {
            "org_id": policy.org_id,
            "collection": collection,
            "document_id": str(existing["_id"]),
        },
        session=session,
    )
    if not authors or not authors["sponsor_ids"] or policy.subject_id in authors["sponsor_ids"]:
        raise PolicyDenied("independent verified authorship is required")
    return True


async def record_authorship(
    database: Any, collection: str, document: dict[str, Any], session: Any, *, new: bool = False
) -> None:
    policy = current_policy()
    if policy is None or not policy.managed or collection not in AUTHORED_COLLECTIONS:
        return
    if policy.subject_kind != "human" or policy.client_id is not None:
        raise PolicyDenied("managed machine sponsorship is not verified")
    query = {"org_id": policy.org_id, "collection": collection, "document_id": str(document["_id"])}
    row = await database["review_authorship"].find_one(query, session=session)
    if row:
        if policy.subject_id not in row["sponsor_ids"]:
            if len(row["sponsor_ids"]) >= 100:
                raise PolicyDenied("authorship history exceeds its bounded capacity")
            await database["review_authorship"].update_one(
                {"_id": row["_id"]},
                {"$addToSet": {"sponsor_ids": policy.subject_id}},
                session=session,
            )
    elif new:
        await database["review_authorship"].insert_one(
            {
                "_id": uuid4().hex,
                **query,
                "sponsor_ids": [policy.subject_id],
                "created_at": utc_now(),
            },
            session=session,
        )
