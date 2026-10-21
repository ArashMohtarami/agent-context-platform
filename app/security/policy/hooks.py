"""Shared driver-boundary hooks cover REST, MCP, admin and direct services."""

import asyncio
from collections.abc import Awaitable, Callable
from functools import wraps
from typing import Any
from uuid import uuid4

from pymongo.asynchronous.collection import AsyncCollection
from pymongo.errors import PyMongoError

from app.models.common import utc_now
from app.security.policy.evaluation import PolicyDenied
from app.security.policy.reviews import require_review
from app.security.tenant_policy import current_policy

RESEARCH_REVIEW_STATES = {
    "knowledge_candidates": {"approved", "rejected", "needs_changes"},
    "skill_update_proposals": {"approved", "rejected"},
    "research_claims": {"supported", "partially_supported", "contested", "unsupported", "outdated"},
    "research_notes": {"reviewed"},
    "research_briefs": {"ready"},
}
AUTHORED_COLLECTIONS = frozenset(
    {"content_items", "content_ideas", "content_versions", *RESEARCH_REVIEW_STATES}
)
AUTHORITY_COLLECTIONS = frozenset({"api_keys", "agent_clients", "client_profiles"})
# Only internal evidence persistence can replay a confirmed-aborted transaction.
# These collections contain no review/issuance decision or outbound side effect.
INGESTION_STORAGE = frozenset(
    {"source_snapshots", "research_documents", "research_chunks", "ingestion_jobs"}
)


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

                # An old worker must never silently adopt the newer policy semantics.
                if control.get("worker_schema_versions") != [40]:
                    raise PolicyDenied("explicit schema-41 operator policy context is required")
                engine = PolicyEngine(IdentityStore(self.raw.database))
        if engine is None:
            return await method(self, *args, **kwargs)
        if policy is not None and policy.managed and self.collection in AUTHORITY_COLLECTIONS:
            raise PolicyDenied(
                "managed authority changes require the verified machine governance API"
            )
        if self.collection == "api_keys" and method.__name__ in {"insert_one", "insert_many"}:
            raise PolicyDenied("managed credential issuance requires verified Phase 7 sponsorship")
        session = kwargs.get("session")
        attempts = 5 if self.collection in INGESTION_STORAGE and session is None else 1
        for attempt in range(attempts):
            boundary = (
                engine.domain_fence(policy, session)
                if policy is not None and policy.engine and not policy.operator
                else engine.operator_fence(session)
            )
            try:
                async with boundary as tx:
                    # Never retain an aborted session in kwargs between attempts.
                    result = await method(self, *args, **{**kwargs, "session": tx})
                    # Authority changes keep their transactional audit contract.
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
            except PyMongoError as error:
                if (
                    attempt + 1 == attempts
                    or not error.has_error_label("TransientTransactionError")
                    or error.has_error_label("UnknownTransactionCommitResult")
                ):
                    raise
                # Reenter the fence: current session, membership, sponsor and
                # policy are checked again. A changed revision is never replayed.
                # The other bounded ingestion may be persisting several
                # chunks. Up to 1.5 seconds total backoff avoids immediate
                # lockstep retries against its session/anchor transaction.
                await asyncio.sleep(0.1 * 2**attempt)

    return operation


def is_decision(collection: str, changes: dict[str, Any]) -> str | None:
    state = changes.get("status")
    if collection in {"content_items", "content_ideas"} and state == "approved":
        return "content:write"
    if collection in RESEARCH_REVIEW_STATES and state in RESEARCH_REVIEW_STATES[collection]:
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
    if collection == "content_items" and any(
        field in changes and changes[field] != existing.get(field)
        for field in (
            "title",
            "body",
            "project_ids",
            "source_refs",
            "pillar_id",
            "topic_tags",
            "tags",
        )
    ):
        raise PolicyDenied("editing cannot also approve content")
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
    if (
        not authors
        or not authors["sponsor_ids"]
        or set(policy.sponsor_ids or (policy.subject_id,)) & set(authors["sponsor_ids"])
    ):
        raise PolicyDenied("independent verified authorship is required")
    return True


async def record_authorship(
    database: Any, collection: str, document: dict[str, Any], session: Any, *, new: bool = False
) -> None:
    policy = current_policy()
    if policy is None or not policy.managed or collection not in AUTHORED_COLLECTIONS:
        return
    if policy.subject_kind == "machine":
        if not policy.sponsor_ids or not policy.machine_key_id:
            raise PolicyDenied("managed machine sponsorship is not verified")
        sponsor_ids = list(policy.sponsor_ids)
    elif policy.subject_kind == "human" and policy.client_id is None:
        sponsor_ids = [policy.subject_id]
    else:
        raise PolicyDenied("managed machine sponsorship is not verified")
    query = {"org_id": policy.org_id, "collection": collection, "document_id": str(document["_id"])}
    row = await database["review_authorship"].find_one(query, session=session)
    if row:
        if not set(sponsor_ids) <= set(row["sponsor_ids"]):
            if len(row["sponsor_ids"]) >= 100:
                raise PolicyDenied("authorship history exceeds its bounded capacity")
            await database["review_authorship"].update_one(
                {"_id": row["_id"]},
                {"$addToSet": {"sponsor_ids": {"$each": sponsor_ids}}},
                session=session,
            )
    elif new:
        await database["review_authorship"].insert_one(
            {
                "_id": uuid4().hex,
                **query,
                "sponsor_ids": sponsor_ids,
                "created_at": utc_now(),
            },
            session=session,
        )
