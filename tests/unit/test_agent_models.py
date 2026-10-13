"""V4 Phase 0 agent-identity model contracts."""

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from app.models.agent import (
    AgentActivityDocument,
    AgentClientDocument,
    AgentSessionDocument,
    AgentSessionStatus,
    ProjectBindingDocument,
)


def test_agent_client_identity_is_deterministic_and_does_not_hold_a_secret() -> None:
    values = {
        "client_id": "claude-code-main",
        "client_type": "claude_code",
        "name": "Claude Code Workstation",
        "owner_id": "owner",
        "scopes": ["skills:read", "context:read"],
        "project_ids": ["payments-api"],
    }

    first = AgentClientDocument(**values)
    second = AgentClientDocument(**values)

    assert first.id == second.id
    assert "token" not in first.model_dump()
    assert "key_hash" not in first.model_dump()
    assert first.last_seen_at is None


@pytest.mark.parametrize("status", ["active", "disabled", "revoked"])
def test_agent_client_supports_the_phase_zero_lifecycle(status: str) -> None:
    client = AgentClientDocument(
        client_id=f"client-{status}",
        client_type="codex",
        name="Codex",
        status=status,
        owner_id="owner",
    )

    assert client.status == status


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("scopes", ["skills:read", "skills:read"], "scopes must be unique"),
        ("scopes", ["future:scope"], "unknown scopes"),
        ("project_ids", ["payments", "payments"], "project_ids must be unique"),
    ],
)
def test_agent_client_rejects_ambiguous_or_future_authorization_metadata(
    field: str, value: list[str], message: str
) -> None:
    values: dict[str, object] = {
        "client_id": "codex-main",
        "client_type": "codex",
        "name": "Codex",
        "owner_id": "owner",
    }
    values[field] = value

    with pytest.raises(ValidationError, match=message):
        AgentClientDocument(**values)


def test_session_is_lightweight_and_cannot_precede_its_start() -> None:
    started_at = datetime(2026, 10, 5, 9, 0, tzinfo=UTC)
    session = AgentSessionDocument(
        session_id="claude-work-1",
        client_id="claude-code-main",
        client_type="claude_code",
        project_id="payments-api",
        repository="git@github.com:example/payments-api.git",
        started_at=started_at,
        last_seen_at=started_at + timedelta(minutes=1),
    )

    assert "content" not in session.model_dump()
    with pytest.raises(ValidationError, match="last_seen_at"):
        AgentSessionDocument(
            session_id="invalid-session",
            client_id="claude-code-main",
            client_type="claude_code",
            started_at=started_at,
            last_seen_at=started_at - timedelta(seconds=1),
        )

    ended = AgentSessionDocument(
        session_id="ended-session",
        client_id="claude-code-main",
        client_type="claude_code",
        started_at=started_at,
        last_seen_at=started_at + timedelta(minutes=1),
        ended_at=started_at + timedelta(minutes=2),
        status="ended",
    )
    assert ended.ended_at is not None
    expired = AgentSessionDocument(
        session_id="expired-session",
        client_id="claude-code-main",
        client_type="claude_code",
        started_at=started_at,
        last_seen_at=started_at + timedelta(minutes=1),
        ended_at=started_at + timedelta(minutes=2),
        status=AgentSessionStatus.EXPIRED,
    )
    assert expired.status is AgentSessionStatus.EXPIRED
    with pytest.raises(ValidationError, match="active session"):
        AgentSessionDocument(
            session_id="impossible-session",
            client_id="claude-code-main",
            client_type="claude_code",
            started_at=started_at,
            last_seen_at=started_at,
            ended_at=started_at,
        )


@pytest.mark.parametrize("metadata", [{"transcript": "private"}, {"labels": ["nested"]}])
def test_session_metadata_remains_operational_and_never_becomes_a_conversation_store(
    metadata: dict[str, object],
) -> None:
    with pytest.raises(ValidationError, match="session metadata"):
        AgentSessionDocument(
            session_id="metadata-session",
            client_id="claude-code-main",
            client_type="claude_code",
            started_at=datetime(2026, 10, 5, 9, 0, tzinfo=UTC),
            last_seen_at=datetime(2026, 10, 5, 9, 0, tzinfo=UTC),
            metadata=metadata,
        )


def test_project_binding_has_a_stable_natural_identity() -> None:
    values = {
        "binding_id": "payments-origin",
        "project_id": "payments-api",
        "binding_type": "git_remote",
        "value": "git@github.com:example/payments-api.git",
    }

    assert ProjectBindingDocument(**values).id == ProjectBindingDocument(**values).id


def test_project_binding_serializes_a_credential_free_canonical_repository_locator() -> None:
    binding = ProjectBindingDocument(
        binding_id="payments-origin",
        project_id="payments-api",
        binding_type="repository_url",
        value="https://github.com:443/example/payments-api.git",
    )

    assert binding.value == "github.com/example/payments-api"
    serialized = binding.model_dump(mode="json")
    assert serialized["value"] == "github.com/example/payments-api"
    assert "token" not in serialized


def test_activity_is_content_free_and_has_a_bounded_operational_outcome() -> None:
    activity = AgentActivityDocument(
        event_id="request-42",
        client_id="codex-main",
        client_type="codex",
        session_id="codex-session-1",
        operation="tool_call",
        tool="search_context",
        project_id="payments-api",
        resource_type="project_context",
        resource_id="retry-design",
        status="succeeded",
        latency_ms=12.5,
    )

    assert "content" not in activity.model_dump()
    with pytest.raises(ValidationError):
        AgentActivityDocument(
            event_id="request-43",
            client_id="codex-main",
            client_type="codex",
            operation="tool_call",
            status="unknown",
        )


def test_agent_documents_serialize_as_content_free_json_contracts() -> None:
    activity = AgentActivityDocument(
        event_id="serialized-event",
        client_id="codex-main",
        client_type="codex",
        operation="tool_call",
        status="denied",
    )

    serialized = activity.model_dump(mode="json")
    assert serialized["client_type"] == "codex"
    assert serialized["status"] == "denied"
    assert "content" not in serialized
    assert "token" not in serialized
