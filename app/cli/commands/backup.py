"""Backup, restore, and the management of the set of backups.

MongoDB is the platform's only primary state, so this is the whole of what has
to survive a lost disk. Search indexes are deliberately not backed up: they are
derived state that `contextctl skills reindex` rebuilds from the stored
documents. The **vectors are** backed up — they live in the documents, and
regenerating them means running the embedding model over every chunk again,
which is minutes to hours of CPU for something already computed.

The application password never reaches the process table. MongoDB's own tools
read it from a configuration file, so it is written to a private file for the
length of the call and removed afterwards rather than passed in argv, where
every process on the host could read it.

Nothing here decides anything. Naming, retention, and integrity live in
`app.backup`; the sequencing lives in `app.services.backup_service`.
"""

import argparse
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from app.backup.tools import DUMP, RESTORE, MongoTools, ToolFailedError, ToolNotFoundError
from app.cli.commands import SubParsers
from app.cli.errors import CliError, UnavailableError, UsageError
from app.cli.output import Result, fields, table
from app.core.config import Settings
from app.db.client import MongoDBClient, MongoDBNotReadyError
from app.repositories.backup_repository import MongoBackupRepository
from app.services.backup_service import (
    BackupError,
    BackupNotFoundError,
    BackupService,
    StoredBackup,
    disk_free,
    total_bytes,
)

_MISSING_TOOL_HINT = (
    "MongoDB Database Tools are not on PATH; rebuild the context-api image "
    "or install mongodump and mongorestore in this operator environment"
)
_NOT_BACKED_UP = "search indexes are not backed up; rebuild them with `contextctl skills reindex`"
_COLUMNS = ("BACKUP", "TAKEN", "SIZE", "DOCUMENTS", "KEPT BY")


async def backup(arguments: argparse.Namespace, settings: Settings) -> Result:
    """Take one backup, into the managed directory or a path you named."""
    if arguments.directory and arguments.output is None:
        raise UsageError("--directory requires --output")
    if arguments.output is not None:
        return await _backup_to_path(arguments, settings)

    async with _service(settings, _timeout_seconds(arguments, settings)) as service:
        try:
            stored = await service.create(gzip=not arguments.no_gzip, prune=not arguments.no_prune)
        except (BackupError, ToolFailedError) as error:
            raise CliError(str(error)) from error
        except ToolNotFoundError as error:
            raise UnavailableError(str(error), hint=_MISSING_TOOL_HINT) from error
        kept = service.stored()
        free = disk_free(service.directory)

    manifest = stored.manifest
    data: dict[str, Any] = {
        "backup": stored.name,
        "directory": str(service.directory),
        "database": settings.mongodb_database,
        "bytes": stored.bytes,
        "documents": manifest.documents if manifest else None,
        "collections": len(manifest.collections) if manifest else None,
        "kept": len(kept),
        "kept_bytes": total_bytes(kept),
        "disk_free_bytes": free,
    }
    return Result(data=data, lines=[*fields(data), "", _NOT_BACKED_UP])


async def _backup_to_path(arguments: argparse.Namespace, settings: Settings) -> Result:
    """Dump to an explicit path, unmanaged: no manifest, no retention.

    Kept because it is what `contextctl backup --output` has always meant, and
    because a one-off copy before a risky migration is a real need that should
    not land in the retained set and push a real backup out of it.
    """
    output = arguments.output.expanduser()
    if output.exists():
        raise UsageError(f"{output} already exists", hint="choose another path, or remove it first")
    output.parent.mkdir(parents=True, exist_ok=True)
    tools = MongoTools(settings, timeout_seconds=_timeout_seconds(arguments, settings))
    command = tools.connection_arguments(database=settings.mongodb_database)
    command.append(f"--out={output}" if arguments.directory else f"--archive={output}")
    if not arguments.no_gzip:
        command.append("--gzip")
    await _run(tools, DUMP, command)
    _protect_output(output)

    data: dict[str, Any] = {
        "database": settings.mongodb_database,
        "output": str(output),
        "format": "directory" if arguments.directory else "archive",
        "gzip": not arguments.no_gzip,
        "bytes": _size(output),
    }
    return Result(data=data, lines=[*fields(data), "", _NOT_BACKED_UP])


