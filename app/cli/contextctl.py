"""`contextctl` — the operator's interface to the platform.

Every command is a thin translation: parse arguments, call one service or one
API endpoint, print the result. Nothing here ranks, embeds, or decides what a
project may see; that all lives behind the API and the services, in one place.

There are two kinds of command, and the difference is visible in what they
need. Reading and searching ask the **Context API**, because that is what it is
for. Importing skills, rebuilding indexes, and backing up reach **MongoDB**
directly: no endpoint exposes them, and nothing but an operator should do them.
"""

import argparse
import asyncio
import logging
import sys
from collections.abc import Sequence
from typing import Any

from pydantic import ValidationError

from app.cli.commands import (
    agent,
    backup,
    candidate,
    claim,
    content,
    creator,
    health,
    identity,
    integration,
    keys,
    memory,
    note,
    organizations,
    policy,
    project,
    projects,
    research,
    search,
    skill,
    skills,
    source,
)
from app.cli.config import CliSettings
from app.cli.errors import EXIT_FAILED, EXIT_OK, EXIT_USAGE, CliError, UsageError
from app.cli.output import Result, render, render_error
from app.core.config import Settings, get_settings

PROGRAM = "contextctl"
DESCRIPTION = "Operate the self-hosted context, skills, and memory platform."
EPILOG = """\
examples:
  contextctl health
  contextctl skills list --limit 5
  contextctl search "how do we prevent duplicate payments?" --project-id payment-service
  contextctl --json memory search "typing conventions" | jq .results
  contextctl keys create --name codex --scope search:read --scope skills:read

exit codes:
  0  the command succeeded
  1  the command ran and did not succeed
  2  the arguments were wrong
  3  the platform could not be reached, or is not ready

configuration:
  CONTEXTCTL_API_BASE_URL        where the Context API listens (default http://127.0.0.1:8000)
  CONTEXTCTL_API_TOKEN_FILE      file holding a bearer token, when the API requires one
  CONTEXTCTL_REQUEST_TIMEOUT_SECONDS
  MONGODB_*                      only for import, reindex, keys, backup, and restore
"""
_UNEXPECTED = "the command failed for an unexpected reason"
_NOT_CONFIGURED = "this command needs the platform's database settings, and they are not set"

logger = logging.getLogger("agent_context.cli")


def build_parser() -> argparse.ArgumentParser:
    """Build the whole command tree."""
    parser = argparse.ArgumentParser(
        prog=PROGRAM,
        description=DESCRIPTION,
        epilog=EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--json",
        action="store_true",
        dest="as_json",
        help="print the result as JSON instead of a table",
    )
    parser.add_argument(
        "--api-base-url",
        metavar="URL",
        help="override CONTEXTCTL_API_BASE_URL for this command",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        metavar="SECONDS",
        dest="api_timeout",
        help="override CONTEXTCTL_REQUEST_TIMEOUT_SECONDS for this command",
    )
    parser.add_argument("--org-id", help="explicit organization for V6 operator work")
    groups = parser.add_subparsers(dest="group", metavar="COMMAND", required=True)
    for module in (
        agent,
        skills,
        skill,
        project,
        projects,
        research,
        source,
        note,
        claim,
        candidate,
        search,
        memory,
        creator,
        content,
        keys,
        health,
        identity,
        integration,
        backup,
        organizations,
        policy,
    ):
        module.add_arguments(groups)
    return parser


def _cli_settings(arguments: argparse.Namespace) -> CliSettings:
    """Read the API settings, letting the command line win over the environment."""
    overrides: dict[str, Any] = {}
    if arguments.api_base_url is not None:
        overrides["api_base_url"] = arguments.api_base_url
    if arguments.api_timeout is not None:
        overrides["request_timeout_seconds"] = arguments.api_timeout
    return CliSettings(**overrides)


