"""What `contextctl` accepts, and what it refuses before doing anything.

Parsing is the half of a CLI that has to be right before anything else can be:
a mistyped flag that silently becomes a default is worse than one that fails.
"""

import argparse
from pathlib import Path
from typing import Any

import pytest

from app.cli.contextctl import DESCRIPTION, EPILOG, PROGRAM, build_parser
from app.cli.errors import EXIT_CODES, EXIT_FAILED, EXIT_OK, EXIT_UNAVAILABLE, EXIT_USAGE

# Every command the CLI is required to expose, as a caller types it.
REQUIRED_COMMANDS = (
    ("skills", "list"),
    ("skills", "import"),
    ("skills", "reindex"),
    ("projects", "create"),
    ("projects", "list"),
    ("project", "binding", "add"),
    ("project", "binding", "list"),
    ("project", "binding", "delete"),
    ("search",),
    ("memory", "search"),
    ("keys", "create"),
    ("keys", "list"),
    ("keys", "revoke"),
    ("keys", "scopes"),
    ("health",),
    ("backup",),
    ("restore",),
    ("backups", "list"),
    ("backups", "verify"),
    ("backups", "prune"),
)
# Commands that ask the API rather than reaching MongoDB themselves. `keys
# scopes` is here because it needs neither: it describes the platform's own
# vocabulary, and the API settings are the ones that are always satisfiable.
API_COMMANDS = {
    ("skills", "list"),
    ("projects", "create"),
    ("projects", "list"),
    ("project", "binding", "add"),
    ("project", "binding", "list"),
    ("project", "binding", "delete"),
    ("search",),
    ("memory", "search"),
    ("keys", "scopes"),
    ("health",),
}
_CREATE = (
    "--name",
    "n",
    "--description",
    "d",
    "--repository",
    "r",
    "--environment",
    "production",
)


def parse(*argv: str) -> argparse.Namespace:
    """Parse one command line."""
    return build_parser().parse_args(argv)


def usage(*argv: str) -> None:
    """Parse one command line that should be refused."""
    with pytest.raises(SystemExit) as exit_request:
        build_parser().parse_args(argv)
    assert exit_request.value.code == EXIT_USAGE


@pytest.mark.parametrize(
    "argv",
    [
        ("restore", "--latest", "--name", "one.archive.gz"),
        ("restore", "--yes"),
        ("backups", "verify", "--all", "--name", "one.archive.gz"),
        ("backup", "--timeout", "0"),
        ("backup", "--timeout", "86401"),
    ],
)
def test_conflicting_or_unsafe_backup_arguments_are_refused(argv: tuple[str, ...]) -> None:
    usage(*argv)


def test_the_parser_describes_the_tool_and_its_exit_codes() -> None:
    parser = build_parser()

    assert parser.prog == PROGRAM
    assert parser.description == DESCRIPTION
    # A caller scripting against this needs the codes without reading source.
    for code in (EXIT_OK, EXIT_FAILED, EXIT_USAGE, EXIT_UNAVAILABLE):
        assert f"  {code}  " in EPILOG
    assert sorted(EXIT_CODES) == [EXIT_OK, EXIT_FAILED, EXIT_USAGE, EXIT_UNAVAILABLE]


@pytest.mark.parametrize("command", REQUIRED_COMMANDS)
def test_every_required_command_parses(command: tuple[str, ...]) -> None:
    extra: dict[tuple[str, ...], tuple[str, ...]] = {
        ("search",): ("a question",),
        ("memory", "search"): ("a question",),
        ("projects", "create"): ("--project-id", "p", *_CREATE),
        ("project", "binding", "add"): (
            "--project-id",
            "p",
            "--type",
            "git_remote",
            "--value",
            "git@example.test:payments.git",
        ),
        ("project", "binding", "delete"): ("binding-1", "--revision", "1"),
        ("keys", "create"): ("--name", "n", "--scope", "search:read"),
        ("keys", "revoke"): ("--key-id", "0123456789abcdef"),
        ("backup",): ("--output", "./context.archive.gz"),
        ("restore",): ("--input", "./context.archive.gz"),
    }
    arguments = parse(*command, *extra.get(command, ()))

    assert callable(arguments.handler)
    assert isinstance(arguments.needs_platform, bool)


