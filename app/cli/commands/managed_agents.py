"""Explicit operator machine cutover and legacy key classification."""

import argparse
from pathlib import Path

from app.cli.commands import SubParsers
from app.cli.errors import CliError, UsageError
from app.cli.output import Result, fields
from app.core.config import Settings
from app.db.client import MongoDBClient
from app.db.initializer import initialize_database
from app.identity.repository import IdentityStore
from app.managed_agents.models import LegacyClaim
from app.managed_agents.operator import MachineCutover
from app.security.policy.engine import PolicyEngine
from app.security.policy.evaluation import PolicyDenied
from app.security.tenant import current_tenant


async def operate(arguments: argparse.Namespace, settings: Settings) -> Result:
    tenant = current_tenant()
    if (
        tenant is None
        or not settings.v6_managed_agents
        or not arguments.writers_stopped
        or arguments.worker_schema_version != [42]
    ):
        raise UsageError(
            "explicit org, Phase7 configuration, stopped writers and only schema42 workers required"
        )
    mongodb = MongoDBClient(settings)
    await mongodb.connect(require_search=False)
    try:
        await initialize_database(
            mongodb.database,
            tenant_foundation=True,
            tenant_isolation=True,
            local_identity=True,
            sso=True,
            management_policy=True,
            human_management=True,
            managed_agents=True,
            management_cutover=True,
        )
        service = MachineCutover(
            PolicyEngine(
                IdentityStore(mongodb.database), human_management=True, managed_agents=True
            )
        )
        org_id, approval = tenant.org_id, arguments.approval_reference
        if arguments.action == "activate":
            data = await service.activate(org_id, approval)
        elif arguments.action == "claim-key":
            if arguments.policy_file.stat().st_size > 65536:
                raise UsageError("bounded reviewed claim file required")
            data = await service.claim(
                org_id, LegacyClaim.model_validate_json(arguments.policy_file.read_text()), approval
            )
        elif arguments.action == "revoke-key":
            data = await service.revoke_legacy(org_id, arguments.key_id, approval)
        else:
            data = await service.inventory(org_id, approval)
            if arguments.action == "prepare":
                async with service.engine.transaction() as tx:
                    anchor = await service.engine.anchor(org_id, tx)
                    await service.engine.fence(anchor, tx, mutation=True)
                    await service.engine.audit(
                        org_id,
                        "installation_operator",
                        "managed_machine_schema_prepared",
                        org_id,
                        tx,
                        approval=approval,
                    )
        return Result(data=data, lines=fields(data))
    except (ValueError, PolicyDenied):
        raise CliError("managed machine cutover refused; inspect reviewed operator state") from None
    finally:
        await mongodb.close()


def add_arguments(subparsers: SubParsers) -> None:
    group = subparsers.add_parser(
        "managed-agents", help="operator machine cutover and legacy key inventory"
    )
    actions = group.add_subparsers(dest="action", required=True)
    for name in ("prepare", "inventory", "claim-key", "revoke-key", "activate"):
        command = actions.add_parser(name)
        command.add_argument("--approval-reference", required=True)
        command.add_argument("--writers-stopped", action="store_true", required=True)
        command.add_argument("--worker-schema-version", type=int, action="append", required=True)
        if name == "claim-key":
            command.add_argument("--policy-file", type=Path, required=True)
        if name == "revoke-key":
            command.add_argument("--key-id", required=True)
        command.set_defaults(handler=operate, needs_platform=False)
