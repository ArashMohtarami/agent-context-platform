"""Fenced machine policy, recipient-only issuance and current sponsor resolution."""

from dataclasses import replace
from datetime import timedelta
from hashlib import sha256
from typing import Any, cast
from uuid import uuid4

from app.identity.repository import Row
from app.managed_agents.models import (
    ClientApproval,
    CredentialIssue,
    ReviewerApproval,
    ServiceCreate,
    TemplatePut,
)
from app.managed_agents.policy import intersect, projection, restrict
from app.management.boundaries import has
from app.models.agent import AgentClientDocument
from app.models.api_key import ApiKeyDocument
from app.models.client_profile import ClientProfileDocument, ClientProfileStatus
from app.models.common import utc_now
from app.security.keys import mint
from app.security.policy.engine import PolicyEngine
from app.security.policy.evaluation import PolicyConflict, PolicyDenied, active_chain, grants
from app.security.policy.governance import Governance
from app.security.policy.models import Grant, Selector, covered
from app.security.principal import Principal
from app.security.scopes import ALL_SCOPES
from app.security.tenant import TenantContext, tenant_scope
from app.security.tenant_policy import ResourceGrant, TenantPolicy


def chain(snapshot: Row, identifier: str, generation: int, subject: str) -> Row:
    row = snapshot["assignments"].get(identifier)
    if not row or row["generation"] != generation or row["subject_id"] != subject:
        raise PolicyDenied("reviewed parent authority changed")
    active_chain(
        row,
        snapshot["assignments"],
        snapshot["members"],
        snapshot["users"],
        utc_now(),
        snapshot.get("teams"),
        snapshot.get("team_links"),
    )
    return cast(Row, row)


def team(
    snapshot: Row,
    team_id: str | None,
    sponsor: str,
    generation: int | None = None,
    link_revision: int | None = None,
) -> tuple[Row | None, Row | None]:
    if team_id is None:
        return None, None
    row = snapshot["teams"].get(team_id)
    link = snapshot["team_links"].get((team_id, sponsor))
    if (
        not row
        or not link
        or row["status"] != "active"
        or link["status"] != "active"
        or (generation is not None and row["generation"] != generation)
        or (link_revision is not None and link["revision"] != link_revision)
    ):
        raise PolicyDenied("current verified team sponsorship is required")
    return row, link


def authority(snapshot: Row, operation: str, row: Row) -> None:
    org_id = snapshot["anchor"]["org_id"]
    actor = snapshot["member"]["user_id"]
    if has(snapshot, operation + ":org", "organization", org_id) or has(
        snapshot, operation + ":org", "client", row["client_id"]
    ):
        return
    if actor == row["sponsor_id"] and (
        has(snapshot, operation + ":own", "member", actor)
        or has(snapshot, operation + ":own", "client", row["client_id"])
    ):
        return
    if row.get("team_id") and has(snapshot, operation + ":team", "team", row["team_id"]):
        team(
            snapshot,
            row["team_id"],
            row["sponsor_id"],
            row.get("team_generation"),
            row.get("team_link_revision"),
        )
        return
    raise PolicyDenied("client authority is outside the current delegated boundary")


def receipt(row: Row) -> Row:
    return {
        k: row[k]
        for k in (
            "org_id",
            "client_id",
            "key_id",
            "recipient_id",
            "issuance_id",
            "status",
            "revision",
            "expires_at",
            "replaces_key_id",
        )
    }


