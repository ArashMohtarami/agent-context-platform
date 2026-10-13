"""V3 Phase 9 candidate CLI tests."""

from typing import Any

import httpx
import pytest

from app.cli import contextctl
from app.cli.errors import EXIT_OK
from tests.mcp_doubles import RecordedApi

RESEARCH = "retry-evidence"


def candidate(status: str = "approved") -> dict[str, Any]:
    return {
        "research_project_id": RESEARCH,
        "candidate_id": "retry-context",
        "candidate_type": "project_context",
        "target_project_id": "payments-api",
        "title": "Retry idempotency requirement",
        "content": "Use an idempotency key.",
        "source_claim_ids": ["retry-key"],
        "source_citation_ids": ["provider-support"],
        "confidence": 0.9,
        "confidence_rationale": None,
        "status": status,
        "reviewer": "reviewer-key",
        "reviewed_at": "2026-10-01T12:00:00Z",
        "review_notes": None,
        "authorization": {"project_ids": ["payments-api"], "owner_key_id": "writer-key"},
        "revision": 2,
        "created_at": "2026-10-01T00:00:00Z",
        "updated_at": "2026-10-01T12:00:00Z",
    }


def event(decision: str) -> dict[str, Any]:
    return {
        "research_project_id": RESEARCH,
        "candidate_id": "retry-context",
        "event_id": "review-event",
        "target_type": "project_context",
        "target_id": "payments-api",
        "reviewer": "reviewer-key",
        "decision": decision,
        "decided_at": "2026-10-01T12:00:00Z",
        "reason": None,
        "authorization": {"project_ids": ["payments-api"], "owner_key_id": "reviewer-key"},
        "created_at": "2026-10-01T12:00:00Z",
    }


def serving(api: RecordedApi, monkeypatch: pytest.MonkeyPatch) -> None:
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
) -> tuple[int, str]:
    monkeypatch.setenv("CONTEXTCTL_API_BASE_URL", "http://context-api.invalid")
    monkeypatch.delenv("CONTEXTCTL_API_TOKEN", raising=False)
    monkeypatch.delenv("CONTEXTCTL_API_TOKEN_FILE", raising=False)
    serving(api, monkeypatch)
    code = contextctl.main(argv)
    return code, capsys.readouterr().out


def test_candidate_cli_lists_and_approves_without_promotion(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    api = RecordedApi()
    api.respond(
        "GET",
        "/api/v3/knowledge-candidates",
        {"items": [candidate("pending_review")], "next_cursor": None},
    )
    api.respond(
        "POST",
        "/api/v3/knowledge-candidates/retry-context/approve",
        {"candidate": candidate(), "event": event("approved")},
    )

    listed_code, listed_output = run(
        api,
        monkeypatch,
        capsys,
        "candidate",
        "list",
        "--research-project-id",
        RESEARCH,
        "--status",
        "pending_review",
    )
    approve_code, approve_output = run(
        api,
        monkeypatch,
        capsys,
        "candidate",
        "approve",
        "--research-project-id",
        RESEARCH,
        "--candidate-id",
        "retry-context",
        "--expected-revision",
        "1",
    )

    assert listed_code == EXIT_OK
    assert "retry-context" in listed_output
    assert approve_code == EXIT_OK
    assert "promoted            no" in approve_output
    assert api.query(0)["status"] == "pending_review"
    assert api.body(1) == {"research_project_id": RESEARCH, "expected_revision": 1, "reason": None}


def test_candidate_cli_can_request_changes(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    api = RecordedApi()
    api.respond(
        "POST",
        "/api/v3/knowledge-candidates/retry-context/reject",
        {"candidate": candidate("needs_changes"), "event": event("needs_changes")},
    )

    code, output = run(
        api,
        monkeypatch,
        capsys,
        "candidate",
        "reject",
        "--research-project-id",
        RESEARCH,
        "--candidate-id",
        "retry-context",
        "--expected-revision",
        "1",
        "--needs-changes",
    )

    assert code == EXIT_OK
    assert "needs_changes" in output
    assert api.body() == {
        "research_project_id": RESEARCH,
        "expected_revision": 1,
        "reason": None,
        "decision": "needs_changes",
    }


def test_candidate_cli_promotes_an_approved_candidate_with_explicit_supersession(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    api = RecordedApi()
    api.respond(
        "POST",
        "/api/v3/knowledge-candidates/retry-context/promote",
        {
            "candidate": candidate("promoted"),
            "event": event("promoted"),
            "target": {
                "target_type": "project_context",
                "target_id": "0123456789abcdef01234567",
                "provenance": {
                    "candidate_id": "retry-context",
                    "research_project_id": RESEARCH,
                    "claim_ids": ["retry-key"],
                    "citation_ids": ["provider-support"],
                    "source_ids": ["provider-docs"],
                    "verified_at": "2026-10-01T12:00:00Z",
                },
            },
        },
    )

    code, output = run(
        api,
        monkeypatch,
        capsys,
        "candidate",
        "promote",
        "--research-project-id",
        RESEARCH,
        "--candidate-id",
        "retry-context",
        "--expected-revision",
        "2",
        "--supersedes-context-id",
        "0123456789abcdef01234566",
    )

    assert code == EXIT_OK
    assert "project_context" in output
    assert "skills_modified     no" in output
    assert api.body() == {
        "research_project_id": RESEARCH,
        "expected_revision": 2,
        "supersedes_context_id": "0123456789abcdef01234566",
    }
