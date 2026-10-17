"""Explicit host-operator bootstrap/adoption review. No anonymous root claim."""

import argparse
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.cli.commands import SubParsers
from app.cli.errors import CliError, UsageError
from app.cli.output import Result, fields
from app.core.config import Settings
from app.db.client import MongoDBClient
from app.db.initializer import initialize_database
from app.identity.repository import IdentityStore
from app.models.agent import AgentClientDocument
from app.models.api_key import ApiKeyDocument
from app.models.common import utc_now
from app.security.policy.engine import PolicyEngine
from app.security.policy.evaluation import PolicyDenied
from app.security.policy.governance import Governance
from app.security.policy.models import Grant, Selector
from app.security.tenant import current_tenant
from app.services.auth_service import principal_of


async def operate(arguments: argparse.Namespace, settings: Settings) -> Result:
    context = current_tenant()
    if context is None or not settings.v6_management_policy:
        raise UsageError(
            "policy operations require explicit --org-id and enabled Phase 5 configuration"
        )
    if not arguments.writers_stopped or arguments.worker_schema_version != [
        42 if settings.v6_managed_agents else 41 if settings.v6_human_management else 40
    ]:
        raise UsageError("policy cutover requires stopped writers and matching policy workers")
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
            human_management=settings.v6_human_management,
            managed_agents=settings.v6_managed_agents,
            management_cutover=True,
        )
        engine = PolicyEngine(
            IdentityStore(mongodb.database),
            human_management=settings.v6_human_management,
            managed_agents=settings.v6_managed_agents,
        )
        if arguments.action == "activate-bootstrap":
            expiry = datetime.fromisoformat(arguments.expires_at)
            if expiry.tzinfo is None:
                raise UsageError("expiry requires an explicit timezone")
            targets = Selector(
                mode="include" if arguments.target_user_id else "none",
                ids=tuple(arguments.target_user_id),
            )
            data = await Governance(engine).bootstrap(
                context.org_id,
                arguments.user_id,
                targets=targets,
                allowed_roles=tuple(arguments.allowed_role),
                expires_at=expiry.astimezone(UTC),
                approval=arguments.approval_reference,
                approve_management_template=arguments.approve_management_template,
            )
        elif arguments.action == "recover-owner-corpus":
            if arguments.tuples_file.stat().st_size > 65536:
                raise UsageError("bounded reviewed tuple file is required")
            values = json.loads(arguments.tuples_file.read_text())
            if not isinstance(values, list) or not 1 <= len(values) <= 100:
                raise UsageError("a bounded nonempty tuple list is required")
            expiry = datetime.fromisoformat(arguments.expires_at)
            if expiry.tzinfo is None:
                raise UsageError("expiry requires an explicit timezone")
            data = await Governance(engine).operator_recover_corpus(
                context.org_id,
                arguments.user_id,
                tuple(Grant.model_validate(v) for v in values),
                expiry.astimezone(UTC),
                arguments.approval_reference,
            )
        else:
            raw = await mongodb.database["api_keys"].find_one(
                {"org_id": context.org_id, "key_id": arguments.key_id}
            )
            if not raw:
                raise UsageError("adopted credential is unavailable")
            record = ApiKeyDocument.model_validate(raw, context={"from_storage": True})
            client = None
            if record.client_id:
                row = await mongodb.database["agent_clients"].find_one(
                    {"org_id": context.org_id, "client_id": record.client_id}
                )
                if not row:
                    raise UsageError("adopted client is unavailable")
                client = AgentClientDocument.model_validate(row, context={"from_storage": True})
            await engine.approve_legacy(
                principal_of(record, client),
                arguments.approval_reference,
                utc_now() + timedelta(hours=arguments.hours),
            )
            data = {
                "org_id": context.org_id,
                "key_id": arguments.key_id,
                "adapter": "reviewed_legacy_adoption",
            }
        return Result(data=data, lines=fields(data))
    except (PolicyDenied, ValueError):
        raise CliError("policy operation refused; inspect reviewed operator state") from None
    finally:
        await mongodb.close()


def add_arguments(subparsers: SubParsers) -> None:
    group = subparsers.add_parser("policy", help="operator-only reviewed Phase 5 activation")
    actions = group.add_subparsers(dest="action", required=True)
    for action in ("activate-bootstrap", "approve-legacy-adapter", "recover-owner-corpus"):
        command = actions.add_parser(action)
        command.add_argument("--approval-reference", required=True)
        command.add_argument("--writers-stopped", action="store_true", required=True)
        command.add_argument("--worker-schema-version", type=int, action="append", required=True)
        if action == "activate-bootstrap":
            command.add_argument("--user-id", required=True)
            command.add_argument("--target-user-id", action="append", default=[])
            command.add_argument("--allowed-role", action="append", default=[])
            command.add_argument("--expires-at", required=True)
            command.add_argument(
                "--approve-management-template", action="store_true", required=True
            )
        elif action == "recover-owner-corpus":
            command.add_argument("--user-id", required=True)
            command.add_argument("--tuples-file", type=Path, required=True)
            command.add_argument("--expires-at", required=True)
        else:
            command.add_argument("--key-id", required=True)
            command.add_argument("--hours", type=int, choices=range(1, 721), required=True)
        command.set_defaults(handler=operate, needs_platform=False)
