"""What `contextctl` does when it runs: what it prints, and what it exits with.

`main` returns its exit code rather than raising, so a whole invocation runs in
-process and the codes themselves are assertable — the part a script depends on
and the part a subprocess test would hide behind its own plumbing.
"""

import json
import logging
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest

from app.cli import contextctl
from app.cli.errors import EXIT_FAILED, EXIT_OK, EXIT_UNAVAILABLE, EXIT_USAGE
from tests.mcp_doubles import (
    RecordedApi,
    memory_record,
    project_record,
    readiness_body,
    search_body,
    search_result,
    skill_record,
)

PROJECT = "payment-service"


@pytest.fixture(autouse=True)
def api_environment(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Point the CLI at a fixed address and hide any ambient configuration."""
    for name in (
        "CONTEXTCTL_API_TOKEN",
        "CONTEXTCTL_API_TOKEN_FILE",
        "CONTEXTCTL_REQUEST_TIMEOUT_SECONDS",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("CONTEXTCTL_API_BASE_URL", "http://context-api.invalid")
    yield


def serving(api: RecordedApi, monkeypatch: pytest.MonkeyPatch) -> None:
    """Make every API call this invocation issues reach the double."""
    transport = httpx.MockTransport(api)
    original = httpx.AsyncClient.__init__

    def patched(self: httpx.AsyncClient, *args: Any, **kwargs: Any) -> None:
        kwargs["transport"] = transport
        original(self, *args, **kwargs)

    monkeypatch.setattr(httpx.AsyncClient, "__init__", patched)


def run(
    api: RecordedApi,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    *argv: str,
) -> tuple[int, str, str]:
    """Run one invocation and return its exit code and streams."""
    serving(api, monkeypatch)
    code = contextctl.main(argv)
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def healthy_api() -> RecordedApi:
    """Build a double answering every read this module makes."""
    api = RecordedApi()
    api.respond("GET", "/health/ready", readiness_body())
    api.respond("GET", "/health/live", {"status": "alive"})
    api.respond("GET", "/api/v1/skills", {"items": [skill_record()], "next_cursor": None})
    api.respond(
        "GET", "/api/v1/projects", {"items": [project_record(PROJECT)], "next_cursor": None}
    )
    api.respond("POST", "/api/v1/projects", project_record(PROJECT), status_code=201)
    api.respond("POST", "/api/v1/search", search_body([search_result(project_id=PROJECT)]))
    api.respond(
        "POST",
        "/api/v1/memory/search",
        {"query": "q", "project_id": None, "results": [{"score": 0.9, "memory": memory_record()}]},
    )
    return api


def test_health_reports_a_ready_platform(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    code, out, err = run(healthy_api(), monkeypatch, capsys, "health")

    assert code == EXIT_OK
    assert "ready: yes" in out
    assert "MongoDB" in out
    assert err == ""


def test_health_exits_unavailable_when_the_platform_is_not_ready(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    api = healthy_api()
    api.respond("GET", "/health/ready", readiness_body(ready=False), status_code=503)

    code, _, err = run(api, monkeypatch, capsys, "health")

    # A deployment script gates on this: a readiness check that always
    # succeeds would be worse than none.
    assert code == EXIT_UNAVAILABLE
    assert "not ready" in err


def test_an_unreachable_platform_is_named_as_such(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    api = RecordedApi(error=httpx.ConnectError("[Errno 111] to 10.0.0.4:8000"))

    code, out, err = run(api, monkeypatch, capsys, "health")

    assert code == EXIT_UNAVAILABLE
    assert "unreachable" in err
    # The transport's own message names a host and port; ours does not.
    assert "10.0.0.4" not in err
    assert out == ""


def test_skills_list_prints_a_table(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    api = healthy_api()

    code, out, _ = run(api, monkeypatch, capsys, "skills", "list", "--limit", "3")

    assert code == EXIT_OK
    assert "SKILL" in out
    assert "fastapi-production" in out
    assert api.query() == {"status": "active", "limit": "3"}


def test_projects_create_sends_what_it_was_given(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    api = healthy_api()

    code, out, _ = run(
        api,
        monkeypatch,
        capsys,
        "projects",
        "create",
        "--project-id",
        PROJECT,
        "--name",
        "Payment Service",
        "--description",
        "Payments",
        "--repository",
        "git@example.invalid:d/p.git",
        "--environment",
        "production",
        "--tag",
        "payments",
    )

    assert code == EXIT_OK
    body = api.body()
    assert body["project_id"] == PROJECT
    assert body["tags"] == ["payments"]
    assert PROJECT in out


def test_project_binding_commands_use_the_authorized_api_and_reviewed_revision(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    api = healthy_api()
    api.respond(
        "POST",
        "/api/v4/project-bindings",
        {
            "binding_id": "payments-remote",
            "project_id": PROJECT,
            "binding_type": "git_remote",
            "value": "github.com/example/payment-service",
            "revision": 1,
            "created_at": "2026-10-06T00:00:00Z",
            "updated_at": "2026-10-06T00:00:00Z",
        },
        status_code=201,
    )
    api.respond("DELETE", "/api/v4/project-bindings/payments-remote", {}, status_code=204)

    created, _, _ = run(
        api,
        monkeypatch,
        capsys,
        "project",
        "binding",
        "add",
        "--project-id",
        PROJECT,
        "--type",
        "git_remote",
        "--value",
        "git@github.com:example/payment-service.git",
    )
    deleted, out, _ = run(
        api,
        monkeypatch,
        capsys,
        "project",
        "binding",
        "delete",
        "payments-remote",
        "--revision",
        "1",
    )

    assert created == EXIT_OK
    assert deleted == EXIT_OK
    assert api.query() == {"expected_revision": "1"}
    assert "deleted" in out


def test_search_reports_the_ranking_and_what_it_cost(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    api = healthy_api()

    code, out, _ = run(api, monkeypatch, capsys, "search", "refunds", "--project-id", PROJECT)

    assert code == EXIT_OK
    assert "SCORE" in out
    assert "0.9312" in out
    assert "tokens" in out
    assert api.body()["project_id"] == PROJECT


def test_search_can_print_the_content_it_found(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    api = healthy_api()

    _, quiet, _ = run(api, monkeypatch, capsys, "search", "refunds")
    _, verbose, _ = run(api, monkeypatch, capsys, "search", "refunds", "--content")

    body = "Payment requests carry a UUID4 idempotency key."
    assert body not in quiet
    assert body in verbose


def test_memory_search_prints_each_memory_with_its_scope(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    api = healthy_api()

    code, out, _ = run(api, monkeypatch, capsys, "memory", "search", "typing")

    assert code == EXIT_OK
    assert "SCOPE" in out
    assert "global" in out
    assert "Strict typing everywhere" in out


@pytest.mark.parametrize(
    "argv",
    [
        ("health",),
        ("skills", "list"),
        ("projects", "list"),
        ("search", "refunds"),
        ("memory", "search", "typing"),
    ],
)
def test_every_read_command_can_answer_as_json(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    argv: tuple[str, ...],
) -> None:
    code, out, err = run(healthy_api(), monkeypatch, capsys, "--json", *argv)

    assert code == EXIT_OK
    # Results on stdout and nothing else, so `| jq` works.
    assert isinstance(json.loads(out), dict)
    assert err == ""


def test_a_failure_is_json_too_when_json_was_asked_for(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    api = RecordedApi(error=httpx.ConnectError("refused"))

    code, out, err = run(api, monkeypatch, capsys, "--json", "health")

    reported = json.loads(err)
    assert reported["exit_code"] == EXIT_UNAVAILABLE
    assert "error" in reported
    assert code == EXIT_UNAVAILABLE
    # Failures never land in the pipe.
    assert out == ""


def test_a_refused_operation_is_a_failure_not_an_outage(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    api = healthy_api()
    api.respond("POST", "/api/v1/projects", {"detail": "project already exists"}, status_code=409)

    code, _, err = run(
        api,
        monkeypatch,
        capsys,
        "projects",
        "create",
        "--project-id",
        PROJECT,
        "--name",
        "n",
        "--description",
        "d",
        "--repository",
        "r",
        "--environment",
        "production",
    )

    assert code == EXIT_FAILED
    assert "already exists" in err


def test_a_rejected_argument_reports_what_the_platform_said(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    api = healthy_api()
    api.respond(
        "POST",
        "/api/v1/memory/search",
        {"detail": [{"msg": "Value error, searching project memory requires project_id"}]},
        status_code=422,
    )

    code, _, err = run(api, monkeypatch, capsys, "memory", "search", "q", "--scope", "project")

    assert code == EXIT_FAILED
    assert "requires project_id" in err


def test_a_platform_error_is_an_outage_not_a_command_failure(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    api = healthy_api()
    api.respond("POST", "/api/v1/search", {"detail": "search is unavailable"}, status_code=503)

    code, _, err = run(api, monkeypatch, capsys, "search", "refunds")

    assert code == EXIT_UNAVAILABLE
    assert "unavailable" in err or "cannot serve" in err


def test_an_unexpected_failure_is_never_printed_raw(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    async def explode(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("mongodb://user:pass@db/agent_context")

    monkeypatch.setattr(httpx.AsyncClient, "send", explode)

    with caplog.at_level(logging.ERROR, logger="agent_context.cli"):
        code, _, err = run(RecordedApi(), monkeypatch, capsys, "health")

    # The caller is told the command failed, not how: a traceback names paths,
    # hosts, and sometimes a connection string.
    assert code == EXIT_FAILED
    assert "unexpected" in err
    assert "mongodb://" not in err
    assert "Traceback" not in err
    # The operator still gets the whole thing, where it belongs.
    assert "mongodb://" in caplog.text


def test_an_unreadable_token_file_is_a_usage_problem(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("CONTEXTCTL_API_TOKEN_FILE", str(tmp_path / "absent"))

    code, _, err = run(healthy_api(), monkeypatch, capsys, "health")

    assert code == EXIT_FAILED
    assert "cannot be read" in err


@pytest.mark.parametrize(
    "argv",
    [
        ("keys", "list"),
        ("keys", "create", "--name", "n", "--scope", "search:read"),
        ("skills", "reindex"),
        ("backup", "--output", "./context.archive.gz"),
    ],
)
def test_a_missing_database_configuration_says_where_to_run_the_command(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
    argv: tuple[str, ...],
) -> None:
    for name in ("MONGODB_USERNAME", "MONGODB_PASSWORD", "MONGODB_PASSWORD_FILE"):
        monkeypatch.delenv(name, raising=False)
    # An empty env file, so the repository's own `.env` cannot supply them.
    monkeypatch.chdir(tmp_path)
    from app.core.config import get_settings

    get_settings.cache_clear()
    monkeypatch.setattr(contextctl, "get_settings", get_settings)

    code, _, err = run(healthy_api(), monkeypatch, capsys, *argv)

    # The raw validation error is a wall of Pydantic text that names no remedy.
    # What an operator needs is which variable is missing and where the command
    # belongs — under Compose the database has no host port, so the answer is
    # never "set these on your laptop".
    assert code == EXIT_USAGE
    assert "database settings" in err
    assert "MONGODB_USERNAME" in err
    assert "docker compose exec" in err
    assert "validation error" not in err
    assert "pydantic" not in err.lower()


def test_a_configured_token_reaches_the_api_and_is_never_printed(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    token_file = tmp_path / "token"
    token_file.write_text("secret-token-value\n", encoding="utf-8")
    monkeypatch.setenv("CONTEXTCTL_API_TOKEN_FILE", str(token_file))
    api = healthy_api()

    code, out, err = run(api, monkeypatch, capsys, "--json", "health")

    assert code == EXIT_OK
    assert api.last.headers["authorization"] == "Bearer secret-token-value"
    assert "secret-token-value" not in out
    assert "secret-token-value" not in err


def test_a_bad_api_address_is_reported_as_a_usage_problem(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    code, _, err = run(
        healthy_api(),
        monkeypatch,
        capsys,
        "--api-base-url",
        "mongodb://127.0.0.1:27017",
        "health",
    )

    assert code == EXIT_USAGE
    assert "http" in err


def test_an_empty_listing_says_so_rather_than_printing_nothing(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    api = healthy_api()
    api.respond("GET", "/api/v1/projects", {"items": [], "next_cursor": None})

    code, out, _ = run(api, monkeypatch, capsys, "projects", "list")

    assert code == EXIT_OK
    assert "(none)" in out


def test_a_further_page_is_offered_when_there_is_one(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    api = healthy_api()
    api.respond("GET", "/api/v1/skills", {"items": [skill_record()], "next_cursor": "abc123"})

    code, out, _ = run(api, monkeypatch, capsys, "skills", "list")

    assert code == EXIT_OK
    assert "--cursor abc123" in out
