"""Explicit operator-only schema cutover and reviewed root management ceiling."""

import argparse
from pathlib import Path

from app.cli.commands import SubParsers
from app.cli.errors import CliError, UsageError
from app.cli.output import Result, fields
from app.core.config import Settings
from app.db.client import MongoDBClient
from app.db.initializer import initialize_database
from app.identity.repository import IdentityStore
from app.management.memberships import Memberships
from app.management.models import RootApproval
from app.security.policy.engine import PolicyEngine
from app.security.policy.evaluation import PolicyDenied
from app.security.tenant import current_tenant


async def operate(arguments: argparse.Namespace, settings: Settings) -> Result:
    context = current_tenant()
    if (
        context is None
        or not arguments.approval_reference.strip()
        or len(arguments.approval_reference) > 512
        or not settings.v6_human_management
        or not arguments.writers_stopped
        or arguments.worker_schema_version != [41]
    ):
        raise UsageError(
            "human management requires --org-id, enabled Phase 6, stopped writers "
            "and only schema-41 workers"
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
            management_cutover=True,
        )
        if arguments.action == "approve-root":
            if arguments.policy_file.stat().st_size > 65536:
                raise UsageError("bounded reviewed policy file is required")
            command = RootApproval.model_validate_json(arguments.policy_file.read_text())
            data = await Memberships(
                PolicyEngine(IdentityStore(mongodb.database), human_management=True)
            ).operator_approve_root(
                context.org_id, arguments.user_id, command, arguments.approval_reference
            )
        else:
            engine = PolicyEngine(IdentityStore(mongodb.database), human_management=True)
            async with engine.transaction() as tx:
                anchor = await engine.anchor(context.org_id, tx)
                await engine.fence(anchor, tx, mutation=True)
                await engine.audit(
                    context.org_id,
                    "installation_operator",
                    "human_management_schema_activated",
                    context.org_id,
                    tx,
                    approval=arguments.approval_reference,
                )
            data = {"org_id": context.org_id, "schema_version": 41, "automatic_grants": False}
        return Result(data=data, lines=fields(data))
    except (PolicyDenied, ValueError):
        raise CliError(
            "human management operation refused; inspect reviewed operator state"
        ) from None
    finally:
        await mongodb.close()


def add_arguments(subparsers: SubParsers) -> None:
    group = subparsers.add_parser("management", help="operator-only human management activation")
    actions = group.add_subparsers(dest="action", required=True)
    for action in ("activate", "approve-root"):
        command = actions.add_parser(action)
        command.add_argument("--approval-reference", required=True)
        command.add_argument("--writers-stopped", action="store_true", required=True)
        command.add_argument("--worker-schema-version", type=int, action="append", required=True)
        if action == "approve-root":
            command.add_argument("--user-id", required=True)
            command.add_argument("--policy-file", type=Path, required=True)
        command.set_defaults(handler=operate, needs_platform=False)