async def restore(arguments: argparse.Namespace, settings: Settings) -> Result:
    """Restore the application database from a backup."""
    if not arguments.yes:
        raise UsageError(
            "a restore overwrites the application database",
            hint="stop the API first, then pass --yes to confirm",
        )
    if arguments.input is None and (arguments.directory or arguments.no_gzip):
        raise UsageError("--directory and --no-gzip apply only with --input")
    if arguments.input is not None:
        return await _restore_from_path(arguments, settings)

    async with _service(settings, _timeout_seconds(arguments, settings)) as service:
        try:
            stored = service.latest() if arguments.latest else service.get(arguments.name)
        except BackupNotFoundError as error:
            raise CliError(str(error)) from error
        try:
            await service.restore(stored, drop=arguments.drop)
        except (BackupError, ToolFailedError) as error:
            raise CliError(str(error)) from error
        except ToolNotFoundError as error:
            raise UnavailableError(str(error), hint=_MISSING_TOOL_HINT) from error

    data = {
        "database": settings.mongodb_database,
        "backup": stored.name,
        "taken": stored.created_at,
        "dropped_first": arguments.drop,
    }
    return Result(data=data, lines=[*fields(data), "", *_AFTER_RESTORE])


async def _restore_from_path(arguments: argparse.Namespace, settings: Settings) -> Result:
    """Restore from an explicit path, managed or not."""
    source = arguments.input.expanduser()
    if not source.exists():
        raise UsageError(f"{source} does not exist")
    tools = MongoTools(settings, timeout_seconds=_timeout_seconds(arguments, settings))
    command = tools.connection_arguments()
    if arguments.directory:
        command.extend([f"--db={settings.mongodb_database}", f"--dir={source}"])
    else:
        command.append(f"--archive={source}")
    if not arguments.no_gzip:
        command.append("--gzip")
    if arguments.drop:
        command.append("--drop")
    check = [*command, "--dryRun", "--quiet"]
    if not arguments.directory:
        check.append(f"--nsInclude={settings.mongodb_database}.*")
        command.append(f"--nsInclude={settings.mongodb_database}.*")
    await _run(tools, RESTORE, check)
    await _run(tools, RESTORE, command)

    data = {
        "database": settings.mongodb_database,
        "input": str(source),
        "format": "directory" if arguments.directory else "archive",
        "dropped_first": arguments.drop,
    }
    return Result(data=data, lines=[*fields(data), "", *_AFTER_RESTORE])


async def list_backups(arguments: argparse.Namespace, settings: Settings) -> Result:
    """List the managed backups, newest first."""
    async with _service(settings, _timeout_seconds(arguments, settings)) as service:
        stored = service.stored()
        decision = service.plan_prune()
        free = disk_free(service.directory)
        directory = service.directory

    rows = [
        (
            item.name,
            item.created_at,
            item.bytes,
            item.manifest.documents if item.manifest else "?",
            ", ".join(decision.reasons.get(item.name, ())) or "expired",
        )
        for item in stored
    ]
    data = {
        "directory": str(directory),
        "backups": [_described(item, decision.reasons.get(item.name, ())) for item in stored],
        "bytes": total_bytes(stored),
        "disk_free_bytes": free,
    }
    lines = table(_COLUMNS, rows)
    if stored:
        lines.append(f"\n{len(stored)} backups, {total_bytes(stored)} bytes in {directory}")
    else:
        lines.append(f"\nno backups in {directory}")
    return Result(data=data, lines=lines)


async def verify(arguments: argparse.Namespace, settings: Settings) -> Result:
    """Check that a backup is intact, and optionally that it restores."""
    async with _service(settings, _timeout_seconds(arguments, settings)) as service:
        try:
            targets = _targets(service, arguments)
        except BackupNotFoundError as error:
            raise CliError(str(error)) from error
        checked = []
        for stored in targets:
            try:
                checked.append(await service.verify(stored, restore=arguments.restore))
            except ToolNotFoundError as error:
                raise UnavailableError(str(error), hint=_MISSING_TOOL_HINT) from error

    data = {"verified": [item.describe() for item in checked]}
    rows = [
        (
            item.name,
            "yes" if item.sound else "NO",
            item.checksum_matches,
            item.readable_archive,
            item.restored_documents if item.restored_documents is not None else "—",
            "; ".join(item.failures) or "—",
        )
        for item in checked
    ]
    lines = table(
        ("BACKUP", "SOUND", "CHECKSUM", "READABLE", "RESTORED", "FAILURES"),
        rows,
    )
    unsound = [item.name for item in checked if not item.sound]
    if unsound:
        raise CliError(
            f"{len(unsound)} of {len(checked)} backups did not verify",
            hint="; ".join(
                f"{item.name}: {'; '.join(item.failures)}" for item in checked if not item.sound
            ),
        )
    if not arguments.restore and checked:
        lines.append("\nchecked the file, not a restore; add --restore to prove it restores")
    return Result(data=data, lines=lines)


