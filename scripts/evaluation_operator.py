"""Private maintenance-container adapter to existing commissioning operations.

Mounted only in the evaluator's operator container. No served endpoint, direct
privileged row writes, fixture imports, activation exception or default password.
"""

import argparse
import asyncio
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.backup.manifest import read
from app.backup.naming import manifest_name
from app.backup.tools import MongoTools
from app.cli.commands import identity, managed_agents, management, organizations, policy
from app.core.config import Settings
from app.db.client import MongoDBClient
from app.identity.repository import IdentityStore
from app.identity.service import local_subject
from app.repositories.backup_repository import MongoBackupRepository
from app.services.backup_service import BackupService


def agent_value_policy(intent, state, review, policy):
    """Exact additional operator intent, never inferred from an owner role."""
    import hashlib

    from app.management.models import RootApproval

    fields = {
        "version",
        "org_id",
        "owner_id",
        "worker_id",
        "projects",
        "clients",
        "approval_reference",
        "policy_sha256",
    }
    if (
        set(review) != fields
        or type(review["version"]) is not int
        or review["version"] != 1
        or state["schema"] != 42
        or review["org_id"] != intent["org_id"]
        or review["projects"] != ["evaluation-notes", "evaluation-agent-value-other"]
        or review["clients"] != ["evaluation-handoff-a", "evaluation-handoff-b"]
        or not isinstance(review["approval_reference"], str)
        or not review["approval_reference"].startswith("fictional-v8-agent-value-")
    ):
        raise ValueError("explicit bounded agent-value operator review required")
    for role in ("owner", "worker"):
        member = state["members"][role]
        if not member["active"] or not member["mfa"] or review[role + "_id"] != member["user_id"]:
            raise ValueError("verified independent enrolled recipients required")
    if review["owner_id"] == review["worker_id"]:
        raise ValueError("independent approval required")
    encoded = (json.dumps(policy, sort_keys=True, separators=(",", ":")) + "\n").encode()
    if hashlib.sha256(encoded).hexdigest() != review["policy_sha256"]:
        raise ValueError("operator policy commitment changed")
    command = RootApproval.model_validate(policy)
    permitted = {
        ("projects:read", "project_directory"): set(review["projects"]),
        ("context:read", "project"): set(review["projects"]),
        ("search:read", "project"): set(review["projects"]),
        ("context:write", "project"): {"evaluation-notes"},
        ("agent:self", "organization_knowledge"): {review["org_id"]},
        **{(a, "project"): {"evaluation-notes"} for a in ("memory:read", "memory:write")},
        **{(a, "client_memory"): set(review["clients"]) for a in ("memory:read", "memory:write")},
        **{
            (a, "member"): {review["worker_id"]}
            for a in ("credentials:issue:own", "credentials:revoke:own", "clients:read:own")
        },
    }
    if (
        command.targets.mode != "include"
        or tuple(command.targets.ids) != (review["worker_id"],)
        or tuple(command.allowed_roles) != ("contributor",)
    ):
        raise ValueError("operator targets widened")
    seen = set()
    for row in command.ceiling:
        key = (row.action, row.resource_kind)
        if (
            key in seen
            or key not in permitted
            or row.selector != "include"
            or set(row.ids) != permitted[key]
            or row.conditions
        ):
            raise ValueError("operator ceiling widened or incomplete")
        seen.add(key)
    if seen != set(permitted):
        raise ValueError("operator ceiling incomplete")
    # Existing management tuples remain service-validated; no new management action.
    expected_management = {
        ("grants:read", "member", (review["owner_id"],)),
        ("grants:read", "member", (review["worker_id"],)),
        ("grants:delegate", "member", (review["worker_id"],)),
        ("members:read", "member", (review["worker_id"],)),
        ("clients:manage:org", "organization", (review["org_id"],)),
        ("clients:read:org", "organization", (review["org_id"],)),
    }
    if (
        len(command.tuples) != len(expected_management)
        or {(r.action, r.resource_kind, tuple(r.ids)) for r in command.tuples}
        != expected_management
        or any(r.selector != "include" or r.conditions for r in command.tuples)
    ):
        raise ValueError("additional management authority refused")
    if (
        not datetime.now(UTC)
        < command.expires_at
        <= min(datetime.fromisoformat(intent["expires_at"]), datetime.now(UTC) + timedelta(hours=2))
    ):
        raise ValueError("bounded operator expiry required")
    return command


