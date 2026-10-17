"""Explicit org/host-only checkpoint of retained event-stream coverage."""

import argparse
from pathlib import Path

from app.cli.commands import SubParsers
from app.cli.errors import CliError, UsageError
from app.cli.output import Result, fields
from app.core.config import Settings
from app.db.client import MongoDBClient
from app.identity.repository import IdentityStore
from app.security.policy.engine import PolicyEngine
from app.security.policy.evaluation import PolicyDenied
from app.security.tenant import current_tenant
from app.summaries.models import Checkpoint
from app.summaries.operator import checkpoint


async def operate(arguments: argparse.Namespace, settings: Settings) -> Result:
    tenant = current_tenant()
    if tenant is None or not settings.v6_managed_agents:
        raise UsageError("explicit --org-id and verified Phase7 configuration required")
    if arguments.declaration_file.stat().st_size > 4096:
        raise UsageError("bounded coverage declaration required")
    declaration = Checkpoint.model_validate_json(arguments.declaration_file.read_text())
    mongodb = MongoDBClient(settings)
    await mongodb.connect(require_search=False)
    try:
        engine = PolicyEngine(
            IdentityStore(mongodb.database), human_management=True, managed_agents=True
        )
        data = await checkpoint(engine, tenant.org_id, declaration)
        return Result(data=data, lines=fields(data))
    except (ValueError, PolicyDenied):
        raise CliError("coverage checkpoint refused; inspect approved operator state") from None
    finally:
        await mongodb.close()


def add_arguments(subparsers: SubParsers) -> None:
    group = subparsers.add_parser("summaries", help="operator-only recorded-stream coverage")
    commands = group.add_subparsers(dest="action", required=True)
    command = commands.add_parser("checkpoint")
    command.add_argument("--declaration-file", type=Path, required=True)
    command.set_defaults(handler=operate, needs_platform=False)
