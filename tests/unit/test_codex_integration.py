"""The shipped Codex configuration and the example project.

Neither is code Codex runs, but both break silently: a renamed entry point, a
renamed tool, or a context type that no longer exists would leave a working
platform that an agent cannot reach, or an example that will not load.
"""

import json
import stat
import tomllib
from pathlib import Path
from typing import Any

import pytest

from app.mcp.tools import TOOLS_BY_NAME
from app.models.project_context import ContextType
from app.schemas.context import ContextCreateRequest
from app.schemas.memory import MemoryCreateRequest
from app.schemas.project import ProjectCreateRequest

REPOSITORY = Path(__file__).resolve().parents[2]
CODEX_CONFIG = REPOSITORY / "examples/clients/codex-config.toml"
LAUNCHER = REPOSITORY / "scripts/codex-mcp-server.sh"
EXAMPLE = REPOSITORY / "examples/payment-service/knowledge.json"
SERVER_NAME = "agent-context"


@pytest.fixture(scope="module")
def codex_config() -> dict[str, Any]:
    """Parse the committed Codex configuration."""
    return tomllib.loads(CODEX_CONFIG.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def server(codex_config: dict[str, Any]) -> dict[str, Any]:
    """Return this repository's MCP server entry."""
    entry = codex_config["mcp_servers"][SERVER_NAME]
    assert isinstance(entry, dict)
    return entry


@pytest.fixture(scope="module")
def example() -> dict[str, Any]:
    """Parse the example project's knowledge."""
    return json.loads(EXAMPLE.read_text(encoding="utf-8"))


def test_the_configuration_uses_the_current_codex_table_name(
    codex_config: dict[str, Any],
) -> None:
    # Codex reads `mcp_servers`, in snake_case. `mcpServers` is the shape other
    # clients use and is silently ignored here, which looks like a server that
    # simply never starts.
    assert list(codex_config) == ["mcp_servers"]
    assert SERVER_NAME in codex_config["mcp_servers"]


def test_the_configuration_selects_the_stdio_transport(server: dict[str, Any]) -> None:
    # `command` is what makes it stdio; `url` would make it streamable HTTP,
    # which this server deliberately does not serve.
    assert "command" in server
    assert "url" not in server


def test_the_configured_command_exists_and_is_executable(server: dict[str, Any]) -> None:
    command = server["command"]
    assert command.startswith("./"), "a project-scoped command is relative to the project root"
    launcher = REPOSITORY / command[2:]

    assert launcher == LAUNCHER
    assert launcher.is_file()
    assert launcher.stat().st_mode & stat.S_IXUSR, "Codex cannot spawn a file it cannot execute"


def test_the_launcher_starts_the_documented_entry_point() -> None:
    body = LAUNCHER.read_text(encoding="utf-8")

    assert "app.run_mcp" in body
    assert (REPOSITORY / "app/run_mcp.py").is_file()


def test_the_server_is_told_where_the_api_is(server: dict[str, Any]) -> None:
    environment = server["env"]

    # It is the one thing the MCP process cannot start usefully without, and
    # the only address it needs: no database credentials appear here.
    assert environment["MCP_API_BASE_URL"].startswith("http")
    assert not any(name.startswith("MONGODB") for name in environment)
    assert not any("PASSWORD" in name or "TOKEN" in name for name in environment)


def test_the_timeouts_leave_room_for_the_documented_launchers(server: dict[str, Any]) -> None:
    # The direct launcher starts in well under a second; the containerised one
    # has to create a container first.
    assert server["startup_timeout_sec"] >= 10
    assert server["tool_timeout_sec"] >= 60


def test_a_server_that_cannot_start_fails_the_session(server: dict[str, Any]) -> None:
    # Answering without project knowledge, silently, is the outcome worth
    # preventing: a session that cannot reach the platform should say so.
    assert server["required"] is True


def test_no_tool_is_disabled_by_the_shipped_configuration(server: dict[str, Any]) -> None:
    # Every tool the server registers should reach Codex. A deny list here
    # would hide capability the documentation promises.
    assert "disabled_tools" not in server
    for key in ("enabled_tools", "tools"):
        assert key not in server


def test_the_example_project_is_the_documented_one(example: dict[str, Any]) -> None:
    project = ProjectCreateRequest.model_validate(example["project"])

    assert project.project_id == "payment-service"


def test_every_example_context_item_is_one_the_api_accepts(example: dict[str, Any]) -> None:
    project_id = example["project"]["project_id"]

    for item in example["context"]:
        request = ContextCreateRequest.model_validate(
            {"project_id": project_id, "source": "example", **item}
        )
        assert request.external_id, "an example must be reloadable without duplicating itself"


def test_every_example_memory_is_one_the_api_accepts(example: dict[str, Any]) -> None:
    for item in example["memory"]:
        request = MemoryCreateRequest.model_validate({"source": "example", **item})
        assert request.external_id


def test_the_example_covers_the_knowledge_the_refund_request_needs(
    example: dict[str, Any],
) -> None:
    titles = " ".join(item["title"] for item in example["context"]).casefold()
    types = {item["type"] for item in example["context"]}

    # The documented walkthrough asks Codex to implement refunds, so the
    # example has to hold the six kinds of knowledge that answer it.
    for subject in ("architecture", "keycloak", "provider", "wallet", "ledger", "idempotency"):
        assert subject in titles, f"the example says nothing about {subject}"
    assert types <= set(ContextType.__args__)  # type: ignore[attr-defined]


def test_the_example_content_carries_the_identifiers_lexical_search_needs(
    example: dict[str, Any],
) -> None:
    body = " ".join(item["content"] for item in example["context"])

    # Retrieval is hybrid, so the example is written to exercise both halves:
    # prose for the vector side, exact technical vocabulary for the lexical one.
    for identifier in (
        "INSUFFICIENT_SCOPE",
        "payments:refund",
        "wallet:write",
        "Idempotency-Key",
        "refund.succeeded",
        "/api/v1/payments",
    ):
        assert identifier in body, f"{identifier} appears nowhere in the example"


def test_the_example_loader_only_calls_tools_that_exist() -> None:
    loader = (REPOSITORY / "examples/payment-service/load.py").read_text(encoding="utf-8")

    for tool in ("save_context", "save_memory"):
        assert f'"{tool}"' in loader
        assert tool in TOOLS_BY_NAME


def test_the_test_image_contains_every_file_this_module_reads() -> None:
    # These tests read the repository, and the test image copies a subset of
    # it. A file this module asserts about but the image does not carry passes
    # here and fails inside the container, which is the worst place to find it.
    stage = (REPOSITORY / "Dockerfile").read_text(encoding="utf-8").split("FROM runtime AS test")[1]
    copied = stage.split("FROM ")[0]

    for path in ("examples/clients/codex-config.toml", "examples", "scripts/codex-mcp-server.sh"):
        assert f"COPY {path}" in copied or f" {path} " in copied, (
            f"the test image does not copy {path}, which this module reads"
        )