def settings(stage):
    return Settings(
        _env_file=None,
        v6_tenant_foundation=True,
        v6_tenant_isolation=stage >= 37,
        v6_local_identity=stage >= 38,
        v6_sso=stage >= 39,
        v6_management_policy=stage >= 40,
        v6_human_management=stage >= 41,
        v6_managed_agents=stage >= 42,
        v6_console_enabled=False,
    )


async def invoke(module, stage, **values):
    arguments = argparse.Namespace(**values)
    result = await module.operate(arguments, settings(stage))
    return result.data


async def snapshot(db, intent):
    members = {}
    for role in ("owner", "worker"):
        email = local_subject(role + "@example.test")
        ident = await IdentityStore(db).find(
            "user_identities", {"provider": "local", "subject": email}
        )
        if ident:
            member = await db.organization_memberships.find_one(
                {"org_id": intent["org_id"], "user_id": ident["user_id"]}
            )
            if member:
                members[role] = {
                    "user_id": member["user_id"],
                    "active": member["status"] == "active" and bool(member["verified_at"]),
                    "mfa": bool(
                        await db.mfa_factors.find_one(
                            {"_id": member["user_id"], "status": "active"}
                        )
                    ),
                }
    ledger = [row["version"] async for row in db["_schema_migrations"].find({})]
    return {"schema": max(ledger, default=0), "members": members}


async def search_probe(db):
    """Real bounded query proof, separate from the production health collection."""
    from pymongo.errors import PyMongoError
    from pymongo.operations import SearchIndexModel

    probe = db["_evaluation_readiness_probe"]
    # Only this fresh instance, with its API stopped and lifecycle lock held.
    # Reclaim a probe left by a killed operator; never touch corpus/health rows.
    await probe.drop()
    await probe.replace_one(
        {"_id": "evaluation"},
        {"_id": "evaluation", "content": "evaluationreadiness", "embedding": [1.0, 0.0, 0.0]},
        upsert=True,
    )
    models = [
        SearchIndexModel(name="evaluation_text", definition={"mappings": {"dynamic": True}}),
        SearchIndexModel(
            name="evaluation_vector",
            type="vectorSearch",
            definition={
                "fields": [
                    {
                        "type": "vector",
                        "path": "embedding",
                        "numDimensions": 3,
                        "similarity": "cosine",
                    }
                ]
            },
        ),
    ]
    created = False
    try:
        async with asyncio.timeout(120):
            while True:
                try:
                    if not created:
                        await probe.create_search_indexes(models)
                        created = True
                    indexes = await (await probe.list_search_indexes()).to_list(None)
                    if len(indexes) == 2 and all(
                        i.get("status") == "READY" and i.get("queryable") for i in indexes
                    ):
                        lexical = await (
                            await probe.aggregate(
                                [
                                    {
                                        "$search": {
                                            "index": "evaluation_text",
                                            "text": {
                                                "path": "content",
                                                "query": "evaluationreadiness",
                                            },
                                        }
                                    }
                                ]
                            )
                        ).to_list(None)
                        vector = await (
                            await probe.aggregate(
                                [
                                    {
                                        "$vectorSearch": {
                                            "index": "evaluation_vector",
                                            "path": "embedding",
                                            "queryVector": [1.0, 0.0, 0.0],
                                            "numCandidates": 10,
                                            "limit": 1,
                                        }
                                    }
                                ]
                            )
                        ).to_list(None)
                        if len(lexical) == len(vector) == 1:
                            return {"search": "READY/queryable/lexical/vector"}
                except PyMongoError:
                    # mongot may still be starting. Timeout remains a failed
                    # prerequisite; neither metadata nor record counts pass it.
                    pass
                await asyncio.sleep(1)
    finally:
        await probe.drop()


