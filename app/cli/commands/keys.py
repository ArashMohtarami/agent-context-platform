"""Key commands: minting, listing, and revoking the credentials the API accepts.

These reach MongoDB directly, like the other operator commands, and that is
deliberate rather than incidental. If minting a key were an API operation it
would need a key to call it, and a key that could mint keys could widen itself
without limit. Issuing credentials is something a person does at the database.

`scopes` is the exception: it describes the platform's own vocabulary, reads
nothing, and so needs no database to answer.
"""

import argparse
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from app.cli.commands import SubParsers
from app.cli.config import CliSettings
from app.cli.errors import CliError, UnavailableError, UsageError
from app.cli.output import Result, fields, table
from app.core.config import Settings
from app.db.client import MongoDBClient, MongoDBNotReadyError
from app.repositories.api_key_repository import MongoApiKeyRepository
from app.repositories.audit_repository import MongoAuditRepository
from app.security.scopes import ALL_SCOPES, REQUIRED_SCOPES, SCOPE_DESCRIPTIONS
from app.security.tenant_maintenance import check_operator_context
from app.services.api_key_service import ApiKeyNotFoundError, ApiKeyService
from app.services.audit_service import AuditService

_COLUMNS = ("KEY ID", "NAME", "STATUS", "EXPIRES", "LAST USED", "PROJECTS", "PROFILES", "SCOPES")
# Said before the key is printed, so nobody has to be told twice.
_SHOWN_ONCE = (
    "This is the only time the key is shown. The platform stores a hash of it, "
    "so it cannot be recovered — issue a new one if it is lost."
)
_REVOKED_NOT_DELETED = "revoked rather than deleted, so the audit trail keeps a name for it"
_EVERY_PROJECT = "all"


async def create(arguments: argparse.Namespace, settings: Settings) -> Result:
    """Issue one key and print it exactly once."""
    async with _platform(settings) as (keys, audit):
        try:
            issued = await keys.create_key(
                name=arguments.name,
                scopes=arguments.scope,
                projects=arguments.project,
                profiles=arguments.profile,
                expires_in_days=arguments.expires_in_days,
            )
        except ValueError as error:
            await audit.record("key.create", "failed", reason="refused as written")
            raise UsageError(str(error), hint="see `contextctl keys scopes`") from error
        await audit.record("key.create", "allowed", reason=f"issued {issued.record.key_id}")

    record = issued.record
    described: dict[str, Any] = {
        "key_id": record.key_id,
        "name": record.name,
        "scopes": list(record.scopes),
        "projects": list(record.projects) or _EVERY_PROJECT,
        "profiles": list(record.profiles) or _EVERY_PROJECT,
        "expires_at": record.expires_at,
    }
    return Result(
        # The key is in `data` on purpose. This is the one moment it exists, so
        # `--json` has to carry it for a deployment script to put it straight
        # into a secret store rather than a human copying it out of a terminal.
        data={**described, "key": issued.token},
        lines=[*fields(described), "", _SHOWN_ONCE, "", f"key: {issued.token}"],
    )


async def list_keys(arguments: argparse.Namespace, settings: Settings) -> Result:
    """List issued keys, never their secrets."""
    async with _platform(settings) as (keys, _):
        page = await keys.list_keys(status=arguments.status, limit=arguments.limit)
    rows = [
        (
            item.key_id,
            item.name,
            item.status,
            item.expires_at,
            item.last_used_at,
            ", ".join(item.projects) or _EVERY_PROJECT,
            ", ".join(item.profiles) or _EVERY_PROJECT,
            ", ".join(item.scopes),
        )
        for item in page.items
    ]
    data = {
        "keys": [
            {
                "key_id": item.key_id,
                "name": item.name,
                "status": item.status,
                "scopes": list(item.scopes),
                "projects": list(item.projects),
                "profiles": list(item.profiles),
                "created_at": item.created_at,
                "expires_at": item.expires_at,
                "last_used_at": item.last_used_at,
            }
            for item in page.items
        ],
        "next_cursor": page.next_cursor,
    }
    lines = table(_COLUMNS, rows)
    if page.next_cursor:
        lines.append(f"\nmore: --cursor {page.next_cursor}")
    return Result(data=data, lines=lines)


async def revoke(arguments: argparse.Namespace, settings: Settings) -> Result:
    """Revoke one key, so it authenticates nothing from now on."""
    async with _platform(settings) as (keys, audit):
        try:
            record = await keys.revoke_key(arguments.key_id)
        except ApiKeyNotFoundError as error:
            await audit.record("key.revoke", "failed", reason="no such key")
            raise CliError(str(error), hint="list them with `contextctl keys list`") from error
        await audit.record("key.revoke", "allowed", reason=f"revoked {record.key_id}")
    described = {"key_id": record.key_id, "name": record.name, "status": record.status}
    return Result(data=described, lines=[*fields(described), "", _REVOKED_NOT_DELETED])