@pytest.mark.parametrize("command", REQUIRED_COMMANDS)
def test_each_command_knows_whether_it_needs_the_api(command: tuple[str, ...]) -> None:
    extra: dict[tuple[str, ...], tuple[str, ...]] = {
        ("search",): ("q",),
        ("memory", "search"): ("q",),
        ("projects", "create"): ("--project-id", "p", *_CREATE),
        ("project", "binding", "add"): (
            "--project-id",
            "p",
            "--type",
            "git_remote",
            "--value",
            "git@example.test:payments.git",
        ),
        ("project", "binding", "delete"): ("binding-1", "--revision", "1"),
        ("keys", "create"): ("--name", "n", "--scope", "search:read"),
        ("keys", "revoke"): ("--key-id", "0123456789abcdef"),
        ("backup",): ("--output", "./context.archive.gz"),
        ("restore",): ("--input", "./context.archive.gz"),
    }
    arguments = parse(*command, *extra.get(command, ()))

    # A search must not fail for want of MongoDB credentials, and an import
    # must not fail for want of an API address.
    assert arguments.needs_platform is (command in API_COMMANDS)


@pytest.mark.parametrize("command", REQUIRED_COMMANDS)
def test_every_command_has_help_text(command: tuple[str, ...]) -> None:
    with pytest.raises(SystemExit) as exit_request:
        build_parser().parse_args([*command, "--help"])

    assert exit_request.value.code == EXIT_OK


def test_the_help_carries_worked_examples() -> None:
    assert "examples:" in EPILOG
    assert "contextctl search" in EPILOG
    assert "CONTEXTCTL_API_BASE_URL" in EPILOG


@pytest.mark.parametrize(
    "argv",
    [
        (),
        ("nonsense",),
        ("skills",),
        ("skills", "nonsense"),
        ("projects",),
        ("project",),
        ("project", "binding"),
        ("project", "binding", "add"),
        ("project", "binding", "delete", "binding-1"),
        ("memory",),
        ("search",),
        ("memory", "search"),
        ("projects", "create"),
        ("projects", "create", "--project-id", "p"),
        ("search", "q", "--source", "nonsense"),
        ("search", "q", "--limit", "not-a-number"),
        ("memory", "search", "q", "--status", "nonsense"),
        ("memory", "search", "q", "--scope", "nonsense"),
        ("keys",),
        ("keys", "create"),
        ("keys", "create", "--name", "n"),
        ("keys", "create", "--name", "n", "--scope", "search:write"),
        ("keys", "create", "--name", "n", "--scope", "SEARCH:READ"),
        ("keys", "revoke"),
        ("keys", "list", "--status", "nonsense"),
        ("backups",),
        ("backups", "nonsense"),
        ("backup", "--unknown-flag"),
        ("backups", "verify", "--unknown-flag"),
        ("health", "--unknown-flag"),
        ("search", "q", "--unknown-flag"),
    ],
)
def test_a_command_line_that_cannot_be_honoured_is_refused(argv: tuple[str, ...]) -> None:
    usage(*argv)


def test_a_bare_backup_is_the_managed_one() -> None:
    # `contextctl backup` with nothing else is the normal case: into the
    # configured directory, with a manifest, applying retention.
    arguments = parse("backup")

    assert arguments.output is None
    assert arguments.no_prune is False
    assert arguments.no_gzip is False


def test_a_restore_names_its_source_one_of_three_ways() -> None:
    # Exactly one source is required and conflicts are refused before a
    # handler can reach the database.
    assert parse("restore", "--latest").latest is True
    assert parse("restore", "--name", "context-20260914-131532.archive.gz").name is not None
    assert parse("restore", "--input", "./context.archive.gz").input is not None
    usage("restore")