async def operate(action):
    intent = json.loads(Path("/intent/intent.json").read_text())
    dbclient = MongoDBClient(settings(36))
    await dbclient.connect(require_search=False)
    try:
        db = dbclient.database
        state = await snapshot(db, intent)
        if action == "snapshot":
            return state
        if action == "probe":
            return await search_probe(db)
        org_id = intent["org_id"]
        approval = intent["approval"]
        from app.security.tenant import TenantContext, tenant_scope

        with tenant_scope(TenantContext(org_id)):
            if action == "agent-value-root":
                review = json.loads(Path("/intent/agent-value-review.json").read_text())
                policy_path = Path("/intent/agent-value-root.json")
                command = agent_value_policy(
                    intent, state, review, json.loads(policy_path.read_text())
                )
                await invoke(
                    management,
                    42,
                    action="approve-root",
                    user_id=review["owner_id"],
                    policy_file=policy_path,
                    approval_reference=review["approval_reference"],
                    writers_stopped=True,
                    worker_schema_version=[42],
                )
                return {
                    "approved": True,
                    "owner_id": review["owner_id"],
                    "worker_id": review["worker_id"],
                    "automatic_grants": False,
                    "expires_at": command.expires_at.isoformat(),
                }
            if action == "agent-value-readiness":
                indexes = {}
                vectors = {}
                for name in ("project_context", "memories"):
                    indexes[name] = [
                        {
                            "name": r["name"],
                            "status": r.get("status"),
                            "queryable": r.get("queryable", False),
                        }
                        async for r in await db[name].list_search_indexes()
                    ]
                    row = await db[name].find_one(
                        {"org_id": org_id, "embedding_model": "/evaluation-model"}
                    )
                    vectors[name] = bool(
                        row
                        and row.get("embedding_dimensions") == 384
                        and len(row.get("embedding", [])) == 384
                    )
                return {"indexes": indexes, "real_model_vectors": vectors}
            if action == "foundation":
                if state["schema"] > 36:
                    return state
                if not await db.organizations.find_one({"org_id": org_id}):
                    counts = {
                        name: await db[name].count_documents({})
                        for name in await db.list_collection_names()
                    }
                    if any(counts.values()):
                        raise ValueError("fresh installation requires a genuinely empty database")
                    await invoke(
                        organizations,
                        36,
                        action="provision",
                        name="Fictional evaluation " + intent["instance"],
                        slug=org_id,
                    )
                service = BackupService(
                    settings(36), MongoTools(settings(36)), repository=MongoBackupRepository(db)
                )
                backups = service.stored()
                if not backups:
                    backup = await service.create(prune=False)
                elif len(backups) == 1:
                    backup = backups[0]
                else:
                    raise ValueError("ambiguous original adoption backup")
                manifest_path = backup.archive.parent / manifest_name(backup.archive.name)
                if not backup.manifest or read(manifest_path) != backup.manifest:
                    raise ValueError("original backup manifest unavailable")
                return await invoke(
                    organizations,
                    36,
                    action="adopt",
                    dry_run=False,
                    adoption_id=intent["adoption_id"],
                    backup_archive=backup.archive,
                    backup_manifest=manifest_path,
                    writers_stopped=True,
                    worker_schema_version=[36],
                )
            if action == "isolation":
                if state["schema"] > 37:
                    return state
                await invoke(
                    organizations,
                    37,
                    action="prepare-isolation",
                    writers_stopped=True,
                    worker_schema_version=[37],
                )
                return await invoke(
                    organizations,
                    37,
                    action="activate-isolation",
                    writers_stopped=True,
                    worker_schema_version=[37],
                    evidence=[
                        Path("/evidence") / name / "result.xml"
                        for name in ("activation-unit", "activation-real", "activation-cutover")
                    ],
                )
            if action == "invitations":
                for role in ("owner", "worker"):
                    if role in state["members"]:
                        continue
                    await invoke(
                        identity,
                        39,
                        action="provision-owner" if role == "owner" else "invite",
                        email=role + "@example.test",
                        name="Fictional " + role,
                        approval_reference=approval,
                        approve_project_bootstrap=True,
                        root_delegation_file=Path("/intent/root.json"),
                    )
                return await snapshot(db, intent)
            if action != "management":
                raise ValueError("unknown maintenance operation")
            if set(state["members"]) != {"owner", "worker"} or not all(
                m["active"] and m["mfa"] for m in state["members"].values()
            ):
                raise ValueError("both recipients must verify enrollment and confirm MFA")
            owner, worker = (state["members"][role]["user_id"] for role in ("owner", "worker"))
            if state["schema"] <= 40:
                await invoke(
                    policy,
                    40,
                    action="activate-bootstrap",
                    user_id=owner,
                    target_user_id=[worker],
                    allowed_role=["contributor"],
                    expires_at=intent["expires_at"],
                    approval_reference=approval,
                    approve_management_template=True,
                    writers_stopped=True,
                    worker_schema_version=[40],
                )
            if state["schema"] < 41:
                await invoke(
                    management,
                    41,
                    action="activate",
                    approval_reference=approval,
                    writers_stopped=True,
                    worker_schema_version=[41],
                )
            if state["schema"] < 42:
                prepared = await invoke(
                    managed_agents,
                    42,
                    action="prepare",
                    approval_reference=approval,
                    writers_stopped=True,
                    worker_schema_version=[42],
                )
                if prepared["keys"]:
                    raise ValueError("empty-store mode refuses legacy credentials")
            control = await db["_managed_agents_control"].find_one({"_id": "installation"})
            if not control or not control.get("activated"):
                await invoke(
                    managed_agents,
                    42,
                    action="activate",
                    approval_reference=approval,
                    writers_stopped=True,
                    worker_schema_version=[42],
                )
            if not await db.governance_events.find_one(
                {
                    "org_id": org_id,
                    "action": "human_management_root_approved",
                    "approval_reference": approval,
                }
            ):
                from app.management.memberships import Memberships
                from app.management.models import RootApproval
                from app.security.policy.engine import PolicyEngine

                def grant(action, kind, identifier):
                    return {
                        "action": action,
                        "resource_kind": kind,
                        "selector": "include",
                        "ids": [identifier],
                    }

                own = [
                    grant("grants:read", "member", owner),
                    grant("grants:read", "member", worker),
                    grant("grants:delegate", "member", worker),
                    grant("members:read", "member", worker),
                    grant("clients:manage:org", "organization", org_id),
                    grant("clients:read:org", "organization", org_id),
                ]
                ceiling = json.loads(Path("/intent/root.json").read_text()) + [
                    grant(a, "member", worker)
                    for a in ("credentials:issue:own", "credentials:revoke:own", "clients:read:own")
                ]
                command = RootApproval(
                    tuples=own,
                    ceiling=ceiling,
                    targets={"mode": "include", "ids": [worker]},
                    allowed_roles=["contributor"],
                    expires_at=intent["expires_at"],
                )
                await Memberships(
                    PolicyEngine(IdentityStore(db), human_management=True, managed_agents=True)
                ).operator_approve_root(org_id, owner, command, approval)
            return await snapshot(db, intent)
    finally:
        await dbclient.close()


if __name__ == "__main__":
    import sys

    # Exceptions remain visible in private operator logs; output contains no secrets.
    print(json.dumps(asyncio.run(operate(sys.argv[1])), default=str))