async def scopes(arguments: argparse.Namespace, settings: CliSettings) -> Result:
    """List the scopes a key can hold, and what each one permits."""
    del arguments, settings
    rows = [
        (scope, "yes" if scope in REQUIRED_SCOPES else "no", SCOPE_DESCRIPTIONS[scope])
        for scope in ALL_SCOPES
    ]
    return Result(
        data={
            "scopes": [
                {
                    "scope": scope,
                    "required": scope in REQUIRED_SCOPES,
                    "description": SCOPE_DESCRIPTIONS[scope],
                }
                for scope in ALL_SCOPES
            ]
        },
        lines=table(("SCOPE", "REQUIRED", "GRANTS"), rows),
    )


@asynccontextmanager
async def _platform(settings: Settings) -> AsyncIterator[tuple[ApiKeyService, AuditService]]:
    """Open the key service and the audit trail over one connection."""
    mongodb = MongoDBClient(settings)
    try:
        # Search is not required. A key has nothing to do with retrieval, and
        # needing a healthy coordinator to issue one would mean authentication
        # could not be bootstrapped on a platform whose search is degraded.
        await mongodb.connect(require_search=False)
    except MongoDBNotReadyError as error:
        raise UnavailableError(
            "MongoDB is not ready",
            hint="keys are issued at the database, so it has to be running",
        ) from error
    try:
        await check_operator_context(mongodb.database, enabled=settings.v6_tenant_foundation)
        audit = MongoAuditRepository(mongodb.database) if settings.audit_log_enabled else None
        yield ApiKeyService(MongoApiKeyRepository(mongodb.database)), AuditService(audit)
    finally:
        await mongodb.close()


def add_arguments(subparsers: SubParsers) -> None:
    """Register the key commands."""
    group = subparsers.add_parser(
        "keys",
        help="issue and revoke API keys",
        description="Issue, list, and revoke the API keys the platform accepts.",
    )
    commands = group.add_subparsers(dest="command", metavar="COMMAND", required=True)

    creating = commands.add_parser(
        "create",
        help="issue one key",
        description=(
            "Issue one API key and print it once. The platform stores only a hash, so a lost key "
            "cannot be recovered and has to be replaced. Naming no project grants every project, "
            "which is the widest grant and so has to be chosen by leaving --project out. Naming "
            "no profile likewise grants every creator profile."
        ),
        epilog=(
            "examples:\n"
            "  contextctl keys create --name codex --scope search:read --scope skills:read\n"
            "  contextctl keys create --name payments-agent \\\n"
            "      --scope search:read --scope context:read --scope context:write \\\n"
            "      --project payment-service --expires-in-days 90\n"
            "  contextctl --json keys create --name ci --scope search:read | jq -r .key"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    creating.add_argument("--name", required=True, help="what this key is for")
    creating.add_argument(
        "--scope",
        action="append",
        required=True,
        default=[],
        choices=list(ALL_SCOPES),
        metavar="SCOPE",
        help="repeatable; see `contextctl keys scopes`",
    )
    creating.add_argument(
        "--project",
        action="append",
        default=[],
        metavar="PROJECT",
        help="repeatable; omit to grant every project",
    )
    creating.add_argument(
        "--profile",
        action="append",
        default=[],
        metavar="PROFILE",
        help="repeatable; omit to grant every creator profile",
    )
    creating.add_argument(
        "--expires-in-days",
        type=int,
        metavar="DAYS",
        help="expire the key after this many days; omit for a key that does not expire",
    )
    creating.set_defaults(handler=create, needs_platform=False)

    listing = commands.add_parser(
        "list",
        help="list issued keys",
        description="List issued keys with scopes, project/profile grants, expiry, and last use.",
        epilog="example: contextctl keys list --status active",
    )
    listing.add_argument("--status", choices=["active", "revoked"], help="only this state")
    listing.add_argument("--limit", type=int, default=20, help="page size (default: 20)")
    listing.set_defaults(handler=list_keys, needs_platform=False)

    revoking = commands.add_parser(
        "revoke",
        help="revoke one key",
        description=(
            "Revoke one key by its identifier. It authenticates nothing from that point on, and "
            "is kept rather than deleted so the audit trail still has a name for what it did."
        ),
        epilog="example: contextctl keys revoke --key-id 0123456789abcdef",
    )
    revoking.add_argument("--key-id", required=True, help="the public identifier, not the key")
    revoking.set_defaults(handler=revoke, needs_platform=False)

    describing = commands.add_parser(
        "scopes",
        help="list the scopes a key can hold",
        description="List every scope this platform defines, and what holding it permits.",
        epilog="example: contextctl keys scopes",
    )
    # Needs no database: it describes the platform's vocabulary rather than
    # reading anything, so it answers wherever `contextctl` is installed.
    describing.set_defaults(handler=scopes, needs_platform=True)
