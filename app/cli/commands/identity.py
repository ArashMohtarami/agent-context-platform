"""Host-operator invitations and explicit, pending Phase 5 owner delegation."""

import argparse
import json
from pathlib import Path

from pymongo.errors import PyMongoError

from app.cli.commands import SubParsers
from app.cli.errors import CliError, UsageError
from app.cli.output import Result
from app.core.config import Settings
from app.db.client import MongoDBClient
from app.db.initializer import initialize_database
from app.identity.factory import build_local_identity
from app.identity.models import RootTuple
from app.security.tenant import current_tenant


async def operate(arguments: argparse.Namespace, settings: Settings) -> Result:
    context = current_tenant()
    if context is None or not settings.v6_local_identity:
        raise UsageError(
            "local identity requires explicit --org-id and enabled identity configuration"
        )
    root = None
    if arguments.action == "provision-owner":
        if not arguments.approve_project_bootstrap or arguments.root_delegation_file is None:
            raise UsageError(
                "owner provisioning needs explicit project bootstrap approval "
                "and a root delegation file"
            )
        path = arguments.root_delegation_file
        if path.stat().st_size > 65536:
            raise UsageError("root delegation file is too large")
        data = json.loads(path.read_text())
        if not isinstance(data, list) or not 1 <= len(data) <= 100:
            raise UsageError("a bounded nonempty list of reviewed root tuples is required")
        root = [RootTuple.model_validate(row).model_dump() for row in data]
    mongodb = MongoDBClient(settings)
    await mongodb.connect(require_search=False)
    service = None
    try:
        await initialize_database(
            mongodb.database, tenant_foundation=True, tenant_isolation=True, local_identity=True
        )
        service = build_local_identity(settings, mongodb.database)
        await service.operator_invite(
            context.org_id,
            arguments.email,
            arguments.name,
            approval_reference=arguments.approval_reference,
            root_delegation=root,
        )
        return Result(
            data={
                "org_id": context.org_id,
                "delivery": "local_test_sink",
                "effective_grants_created": False,
            },
            lines=[
                "Invitation saved to the configured private local mail sink. "
                "No effective grants created."
            ],
        )
    except PyMongoError:
        # Duplicate identity/index failures can embed private emails in driver text.
        raise CliError("identity operation refused; inspect private operator state") from None
    finally:
        if service:
            service.passwords.close()
        await mongodb.close()


def add_arguments(subparsers: SubParsers) -> None:
    group = subparsers.add_parser(
        "identity", help="operator-only local enrollment; no browser owner claim"
    )
    actions = group.add_subparsers(dest="action", required=True)
    for action in ("invite", "provision-owner"):
        command = actions.add_parser(action)
        command.add_argument("--email", required=True)
        command.add_argument("--name", required=True)
        command.add_argument("--approval-reference", required=True)
        if action == "provision-owner":
            command.add_argument("--root-delegation-file", type=Path, required=True)
            command.add_argument("--approve-project-bootstrap", action="store_true")
        command.set_defaults(handler=operate, needs_platform=False)