def test_repeatable_filters_collect() -> None:
    arguments = parse(
        "search",
        "q",
        "--source",
        "memory",
        "--source",
        "active_skill",
        "--tag",
        "payments",
        "--tag",
        "refunds",
    )

    assert arguments.source == ["memory", "active_skill"]
    assert arguments.tag == ["payments", "refunds"]


def test_json_and_the_api_address_are_global_not_per_command() -> None:
    # `contextctl --json search …`, not `contextctl search --json`: one place
    # to set them, whichever command follows.
    arguments = parse("--json", "--api-base-url", "http://api.invalid", "health")

    assert arguments.as_json is True
    assert arguments.api_base_url == "http://api.invalid"
    usage("search", "q", "--json")


def test_paths_are_parsed_as_paths() -> None:
    arguments = parse("backup", "--output", "~/backups/context.archive.gz")

    assert isinstance(arguments.output, Path)


def test_defaults_are_the_documented_ones() -> None:
    search = parse("search", "q")
    listing = parse("skills", "list")
    memory = parse("memory", "search", "q")

    assert search.limit == 10
    assert search.minimum_importance == 0.0
    assert search.token_budget is None
    assert listing.limit == 20
    assert listing.status == "active"
    assert memory.status == "active"


def test_a_restore_is_not_something_that_happens_by_accident() -> None:
    arguments = parse("restore", "--input", "./context.archive.gz")

    # Scriptable, but never implicit: nothing overwrites a database because a
    # command was typed with one word missing.
    assert arguments.yes is False
    assert arguments.drop is False


def test_a_key_grant_collects_scopes_and_projects() -> None:
    arguments = parse(
        "keys",
        "create",
        "--name",
        "agent",
        "--scope",
        "search:read",
        "--scope",
        "context:read",
        "--project",
        "payment-service",
        "--project",
        "wallet-service",
    )

    assert arguments.scope == ["search:read", "context:read"]
    assert arguments.project == ["payment-service", "wallet-service"]
    # Omitted rather than defaulted: no expiry is a decision, and every project
    # is what an empty project list means.
    assert arguments.expires_in_days is None


def test_agent_commands_expose_client_and_one_time_token_workflows() -> None:
    creating = parse(
        "agent",
        "create",
        "--client-id",
        "codex-main",
        "--type",
        "codex",
        "--name",
        "Codex",
        "--owner-id",
        "operator",
        "--scope",
        "search:read",
        "--project",
        "payments",
    )
    token = parse("agent", "token", "create", "codex-main", "--expires-in-days", "30")

    assert callable(creating.handler)
    assert creating.scope == ["search:read"]
    assert creating.project == ["payments"]
    assert callable(token.handler)
    assert token.handler.__name__ == "token_create"


def test_agent_activity_command_accepts_bounded_content_free_filters() -> None:
    arguments = parse(
        "agent",
        "activity",
        "--client-id",
        "claude-code-main",
        "--project-id",
        "payment-service",
        "--tool",
        "save_context",
        "--status",
        "succeeded",
        "--limit",
        "10",
        "--cursor",
        "cursor-1",
    )

    assert callable(arguments.handler)
    assert arguments.handler.__name__ == "activity"
    assert arguments.client_id == "claude-code-main"
    assert arguments.project_id == "payment-service"
    assert arguments.tool == "save_context"
    assert arguments.status == "succeeded"
    assert arguments.limit == 10
    assert arguments.cursor == "cursor-1"


def test_no_command_accepts_a_secret_on_the_command_line() -> None:
    # A password or token in argv is readable by every process on the host and
    # ends up in shell history. They come from the environment or a file.
    # `keys create` prints one rather than taking one, which is the same rule.
    forbidden = ("--password", "--api-token", "--token", "--secret", "--key")
    parser = build_parser()
    actions: list[Any] = list(parser._actions)
    for action in actions:
        if hasattr(action, "choices") and isinstance(action.choices, dict):
            for command in action.choices.values():
                actions.extend(command._actions)
    flags = {option for action in actions for option in getattr(action, "option_strings", [])}

    assert flags.isdisjoint(forbidden)
