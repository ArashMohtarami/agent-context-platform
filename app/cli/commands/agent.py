"""Operator-only V4 Phase 1 agent client commands."""

import argparse
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from app.cli.commands import SubParsers
from app.cli.errors import CliError
from app.cli.output import Result, fields, table
from app.core.config import Settings
from app.db.client import MongoDBClient
from app.models.agent import AgentClientType
from app.repositories.agent_repository import (
    MongoAgentActivityRepository,
    MongoAgentClientRepository,
)
from app.repositories.api_key_repository import MongoApiKeyRepository
from app.security.tenant_maintenance import check_operator_context
from app.services.agent_client_service import AgentClientNotFoundError, AgentClientService
from app.services.api_key_service import ApiKeyService


async def list_clients(args: argparse.Namespace, settings: Settings) -> Result:
    async with _service(settings) as service:
        page = await service.list_clients(limit=args.limit)
    return Result(
        data={
            "clients": [item.model_dump(mode="json") for item in page.items],
            "next_cursor": page.next_cursor,
        },
        lines=table(
            ("CLIENT ID", "TYPE", "STATUS"),
            [(x.client_id, x.client_type, x.status) for x in page.items],
        ),
    )


async def create(args: argparse.Namespace, settings: Settings) -> Result:
    async with _service(settings) as service:
        item = await service.create_client(
            client_id=args.client_id,
            client_type=args.type,
            name=args.name,
            owner_id=args.owner_id,
            scopes=args.scope,
            project_ids=args.project,
        )
    return Result(data=item.model_dump(mode="json"), lines=fields(item.model_dump(mode="json")))


async def show(args: argparse.Namespace, settings: Settings) -> Result:
    try:
        async with _service(settings) as service:
            item = await service.get_client(args.client_id)
    except AgentClientNotFoundError as error:
        raise CliError(str(error), hint="list clients with `contextctl agent list`") from error
    return Result(data=item.model_dump(mode="json"), lines=fields(item.model_dump(mode="json")))


async def token_create(args: argparse.Namespace, settings: Settings) -> Result:
    try:
        async with _service(settings) as service:
            issued = await service.create_credential(
                args.client_id,
                scopes=args.scope or None,
                projects=args.project or None,
                expires_in_days=args.expires_in_days,
            )
    except AgentClientNotFoundError as error:
        raise CliError(str(error), hint="create the client before issuing its token") from error
    return Result(
        data={"key_id": issued.record.key_id, "client_id": args.client_id, "token": issued.token},
        lines=["This is the only time the token is shown.", f"token: {issued.token}"],
    )


async def revoke(args: argparse.Namespace, settings: Settings) -> Result:
    try:
        async with _service(settings) as service:
            item = await service.revoke_client(args.client_id)
    except AgentClientNotFoundError as error:
        raise CliError(str(error), hint="list clients with `contextctl agent list`") from error
    return Result(
        data={"client_id": item.client_id, "status": item.status},
        lines=fields({"client_id": item.client_id, "status": item.status}),
    )


async def activity(args: argparse.Namespace, settings: Settings) -> Result:
    """Show bounded, content-free V4 activity for an operator investigation."""
    mongodb = MongoDBClient(settings)
    await mongodb.connect(require_search=False)
    try:
        await check_operator_context(
            mongodb.database,
            enabled=settings.v6_tenant_foundation,
            management_policy=settings.v6_management_policy,
        )
        page = await MongoAgentActivityRepository(mongodb.database).list(
            client_id=args.client_id,
            session_id=args.session_id,
            project_id=args.project_id,
            tool=args.tool,
            status=args.status,
            limit=args.limit,
            cursor=args.cursor,
        )
    finally:
        await mongodb.close()
    records = [item.model_dump(mode="json") for item in page.items]
    return Result(
        data={"events": records, "next_cursor": page.next_cursor},
        lines=table(
            ("CREATED", "CLIENT", "OPERATION", "RESOURCE", "STATUS"),
            [
                (
                    item.created_at.isoformat(),
                    item.client_id,
                    item.operation,
                    "" if item.resource_id is None else f"{item.resource_type}:{item.resource_id}",
                    item.status,
                )
                for item in page.items
            ],
        ),
    )


@asynccontextmanager
async def _service(settings: Settings) -> AsyncIterator[AgentClientService]:
    mongodb = MongoDBClient(settings)
    await mongodb.connect(require_search=False)
    try:
        await check_operator_context(
            mongodb.database,
            enabled=settings.v6_tenant_foundation,
            management_policy=settings.v6_management_policy,
        )
        yield AgentClientService(
            MongoAgentClientRepository(mongodb.database),
            ApiKeyService(MongoApiKeyRepository(mongodb.database)),
        )
    finally:
        await mongodb.close()


def add_arguments(groups: SubParsers) -> None:
    parser = groups.add_parser("agent", help="administer V4 agent clients")
    commands = parser.add_subparsers(dest="command", required=True)
    listing = commands.add_parser("list")
    listing.add_argument("--limit", type=int, default=20)
    listing.set_defaults(handler=list_clients, needs_platform=False)
    creating = commands.add_parser("create")
    creating.add_argument("--client-id", required=True)
    creating.add_argument(
        "--type",
        required=True,
        choices=[client_type.value for client_type in AgentClientType],
    )
    creating.add_argument("--name", required=True)
    creating.add_argument("--owner-id", required=True)
    creating.add_argument("--scope", action="append", default=[])
    creating.add_argument("--project", action="append", default=[])
    creating.set_defaults(handler=create, needs_platform=False)
    showing = commands.add_parser("show")
    showing.add_argument("client_id")
    showing.set_defaults(handler=show, needs_platform=False)
    token = commands.add_parser("token")
    token_commands = token.add_subparsers(dest="token_command", required=True)
    token_creating = token_commands.add_parser("create")
    token_creating.add_argument("client_id")
    token_creating.add_argument("--scope", action="append", default=[])
    token_creating.add_argument("--project", action="append", default=[])
    token_creating.add_argument("--expires-in-days", type=int)
    token_creating.set_defaults(handler=token_create, needs_platform=False)
    revoking = commands.add_parser("revoke")
    revoking.add_argument("client_id")
    revoking.set_defaults(handler=revoke, needs_platform=False)
    activities = commands.add_parser("activity", help="show content-free agent activity")
    activities.add_argument("--client-id")
    activities.add_argument("--session-id")
    activities.add_argument("--project-id")
    activities.add_argument("--tool")
    activities.add_argument("--status", choices=["succeeded", "denied", "failed"])
    activities.add_argument("--limit", type=int, default=20)
    activities.add_argument("--cursor")
    activities.set_defaults(handler=activity, needs_platform=False)