def _settings(arguments: argparse.Namespace) -> CliSettings | Settings:
    """Read whichever configuration this command actually needs.

    A search must not fail because MongoDB credentials are absent from the
    shell, and an import must not fail because an API address is not set.
    """
    if arguments.needs_platform:
        return _cli_settings(arguments)
    try:
        return get_settings()
    except ValidationError as error:
        raise UsageError(_NOT_CONFIGURED, hint=_where_to_run(error)) from error


def _where_to_run(error: ValidationError) -> str:
    """Explain a missing database configuration in terms of what to do.

    This is worth spelling out because the failure is confusing on its own: a
    committed `.env` is written for Compose, which maps `MONGODB_APP_USERNAME`
    to the `MONGODB_USERNAME` the application reads — so the file that looks
    like it configures everything configures a different set of names, and the
    command appears broken rather than run in the wrong place.
    """
    missing = sorted({str(item["loc"][0]).upper() for item in error.errors() if item["loc"]})
    if missing:
        named = f"{', '.join(missing)} must be set"
    else:
        # A model-level failure has no field to name, so its own message is the
        # only thing that says what is wrong — and those are written to be read.
        named = "; ".join(_reason(item["msg"]) for item in error.errors())
    return (
        f"{named}. These commands reach MongoDB directly, and under Compose it is on an "
        "internal network with no host port — so run one inside the platform: "
        "docker compose exec context-api python -m app.cli.contextctl …"
    )


def _reason(message: str) -> str:
    """Return a validator's message without Pydantic's own prefix."""
    return message.removeprefix("Value error, ")


async def _dispatch(arguments: argparse.Namespace) -> Result:
    """Run one command."""
    handler = arguments.handler
    from app.security.tenant import TenantContext, tenant_scope

    if arguments.org_id is not None and arguments.group == "backup":
        raise UsageError(
            "backup/restore is installation-wide; --org-id is not a tenant restore selector"
        )
    if arguments.org_id is not None:
        with tenant_scope(TenantContext(arguments.org_id)):
            result: Result = await handler(arguments, _settings(arguments))
            return result
    result = await handler(arguments, _settings(arguments))
    return result


def main(argv: Sequence[str] | None = None) -> int:
    """Run one `contextctl` invocation and return its exit code.

    The exit code is returned rather than raised so the whole tool can be
    driven from a test without a subprocess, which is what makes the exit
    codes themselves testable.
    """
    parser = build_parser()
    try:
        arguments = parser.parse_args(argv)
    except SystemExit as exit_request:
        # argparse exits 2 for a usage error and 0 for --help; both are already
        # the right answer, and both have already printed.
        return int(exit_request.code or EXIT_OK)

    as_json = bool(arguments.as_json)
    try:
        result = asyncio.run(_dispatch(arguments))
    except CliError as error:
        render_error(
            str(error),
            as_json=as_json,
            exit_code=error.exit_code,
            hint=error.hint,
        )
        return error.exit_code
    except ValueError as error:
        # Configuration and argument values the models refuse, reported as the
        # usage problems they are.
        render_error(str(error), as_json=as_json, exit_code=EXIT_USAGE)
        return EXIT_USAGE
    except KeyboardInterrupt:  # pragma: no cover - interactive interruption
        render_error("interrupted", as_json=as_json, exit_code=EXIT_FAILED)
        return EXIT_FAILED
    except Exception:
        # Never shown verbatim: an unexpected traceback names paths, hosts, and
        # sometimes a connection string. It goes to the log, not to the user.
        logger.exception("cli_command_failed", extra={"cli_command": arguments.group})
        render_error(_UNEXPECTED, as_json=as_json, exit_code=EXIT_FAILED)
        return EXIT_FAILED

    render(result, as_json=as_json)
    if arguments.group == "organizations" and result.data.get("compatible") is False:
        return EXIT_FAILED
    return EXIT_OK


def run() -> None:
    """Console-script entry point."""
    sys.exit(main())


if __name__ == "__main__":
    run()
