"""V3 Phase 11 read/export-only CLI tests."""

from typing import Any

import httpx
import pytest

from app.cli import contextctl
from app.cli.errors import EXIT_OK
from tests.mcp_doubles import RecordedApi

RESEARCH = "retry-evidence"


def proposal() -> dict[str, Any]:
    return {
        "research_project_id": RESEARCH,
        "proposal_id": "idempotency-guidance",
        "skill_id": "backend-guidance",
        "current_version": "1.2.3",
        "proposed_change": "Clarify idempotency key guidance.",
        "proposed_patch": "--- a/skills/backend-guidance.md\n+++ b/skills/backend-guidance.md",
        "source_claim_ids": ["retry-key"],
        "source_citation_ids": ["provider-support"],
        "reason": "The retained provider evidence changed.",
        "status": "pending_review",
        "trust_level": "generated",
        "authorization": {"project_ids": ["payments-api"], "owner_key_id": "writer"},
        "revision": 1,
        "created_at": "2026-10-01T00:00:00Z",
        "updated_at": "2026-10-01T00:00:00Z",
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


def test_skill_proposal_cli_lists_shows_and_exports_without_writing_a_file(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    api = RecordedApi()
    api.respond(
        "GET",
        "/api/v3/skill-update-proposals",
        {"items": [proposal()], "next_cursor": None},
    )
    api.respond("GET", "/api/v3/skill-update-proposals/idempotency-guidance", proposal())
    api.respond(
        "GET",
        "/api/v3/skill-update-proposals/idempotency-guidance/export",
        {"proposal": proposal(), "proposed_patch": proposal()["proposed_patch"]},
    )

    listed_code, listed = run(
        api, monkeypatch, capsys, "skill", "proposal", "list", "--research-project-id", RESEARCH
    )
    shown_code, shown = run(
        api,
        monkeypatch,
        capsys,
        "skill",
        "proposal",
        "show",
        "--research-project-id",
        RESEARCH,
        "--proposal-id",
        "idempotency-guidance",
    )
    exported_code, exported = run(
        api,
        monkeypatch,
        capsys,
        "skill",
        "proposal",
        "export",
        "--research-project-id",
        RESEARCH,
        "--proposal-id",
        "idempotency-guidance",
    )

    assert listed_code == shown_code == exported_code == EXIT_OK
    assert "idempotency-guidance" in listed
    assert "generated" in shown
    assert exported == proposal()["proposed_patch"] + "\n"
    assert api.paths == [
        "/api/v3/skill-update-proposals",
        "/api/v3/skill-update-proposals/idempotency-guidance",
        "/api/v3/skill-update-proposals/idempotency-guidance/export",
    ]
