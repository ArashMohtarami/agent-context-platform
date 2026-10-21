"""Live organization policy checks shared by sessions, OIDC and explicit switch."""

from typing import Any

from pymongo.asynchronous.client_session import AsyncClientSession

from app.identity.crypto import AuthRejected
from app.identity.oidc import provider_mfa
from app.identity.providers import Registration, Registry
from app.identity.repository import IdentityStore
from app.models.common import utc_now

type Row = dict[str, Any]


async def _identity(
    store: IdentityStore,
    row: Row,
    provider: str,
    tx: AsyncClientSession | None,
    joined: Row | None,
) -> Row | None:
    query = {
        "_id": row["authentication"]["identity_id"],
        "user_id": row["user_id"],
        "provider": provider,
        "verified_at": {"$ne": None},
    }
    if joined is None:
        return await store.find("user_identities", query, tx)
    if not joined.get("verified_at") or any(
        joined.get(key) != query[key] for key in ("_id", "user_id", "provider")
    ):
        raise AuthRejected("authentication was not accepted")
    return joined


async def approved_provider(
    store: IdentityStore, registry: Registry, org: Row, tx: AsyncClientSession | None = None
) -> Registration:
    config = await store.find("organization_oidc", {"org_id": org["org_id"]}, tx)
    if not config:
        raise AuthRejected("authentication was not accepted")
    registration = registry.get(config["registration_id"])
    if (
        registration.org_id != org["org_id"]
        or registration.version != config["registration_version"]
    ):
        raise AuthRejected("authentication was not accepted")
    return registration


async def check_policy(
    store: IdentityStore,
    registry: Registry | None,
    row: Row,
    org: Row,
    tx: AsyncClientSession | None = None,
    *,
    identity: Row | None = None,
) -> None:
    evidence = row.get("authentication")
    method = evidence["method"] if evidence else "local"
    if method == "local":
        if evidence and not await _identity(store, row, "local", tx, identity):
            raise AuthRejected("authentication was not accepted")
        if org["auth_policy"] in {"local_only", "sso_preferred"}:
            return
        if not row.get("mfa_at"):
            raise AuthRejected("target organization authentication is required")
        exception = (
            await store.find(
                "organization_break_glass",
                {
                    "org_id": org["org_id"],
                    "user_id": row["user_id"],
                    "membership_revision": row["membership_revision"],
                    "policy_version": org["policy_version"],
                    "expires_at": {"$gt": utc_now()},
                },
                tx,
            )
            if registry
            else None
        )
        if exception:
            return
        raise AuthRejected("target organization authentication is required")
    if not registry or not evidence or org["auth_policy"] == "local_only":
        raise AuthRejected("target organization authentication is required")
    registration = await approved_provider(store, registry, org, tx)
    if not (
        evidence["registration_id"] == registration.registration_id
        and evidence["registration_version"] == registration.version
        and evidence["issuer"] == registration.issuer
        and evidence["client_id"] == registration.client_id
        and evidence["assurance_version"] == registration.assurance_version
        and evidence["valid_until"] > utc_now()
    ):
        raise AuthRejected("target organization authentication is required")
    identity = await _identity(store, row, registration.issuer, tx, identity)
    if not identity or (
        registration.require_mfa
        and not provider_mfa(
            registration, {"acr": evidence["acr"], "amr": evidence["verified_amr"]}
        )
    ):
        raise AuthRejected("target organization authentication is required")