async def prune(arguments: argparse.Namespace, settings: Settings) -> Result:
    """Apply the retention policy, or show what it would do."""
    async with _service(settings, _timeout_seconds(arguments, settings)) as service:
        policy = service.policy
        decision = service.plan_prune() if arguments.dry_run else service.prune()
        directory = service.directory

    data = {
        "directory": str(directory),
        "policy": {"daily": policy.daily, "weekly": policy.weekly, "monthly": policy.monthly},
        "kept": list(decision.keep),
        "removed": list(decision.remove),
        "dry_run": arguments.dry_run,
    }
    rows = [(name, ", ".join(decision.reasons.get(name, ())), "keep") for name in decision.keep]
    rows.extend((name, "", "remove") for name in decision.remove)
    lines = table(("BACKUP", "KEPT BY", "ACTION"), rows)
    verb = "would remove" if arguments.dry_run else "removed"
    lines.append(f"\n{verb} {len(decision.remove)}, kept {len(decision.keep)}")
    return Result(data=data, lines=lines)


def _targets(service: BackupService, arguments: argparse.Namespace) -> list[StoredBackup]:
    """Return the backups one verify invocation should check."""
    if arguments.all:
        found = list(service.stored())
        if not found:
            raise BackupNotFoundError(f"no backups in {service.directory}")
        return found
    if arguments.name is not None:
        return [service.get(arguments.name)]
    return [service.latest()]


def _described(item: StoredBackup, kept_by: tuple[str, ...]) -> dict[str, Any]:
    """Describe one stored backup for a JSON result."""
    manifest = item.manifest
    return {
        "backup": item.name,
        "created_at": item.created_at,
        "bytes": item.bytes,
        "documents": manifest.documents if manifest else None,
        "collections": len(manifest.collections) if manifest else None,
        "collection_counts": dict(manifest.collections) if manifest else None,
        "manifest": manifest is not None,
        "manifest_error": item.manifest_error,
        "kept_by": list(kept_by),
    }


_AFTER_RESTORE = (
    "next: rebuild the search indexes with `contextctl skills reindex`,",
    "then confirm the platform with `contextctl health`",
)


@asynccontextmanager
async def _service(settings: Settings, seconds: float) -> AsyncIterator[BackupService]:
    """Open a backup service, with a database connection when one is possible.

    The connection is optional. It is what lets a backup record how many
    documents it covers and lets `--restore` verification count what came back,
    but a database that is down is exactly when an operator most needs to list
    and check the backups they already have — so its absence narrows what can
    be done rather than preventing all of it.
    """
    tools = MongoTools(settings, timeout_seconds=seconds)
    mongodb = MongoDBClient(settings)
    connected = False
    try:
        await mongodb.connect(require_search=False)
        connected = True
    except (MongoDBNotReadyError, ValueError):
        connected = False
    try:
        repository = MongoBackupRepository(mongodb.database) if connected else None
        yield BackupService(settings, tools, repository=repository)
    finally:
        if connected:
            await mongodb.close()


async def _run(tools: MongoTools, tool: str, command: list[str]) -> None:
    """Run one MongoDB tool, reporting its failure the way the CLI does."""
    try:
        await tools.run(tool, command)
    except ToolNotFoundError as error:
        raise UnavailableError(str(error), hint=_MISSING_TOOL_HINT) from error
    except ToolFailedError as error:
        raise CliError(str(error)) from error
    except ValueError as error:
        raise CliError(str(error)) from error


def _size(output: Path) -> int:
    """Return the size of an archive, or of a dump tree."""
    if output.is_file():
        return output.stat().st_size
    return sum(item.stat().st_size for item in output.rglob("*") if item.is_file())


def _protect_output(output: Path) -> None:
    """Make an unmanaged archive or dump tree private before reporting success."""
    if output.is_file():
        output.chmod(0o600)
        return
    output.chmod(0o700)
    for path in output.rglob("*"):
        path.chmod(0o700 if path.is_dir() else 0o600)


def _timeout_seconds(arguments: argparse.Namespace, settings: Settings) -> float:
    """Let an explicit CLI timeout override the environment-driven default."""
    timeout = arguments.timeout
    return settings.backup_timeout_seconds if timeout is None else float(timeout)


def _timeout(parser: argparse.ArgumentParser) -> None:
    """Add the shared timeout option."""
    parser.add_argument(
        "--timeout",
        type=_positive_timeout,
        default=None,
        metavar="SECONDS",
        help="give up after this long (default: BACKUP_TIMEOUT_SECONDS)",
    )


