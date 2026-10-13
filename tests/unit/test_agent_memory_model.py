"""Validation and stable identifiers for V4's extension of V1 memory."""

import pytest
from pydantic import ValidationError

from tests.memory_doubles import memory

PROJECT = "payment-service"
CLAUDE = "claude-code-main"


def test_legacy_global_and_project_memories_remain_valid_shared_records() -> None:
    global_memory = memory(external_id="platform-typing")
    project_memory = memory(PROJECT, external_id="payment-retries")

    assert global_memory.scope == "global"
    assert global_memory.client_id is None
    assert project_memory.scope == "project"
    assert project_memory.client_id is None


def test_agent_memory_requires_exactly_the_owners_its_scope_promises() -> None:
    agent = memory(scope="agent", client_id=CLAUDE, external_id="worktree")
    project_agent = memory(
        PROJECT,
        scope="project_agent",
        client_id=CLAUDE,
        external_id="checkpoint",
    )

    assert agent.project_id is None
    assert agent.client_id == CLAUDE
    assert project_agent.project_id == PROJECT
    assert project_agent.client_id == CLAUDE

    with pytest.raises(ValidationError, match="agent memory has an invalid client_id"):
        memory(scope="agent")
    with pytest.raises(ValidationError, match="project_agent memory has an invalid project_id"):
        memory(scope="project_agent", client_id=CLAUDE)
    with pytest.raises(ValidationError, match="global memory has an invalid client_id"):
        memory(scope="global", client_id=CLAUDE)


def test_agent_external_ids_are_deterministic_per_client_without_changing_v1_shapes() -> None:
    legacy = memory(external_id="same-name")
    claude = memory(scope="agent", client_id=CLAUDE, external_id="same-name")
    codex = memory(scope="agent", client_id="codex-main", external_id="same-name")

    assert claude.id != codex.id
    assert legacy.id != claude.id