class ManagedAgents:
    def __init__(self, engine: PolicyEngine) -> None:
        self.engine, self.store = engine, engine.store
        self.db: Any = engine.store.database

    async def enabled(self) -> None:
        if not self.engine.managed_agents:
            raise PolicyDenied("managed agents are disabled")
        await self.engine.enabled()

    async def client(self, org_id: str, client_id: str, tx: Any = None) -> Row:
        row = await self.store.find(
            "managed_clients", {"org_id": org_id, "client_id": client_id}, tx
        )
        if not row:
            raise PolicyDenied("verified client is unavailable")
        return row

    async def service(self, org_id: str, identifier: str, tx: Any = None) -> Row:
        row = await self.store.find(
            "service_accounts", {"org_id": org_id, "service_account_id": identifier}, tx
        )
        if not row or row["status"] != "active" or row["expires_at"] <= utc_now():
            raise PolicyDenied("service custody is unavailable")
        snapshot = await self.engine.snapshot(org_id, row["custodian_id"], tx)
        if snapshot["member"]["revision"] != row["custodian_revision"]:
            raise PolicyDenied("service custody changed")
        team(
            snapshot,
            row["team_id"],
            row["custodian_id"],
            row["team_generation"],
            row["team_link_revision"],
        )
        return row

    async def create_service(self, org_id: str, actor: str, command: ServiceCreate) -> Row:
        await self.enabled()

        async def operation(snapshot: Row, tx: Any) -> Row:
            self.engine.require(snapshot, "clients:manage:org", "organization", org_id)
            if (
                actor == command.custodian_id
                or not utc_now() < command.expires_at <= utc_now() + timedelta(days=365)
            ):
                raise PolicyDenied("independent bounded custody approval is required")
            custodian = await self.engine.snapshot(org_id, command.custodian_id, tx)
            team_row, team_link = team(
                custodian, command.team_id, command.custodian_id, command.expected_team_generation
            )
            if not team_row or not team_link:
                raise PolicyDenied("verified custody team is required")
            if await self.store.find(
                "service_accounts",
                {"org_id": org_id, "service_account_id": command.service_account_id},
                tx,
            ):
                raise PolicyConflict("service account already exists")
            row = {
                "_id": "service_" + uuid4().hex,
                "org_id": org_id,
                "service_account_id": command.service_account_id,
                "name": command.name,
                "custodian_id": command.custodian_id,
                "custodian_revision": custodian["member"]["revision"],
                "team_id": team_row["team_id"],
                "team_generation": team_row["generation"],
                "team_link_revision": team_link["revision"],
                "status": "active",
                "revision": 1,
                "created_at": utc_now(),
                "expires_at": command.expires_at,
                "created_by": actor,
                "reviewer": None,
            }
            await self.store.insert("service_accounts", row, tx)
            await self.engine.audit(
                org_id,
                actor,
                "service_custody_approved",
                row["service_account_id"],
                tx,
                change={"after": row},
            )
            return row

        return await Governance(self.engine).mutate(org_id, actor, operation)

    async def approve(
        self, org_id: str, actor: str, command: ClientApproval, *, preview: bool = False
    ) -> Row:
        await self.enabled()

        async def operation(snapshot: Row, tx: Any) -> Row:
            service = (
                await self.service(org_id, command.subject_id, tx)
                if command.subject_kind == "service_account"
                else None
            )
            sponsor_id = service["custodian_id"] if service else command.subject_id
            sponsor = await self.engine.snapshot(org_id, sponsor_id, tx)
            if actor == sponsor_id:
                raise PolicyDenied("independent client policy approval is required")
            parent = chain(snapshot, command.parent_id, command.expected_parent_generation, actor)
            team_row, team_link = team(
                sponsor, command.team_id, sponsor_id, command.expected_team_generation
            )
            if service and command.team_id != service["team_id"]:
                raise PolicyDenied("client must remain in its service custody team")
            proposed = {
                "client_id": command.client_id,
                "sponsor_id": sponsor_id,
                "team_id": command.team_id,
                "team_generation": team_row["generation"] if team_row else None,
                "team_link_revision": team_link["revision"] if team_link else None,
            }
            authority(snapshot, "clients:manage", proposed)
            if snapshot["member"]["role"] == "team_lead" and (
                not team_row or parent.get("team_id") != command.team_id
            ):
                raise PolicyDenied("lead must use its named team parent")
            direct = Selector.model_validate(parent["targets"]).contains(sponsor_id) and any(
                g.action == "grants:delegate"
                and g.resource_kind == "member"
                and g.selection().contains(sponsor_id)
                for g in grants(parent)
            )
            delegated = bool(
                team_row
                and any(
                    g.action == "grants:delegate"
                    and g.resource_kind == "team"
                    and g.selection().contains(team_row["team_id"])
                    for g in grants(parent)
                )
            )
            if (
                parent["remaining_depth"] < 1
                or not (direct or delegated)
                or not utc_now() < command.expires_at <= parent["expires_at"]
                or command.expires_at > utc_now() + timedelta(days=90)
                or (service and command.expires_at > service["expires_at"])
            ):
                raise PolicyDenied("bounded current delegation is required")
            if any(not covered(g, grants(parent, "ceiling")) for g in command.tuples):
                raise PolicyDenied("client policy exceeds its reviewed parent ceiling")
            await self.engine.validate_resources(org_id, command.tuples, tx)
            scopes = sorted(
                {
                    g.action
                    for g in command.tuples
                    if g.action in ALL_SCOPES and g.selector != "none"
                }
            )
            template = None
            if command.profile_id:
                template = await self.db["client_profiles"].find_one(
                    {
                        "org_id": org_id,
                        "profile_id": command.profile_id,
                        "revision": command.expected_profile_revision,
                        "status": "active",
                    },
                    session=tx,
                )
                if not template or not set(scopes) <= set(template["scopes"]):
                    raise PolicyDenied("assignment must narrow the current active template")
            raw = await self.db["agent_clients"].find_one(
                {"org_id": org_id, "client_id": command.client_id}, session=tx
            )
            old = await self.store.find(
                "managed_clients", {"org_id": org_id, "client_id": command.client_id}, tx
            )
            if (
                old["revision"] if old else raw["revision"] if raw else 0
            ) != command.expected_revision or (
                old
                and (
                    old["subject_id"] != command.subject_id
                    or old["subject_kind"] != command.subject_kind
                    or old["team_id"] != command.team_id
                )
            ):
                raise PolicyConflict("client revision or immutable sponsorship changed")
            row = {
                "_id": old["_id"] if old else "sponsorship_" + uuid4().hex,
                "org_id": org_id,
                **proposed,
                "subject_kind": command.subject_kind,
                "subject_id": command.subject_id,
                "sponsor_revision": sponsor["member"]["revision"],
                "service_revision": service["revision"] if service else None,
                "parent_id": parent["_id"],
                "parent_generation": parent["generation"],
                "issuer_id": actor,
                "tuples": [g.model_dump() for g in command.tuples],
                "scopes": scopes,
                "profile_id": command.profile_id,
                "profile_revision": command.expected_profile_revision,
                "status": "active",
                "revision": (old["revision"] + 1 if old else 1),
                "created_at": old["created_at"] if old else utc_now(),
                "expires_at": command.expires_at,
            }
            # Policy updates invalidate all previous credential receipts. Reissue explicitly.
            projects = projection(command.tuples, "project", "project_directory")
            if preview:
                preview_tuples = intersect(command.tuples, sponsor["tuples"])
                if service:
                    preview_tuples += tuple(
                        g
                        for g in intersect(
                            command.tuples, await self._review_tuples(org_id, service, tx)
                        )
                        if g.action == "reviews:decide"
                    )
                return {
                    "client_id": command.client_id,
                    "effective_tuples": [g.model_dump() for g in preview_tuples],
                    "copied_scopes": scopes,
                    "profile_revision": command.expected_profile_revision,
                    "rate_limit_profile_enforced": False,
                    "policy_revision": snapshot["anchor"]["revision"],
                }
            if old:
                await self.store.update(
                    "managed_clients",
                    {"_id": old["_id"], "revision": old["revision"]},
                    {"$set": {k: v for k, v in row.items() if k != "_id"}},
                    tx,
                )
            else:
                await self.store.insert("managed_clients", row, tx)
            data = {
                "scopes": scopes,
                "project_mode": projects.mode,
                "project_ids": list(projects.ids),
                "profile_id": command.profile_id,
                "sponsorship_id": row["_id"],
                "name": command.name,
                "status": "active",
                "updated_at": utc_now(),
            }
            if raw:
                await self.db["agent_clients"].update_one(
                    {"_id": raw["_id"], "org_id": org_id, "revision": raw["revision"]},
                    {"$set": data, "$inc": {"revision": 1}},
                    session=tx,
                )
            else:
                with tenant_scope(TenantContext(org_id)):
                    doc = AgentClientDocument(
                        client_id=command.client_id,
                        client_type=command.client_type,
                        owner_id="verified-sponsorship",
                        created_at=data["updated_at"],
                        **data,
                    )
                await self.db["agent_clients"].insert_one(doc.model_dump(by_alias=True), session=tx)
            await self.engine.audit(
                org_id,
                actor,
                "managed_client_policy_approved",
                command.client_id,
                tx,
                change={"after": row},
            )
            return row

        if preview:
            async with self.engine.transaction() as tx:
                return await operation(await self.engine.snapshot(org_id, actor, tx), tx)
        return await Governance(self.engine).mutate(org_id, actor, operation)

    async def current(
        self, org_id: str, client_id: str, tx: Any = None
    ) -> tuple[Row, Row, tuple[Grant, ...]]:
        await self.enabled()
        row = await self.client(org_id, client_id, tx)
        if row["status"] != "active" or row["expires_at"] <= utc_now():
            raise PolicyDenied("client sponsorship expired or revoked")
        sponsor = await self.engine.snapshot(org_id, row["sponsor_id"], tx)
        if sponsor["member"]["revision"] != row["sponsor_revision"]:
            raise PolicyDenied("sponsor membership changed")
        team(
            sponsor,
            row["team_id"],
            row["sponsor_id"],
            row["team_generation"],
            row["team_link_revision"],
        )
        issuer = await self.engine.snapshot(org_id, row["issuer_id"], tx)
        parent = chain(issuer, row["parent_id"], row["parent_generation"], row["issuer_id"])
        if row["subject_kind"] == "service_account":
            service = await self.service(org_id, row["subject_id"], tx)
            if service["revision"] != row["service_revision"]:
                raise PolicyDenied("service custody revision changed")
        client = await self.db["agent_clients"].find_one(
            {
                "org_id": org_id,
                "client_id": client_id,
                "status": "active",
                "sponsorship_id": row["_id"],
            },
            session=tx,
        )
        if not client:
            raise PolicyDenied("client is unavailable")
        effective = intersect(grants(row), sponsor["tuples"], grants(parent, "ceiling"))
        if row["subject_kind"] == "service_account":
            effective += tuple(
                g
                for g in intersect(
                    grants(row),
                    grants(parent, "ceiling"),
                    await self._review_tuples(org_id, service, tx),
                )
                if g.action == "reviews:decide"
            )
        # The V4 client snapshot and V5 assigned scope snapshot remain upper bounds.
        effective = tuple(
            g for g in effective if g.action in client["scopes"] or g.action == "reviews:decide"
        )
        project_ids = (
            None
            if client.get("project_mode") == "all_in_org"
            else tuple(client.get("project_ids", []))
        )
        effective = restrict(effective, project=project_ids, project_directory=project_ids)
        return row, sponsor, effective

    async def reviewer_approve(
        self, org_id: str, actor: str, identifier: str, command: ReviewerApproval
    ) -> Row:
        async def operation(snapshot: Row, tx: Any) -> Row:
            row = await self.service(org_id, identifier, tx)
            if row["revision"] != command.expected_revision or actor in {
                row["custodian_id"],
                row["created_by"],
            }:
                raise PolicyDenied("independent reviewer approval is required")
            parent = chain(snapshot, command.parent_id, command.expected_parent_generation, actor)
            if not utc_now() < command.expires_at <= min(parent["expires_at"], row["expires_at"]):
                raise PolicyDenied("reviewer approval must be bounded")
            if not any(
                g.action == "reviews:decide" and g.selector != "none" for g in command.tuples
            ) or any(
                g.action
                not in {
                    "reviews:decide",
                    "content:read",
                    "content:write",
                    "research:read",
                    "research:approve",
                }
                or not covered(g, grants(parent))
                for g in command.tuples
            ):
                raise PolicyDenied("operation-specific reviewer authority is required")
            await self.engine.validate_resources(org_id, command.tuples, tx)
            reviewer = {
                "user_id": actor,
                "membership_revision": snapshot["member"]["revision"],
                "parent_id": parent["_id"],
                "parent_generation": parent["generation"],
                "expires_at": command.expires_at,
                "tuples": [g.model_dump() for g in command.tuples],
            }
            updated = await self.store.update(
                "service_accounts",
                {"_id": row["_id"], "revision": row["revision"]},
                {"$set": {"reviewer": reviewer}, "$inc": {"revision": 1}},
                tx,
            )
            await self.engine.audit(
                org_id,
                actor,
                "service_reviewer_exception_approved",
                identifier,
                tx,
                change={"approval": reviewer},
            )
            if not updated:
                raise PolicyConflict("service revision changed")
            return updated

        return await Governance(self.engine).mutate(org_id, actor, operation)

    async def issue(self, org_id: str, actor: str, client_id: str, command: CredentialIssue) -> Row:
        await self.enabled()
        # Keep the token outside the transaction's return/audit/retry value.
        minted = mint()
        delivered = False

        async def operation(snapshot: Row, tx: Any) -> Row:
            nonlocal delivered
            row, sponsor, effective = await self.current(org_id, client_id, tx)
            if actor != row["sponsor_id"]:
                raise PolicyDenied("only the authenticated recipient/custodian may issue")
            self.engine.require(snapshot, "credentials:issue:own", "member", actor)
            command_hash = sha256(command.model_dump_json().encode()).hexdigest()
            existing = await self.store.find(
                "managed_credentials",
                {
                    "org_id": org_id,
                    "client_id": client_id,
                    "recipient_id": actor,
                    "issuance_id": command.issuance_id,
                },
                tx,
            )
            if existing:
                if existing["command_hash"] != command_hash:
                    raise PolicyConflict("issuance retry differs from its receipt")
                return {**receipt(existing), "delivered": False}
            if (
                row["revision"] != command.expected_client_revision
                or not command.tuples
                or not utc_now()
                < command.expires_at
                <= min(row["expires_at"], utc_now() + timedelta(days=90))
                or any(not covered(g, effective) for g in command.tuples)
            ):
                raise PolicyDenied("credential exceeds current approved client/sponsor policy")
            await self.engine.validate_resources(org_id, command.tuples, tx)
            scopes = sorted(
                {
                    g.action
                    for g in command.tuples
                    if g.action in ALL_SCOPES and g.selector != "none"
                }
            )
            if not scopes:
                raise PolicyDenied("credential needs a domain or gateway action")
            if command.replaces_key_id:
                old = await self.store.find(
                    "managed_credentials",
                    {
                        "org_id": org_id,
                        "client_id": client_id,
                        "key_id": command.replaces_key_id,
                        "recipient_id": actor,
                        "status": "active",
                    },
                    tx,
                )
                if not old:
                    raise PolicyDenied("replacement requires an active owned credential")
                await self._revoke_key(old, tx)
            projects = projection(command.tuples, "project", "project_directory")
            profiles = projection(command.tuples, "creator_profile")
            with tenant_scope(TenantContext(org_id)):
                key = ApiKeyDocument(
                    key_id=minted.key_id,
                    key_hash=minted.key_hash,
                    client_id=client_id,
                    name="managed:" + client_id,
                    scopes=scopes,
                    project_mode=projects.mode,
                    projects=list(projects.ids),
                    profile_mode=profiles.mode,
                    profiles=list(profiles.ids),
                    expires_at=command.expires_at,
                )
            await self.db["api_keys"].insert_one(key.model_dump(by_alias=True), session=tx)
            record = {
                "_id": "credential_" + uuid4().hex,
                "org_id": org_id,
                "client_id": client_id,
                "key_id": minted.key_id,
                "recipient_id": actor,
                "issuance_id": command.issuance_id,
                "command_hash": command_hash,
                "client_revision": row["revision"],
                "tuples": [g.model_dump() for g in command.tuples],
                "status": "active",
                "revision": 1,
                "created_at": utc_now(),
                "expires_at": command.expires_at,
                "replaces_key_id": command.replaces_key_id,
                "claimed": False,
            }
            await self.store.insert("managed_credentials", record, tx)
            await self.engine.audit(
                org_id,
                actor,
                "managed_credential_issued",
                minted.key_id,
                tx,
                change={
                    "receipt": receipt(record),
                    "service_account_id": row["subject_id"]
                    if row["subject_kind"] == "service_account"
                    else None,
                },
            )
            delivered = True
            return {**receipt(record), "delivered": True}

        result = await Governance(self.engine).mutate(org_id, actor, operation)
        if result["delivered"] and delivered:
            result["token"] = minted.token
        return result

    async def _revoke_key(self, row: Row, tx: Any) -> None:
        await self.store.update(
            "managed_credentials",
            {"_id": row["_id"], "org_id": row["org_id"]},
            {"$set": {"status": "revoked"}, "$inc": {"revision": 1}},
            tx,
        )
        await self.db["api_keys"].update_one(
            {"org_id": row["org_id"], "key_id": row["key_id"]},
            {"$set": {"status": "revoked", "revoked_at": utc_now()}, "$inc": {"revision": 1}},
            session=tx,
        )

    async def revoke(
        self, org_id: str, actor: str, client_id: str, expected: int, *, key_id: str | None = None
    ) -> Row:
        async def operation(snapshot: Row, tx: Any) -> Row:
            row = await self.client(org_id, client_id, tx)
            authority(snapshot, "credentials:revoke" if key_id else "clients:manage", row)
            if key_id:
                key = await self.store.find(
                    "managed_credentials",
                    {
                        "org_id": org_id,
                        "client_id": client_id,
                        "key_id": key_id,
                        "revision": expected,
                    },
                    tx,
                )
                if not key:
                    raise PolicyConflict("credential revision changed")
                await self._revoke_key(key, tx)
            else:
                if row["revision"] != expected:
                    raise PolicyConflict("client revision changed")
                await self.store.update(
                    "managed_clients",
                    {"_id": row["_id"], "revision": expected},
                    {"$set": {"status": "revoked"}, "$inc": {"revision": 1}},
                    tx,
                )
                await self.db["agent_clients"].update_one(
                    {"org_id": org_id, "client_id": client_id},
                    {"$set": {"status": "revoked"}, "$inc": {"revision": 1}},
                    session=tx,
                )
            await self.engine.audit(
                org_id,
                actor,
                "managed_credential_revoked" if key_id else "managed_client_revoked",
                key_id or client_id,
                tx,
            )
            return {"client_id": client_id, "key_id": key_id, "status": "revoked"}

        return await Governance(self.engine).mutate(org_id, actor, operation)

    async def resolve(self, principal: Principal, tx: Any = None) -> Principal:
        await self.enabled()
        marker = await self.store.find(
            "_managed_agents_control", {"_id": "installation", "activated": True}, tx
        )
        if not marker:
            raise PolicyDenied("legacy key reconciliation is incomplete")
        org_id = str(principal.org_id)
        cred = await self.store.find(
            "managed_credentials",
            {"org_id": org_id, "key_id": principal.key_id, "status": "active"},
            tx,
        )
        if not cred or cred["expires_at"] <= utc_now() or cred["client_id"] != principal.client_id:
            raise PolicyDenied("verified credential is unavailable")
        row, sponsor, tuples = await self.current(org_id, cred["client_id"], tx)
        if cred["client_revision"] != row["revision"] or cred["recipient_id"] != row["sponsor_id"]:
            raise PolicyDenied("credential approval changed; explicit replacement required")
        key = await self.db["api_keys"].find_one(
            {
                "org_id": org_id,
                "key_id": principal.key_id,
                "client_id": row["client_id"],
                "status": "active",
            },
            session=tx,
        )
        if not key or (key.get("expires_at") is not None and key["expires_at"] <= utc_now()):
            raise PolicyDenied("credential expired or revoked")
        from app.services.auth_service import principal_of

        raw_client = await self.db["agent_clients"].find_one(
            {"org_id": org_id, "client_id": row["client_id"]}, session=tx
        )
        stored_principal = principal_of(
            ApiKeyDocument.model_validate(key, context={"from_storage": True}),
            AgentClientDocument.model_validate(raw_client, context={"from_storage": True}),
        )
        principal = replace(stored_principal, cursor_secret=principal.cursor_secret)
        tuples = intersect(tuples, grants(cred))
        # V1 key/V4 client resource restrictions are additional current ceilings.
        tuples = restrict(
            tuples,
            project=principal.projects,
            project_directory=principal.projects,
            creator_profile=principal.profiles,
        )
        tuples = tuple(
            g
            for g in tuples
            if (g.action in key["scopes"] and g.action in principal.scopes)
            or g.action == "reviews:decide"
        )
        reviewer = False
        review: tuple[Grant, ...] = ()
        if row["subject_kind"] == "service_account":
            service = await self.service(org_id, row["subject_id"], tx)
            approval = service["reviewer"]
            if approval and approval["expires_at"] > utc_now():
                approver = await self.engine.snapshot(org_id, approval["user_id"], tx)
                parent = chain(
                    approver,
                    approval["parent_id"],
                    approval["parent_generation"],
                    approval["user_id"],
                )
                if (
                    approver["member"]["revision"] == approval["membership_revision"]
                    and approval["user_id"] != row["sponsor_id"]
                ):
                    review = intersect(tuples, grants(approval), grants(parent))
                    tuples = tuple(g for g in tuples if g.action != "reviews:decide") + tuple(
                        g for g in review if g.action == "reviews:decide"
                    )
                    reviewer = any(
                        g.action == "reviews:decide" and g.selector != "none" for g in review
                    )
        if not reviewer:
            tuples = tuple(g for g in tuples if g.action != "reviews:decide")
        policy = TenantPolicy(
            org_id,
            row["sponsor_id"],
            tuple(
                ResourceGrant(g.action, g.resource_kind, g.selector, tuple(g.ids)) for g in tuples
            ),
            client_id=row["client_id"],
            managed=True,
            subject_kind="machine",
            revision=sponsor["anchor"]["revision"],
            subject_revision=sponsor["member"]["revision"],
            engine=self.engine,
            machine_key_id=principal.key_id,
            sponsor_ids=(row["sponsor_id"],),
            team_id=row["team_id"],
            service_account_id=row["subject_id"]
            if row["subject_kind"] == "service_account"
            else None,
            sponsorship_revision=row["revision"],
            reviewer=reviewer,
            reviewer_grants=tuple(
                ResourceGrant(g.action, g.resource_kind, g.selector, tuple(g.ids)) for g in review
            ),
        )

        async def refresh() -> None:
            fresh = await self.resolve(replace(principal, managed_policy=None))
            if (
                fresh.managed_policy is None
                or fresh.managed_policy.fingerprint != policy.fingerprint
            ):
                raise PolicyConflict("machine policy changed; reauthenticate")

        async def transaction_check(session: Any) -> None:
            fresh = await self.resolve(replace(principal, managed_policy=None), session)
            if (
                fresh.managed_policy is None
                or fresh.managed_policy.fingerprint != policy.fingerprint
            ):
                raise PolicyConflict("machine policy changed")

        policy = replace(policy, refresh=refresh, transaction_check=transaction_check)
        scopes = tuple(
            s
            for s in principal.scopes
            if any(g.action == s and g.selector != "none" for g in tuples)
        )
        if not scopes:
            raise PolicyDenied("current machine policy has no usable actions")
        return replace(principal, scopes=scopes, managed_policy=policy)

    async def inspect(self, org_id: str, actor: str, client_id: str) -> Row:
        snapshot = await self.engine.snapshot(org_id, actor)
        row = await self.client(org_id, client_id)
        authority(snapshot, "clients:read", row)
        credentials = await self.store.list(
            "managed_credentials", {"org_id": org_id, "client_id": client_id}
        )
        try:
            _, _, tuples = await self.current(org_id, client_id)
        except PolicyDenied:
            tuples = ()
        return {
            "client": row,
            "credentials": [receipt(c) for c in credentials],
            "effective_tuples": [g.model_dump() for g in tuples],
            "rate_limit_profile_enforced": False,
        }

    async def put_template(
        self, org_id: str, actor: str, profile_id: str, command: TemplatePut
    ) -> Row:
        async def operation(snapshot: Row, tx: Any) -> Row:
            self.engine.require(snapshot, "clients:manage:org", "organization", org_id)
            old = await self.db["client_profiles"].find_one(
                {"org_id": org_id, "profile_id": profile_id}, session=tx
            )
            if (old["revision"] if old else 0) != command.expected_revision:
                raise PolicyConflict("template revision changed")
            with tenant_scope(TenantContext(org_id)):
                doc = ClientProfileDocument(
                    profile_id=profile_id,
                    name=command.name,
                    scopes=list(command.scopes),
                    status=ClientProfileStatus(command.status),
                    rate_limit_profile=command.rate_limit_profile,
                )
            if old:
                await self.db["client_profiles"].update_one(
                    {"_id": old["_id"], "revision": command.expected_revision},
                    {
                        "$set": {
                            "name": command.name,
                            "scopes": list(command.scopes),
                            "status": command.status,
                            "rate_limit_profile": command.rate_limit_profile,
                            "updated_at": utc_now(),
                        },
                        "$inc": {"revision": 1},
                    },
                    session=tx,
                )
            else:
                await self.db["client_profiles"].insert_one(
                    doc.model_dump(by_alias=True), session=tx
                )
            await self.engine.audit(
                org_id,
                actor,
                "client_template_updated",
                profile_id,
                tx,
                change={"scopes": list(command.scopes), "status": command.status},
            )
            return {
                "profile_id": profile_id,
                "revision": command.expected_revision + 1,
                "assigned_clients_changed": False,
                "rate_limit_profile_enforced": False,
            }

        return await Governance(self.engine).mutate(org_id, actor, operation)

    async def list_clients(self, org_id: str, actor: str) -> Row:
        snapshot = await self.engine.snapshot(org_id, actor)
        choices: list[Row] = []
        if has(snapshot, "clients:read:org", "organization", org_id):
            choices.append({})
        if has(snapshot, "clients:read:own", "member", actor):
            choices.append({"sponsor_id": actor})
        teams = [
            team_row
            for team_row in snapshot["teams"]
            if snapshot["teams"][team_row]["status"] == "active"
            and has(snapshot, "clients:read:team", "team", team_row)
        ]
        if teams:
            choices.append({"team_id": {"$in": teams}})
        query: Row = (
            {"org_id": org_id, "$or": choices}
            if choices
            else {"org_id": org_id, "_id": {"$in": []}}
        )
        rows = await self.store.list("managed_clients", query)
        return {
            "items": rows[:100],
            "truncated": len(rows) > 100,
            "policy_revision": snapshot["anchor"]["revision"],
        }

    async def revoke_service(self, org_id: str, actor: str, identifier: str, expected: int) -> Row:
        async def operation(snapshot: Row, tx: Any) -> Row:
            self.engine.require(snapshot, "clients:manage:org", "organization", org_id)
            row = await self.store.find(
                "service_accounts",
                {"org_id": org_id, "service_account_id": identifier, "revision": expected},
                tx,
            )
            if not row:
                raise PolicyConflict("service custody revision changed")
            await self.store.update(
                "service_accounts",
                {"_id": row["_id"], "revision": expected},
                {"$set": {"status": "revoked"}, "$inc": {"revision": 1}},
                tx,
            )
            await self.engine.audit(org_id, actor, "service_account_revoked", identifier, tx)
            return {"service_account_id": identifier, "status": "revoked"}

        return await Governance(self.engine).mutate(org_id, actor, operation)

    async def list_services(self, org_id: str, actor: str) -> Row:
        snapshot = await self.engine.snapshot(org_id, actor)
        choices: list[Row] = []
        if has(snapshot, "clients:read:org", "organization", org_id):
            choices.append({})
        if has(snapshot, "clients:read:own", "member", actor):
            choices.append({"custodian_id": actor})
        teams = [
            t
            for t in snapshot["teams"]
            if snapshot["teams"][t]["status"] == "active"
            and has(snapshot, "clients:read:team", "team", t)
        ]
        if teams:
            choices.append({"team_id": {"$in": teams}})
        query: Row = (
            {"org_id": org_id, "$or": choices}
            if choices
            else {"org_id": org_id, "_id": {"$in": []}}
        )
        rows = await self.store.list("service_accounts", query)
        return {
            "items": rows[:100],
            "truncated": len(rows) > 100,
            "policy_revision": snapshot["anchor"]["revision"],
        }

    async def _review_tuples(self, org_id: str, service: Row, tx: Any = None) -> tuple[Grant, ...]:
        approval = service["reviewer"]
        if not approval or approval["expires_at"] <= utc_now():
            return ()
        approver = await self.engine.snapshot(org_id, approval["user_id"], tx)
        if approver["member"]["revision"] != approval["membership_revision"] or approval[
            "user_id"
        ] in {service["custodian_id"], service["created_by"]}:
            raise PolicyDenied("independent reviewer authority changed")
        parent = chain(
            approver, approval["parent_id"], approval["parent_generation"], approval["user_id"]
        )
        return intersect(grants(approval), grants(parent))