def _positive_timeout(value: str) -> float:
    """Parse a finite positive tool timeout."""
    try:
        parsed = float(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("timeout must be a number") from error
    if not 0.0 < parsed <= 86_400.0:
        raise argparse.ArgumentTypeError("timeout must be greater than 0 and at most 86400")
    return parsed


def add_arguments(subparsers: SubParsers) -> None:
    """Register the backup, restore, and backups commands."""
    dumping = subparsers.add_parser(
        "backup",
        help="take one backup",
        description=(
            "Take one backup of the application database. With no --output it goes into the "
            "configured backup directory, records a manifest beside it, and applies the retention "
            "policy. Search indexes are not included: they are derived state rebuilt by "
            "`contextctl skills reindex`. The vectors are included, because regenerating them is "
            "expensive and they live in the documents."
        ),
        epilog=(
            "examples:\n"
            "  contextctl backup\n"
            "  contextctl backup --no-prune\n"
            "  contextctl backup --output ./before-migration.archive.gz"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    dumping.add_argument(
        "--output",
        type=Path,
        help="write here instead, unmanaged: no manifest and no retention",
    )
    dumping.add_argument(
        "--directory",
        action="store_true",
        help="with --output, write a dump tree instead of a single archive",
    )
    dumping.add_argument("--no-gzip", action="store_true", help="do not compress")
    dumping.add_argument(
        "--no-prune",
        action="store_true",
        help="keep every backup instead of applying the retention policy",
    )
    _timeout(dumping)
    dumping.set_defaults(handler=backup, needs_platform=False)

    restoring = subparsers.add_parser(
        "restore",
        help="restore the application database",
        description=(
            "Restore the application database. Stop the API first. Afterwards rebuild the search "
            "indexes and confirm readiness; a restore does not bring them back, because they are "
            "not backed up."
        ),
        epilog=(
            "examples:\n"
            "  contextctl restore --latest --drop --yes\n"
            "  contextctl restore --name context-20260914-131532.archive.gz --yes\n"
            "  contextctl restore --input ./before-migration.archive.gz --yes"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    source = restoring.add_mutually_exclusive_group(required=True)
    source.add_argument("--latest", action="store_true", help="restore the newest backup")
    source.add_argument("--name", metavar="BACKUP", help="restore this managed backup")
    source.add_argument("--input", type=Path, help="restore an archive or dump tree by path")
    restoring.add_argument(
        "--directory",
        action="store_true",
        help="with --input, read a dump tree instead of a single archive",
    )
    restoring.add_argument("--no-gzip", action="store_true", help="the backup is not compressed")
    restoring.add_argument(
        "--drop",
        action="store_true",
        help="drop each collection before restoring it, replacing rather than merging",
    )
    restoring.add_argument(
        "--yes",
        action="store_true",
        help="confirm the overwrite; required, and scriptable",
    )
    _timeout(restoring)
    restoring.set_defaults(handler=restore, needs_platform=False)

    managing = subparsers.add_parser(
        "backups",
        help="list, verify, and expire the backups you have",
        description="Inspect the set of managed backups.",
    )
    commands = managing.add_subparsers(dest="command", metavar="COMMAND", required=True)

    listing = commands.add_parser(
        "list",
        help="list the managed backups",
        description="List the backups in the configured directory, newest first.",
        epilog="example: contextctl backups list",
    )
    _timeout(listing)
    listing.set_defaults(handler=list_backups, needs_platform=False)

    checking = commands.add_parser(
        "verify",
        help="check that a backup is intact",
        description=(
            "Check a backup against the checksum recorded when it was written, and that "
            "mongorestore can open and plan it. With --restore it is restored into a throwaway "
            "database and its document counts compared with the manifest, which is the only check "
            "that proves a backup restores. Exits non-zero if any backup fails."
        ),
        epilog=(
            "examples:\n"
            "  contextctl backups verify                 # the newest, file checks only\n"
            "  contextctl backups verify --restore       # prove it actually restores\n"
            "  contextctl backups verify --all"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    check_target = checking.add_mutually_exclusive_group()
    check_target.add_argument(
        "--name", metavar="BACKUP", help="check this one instead of the newest"
    )
    check_target.add_argument("--all", action="store_true", help="check every backup")
    checking.add_argument(
        "--restore",
        action="store_true",
        help="restore into a throwaway database and compare the counts",
    )
    _timeout(checking)
    checking.set_defaults(handler=verify, needs_platform=False)

    expiring = commands.add_parser(
        "prune",
        help="apply the retention policy",
        description=(
            "Delete the backups the retention policy no longer keeps. The default keeps the "
            "newest backup of each of the last 7 days, 4 ISO weeks, and 3 months."
        ),
        epilog="example: contextctl backups prune --dry-run",
    )
    expiring.add_argument(
        "--dry-run",
        action="store_true",
        help="show what would be removed without removing it",
    )
    _timeout(expiring)
    expiring.set_defaults(handler=prune, needs_platform=False)
