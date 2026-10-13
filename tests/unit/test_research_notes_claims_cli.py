"""V3 Phase 6 note and claim CLI commands are thin REST clients."""

from typing import Any

import httpx
import pytest

from app.cli import contextctl
from app.cli.errors import EXIT_OK, EXIT_USAGE
from tests.mcp_doubles import RecordedApi

RESEARCH = "retry-evidence"


def note() -> dict[str, Any]:
    return {
        "research_project_id": RESEARCH,
        "note_id": "retry-note",
        "title": "Retry notes",
        "body": "Working artifact only.",
        "source_refs": [{"source_id": "provider-docs"}],
        "created_by": None,
        "status": "working",
        "authorization": {"project_ids": ["payments-api"], "owner_key_id": None},
        "revision": 1,
        "created_at": "2026-09-30T00:00:00Z",
        "updated_at": "2026-09-30T00:00:00Z",
    }


def claim() -> dict[str, Any]:
    return {
        "research_project_id": RESEARCH,
        "claim_id": "retry-key",
        "statement": "Retries require an idempotency key.",
        "status": "contested",
        "confidence": 0.4,
        "confidence_rationale": "Evidence conflicts.",
        "supporting_citations": ["supports"],
        "contradicting_citations": ["contradicts"],
        "authorization": {"project_ids": ["payments-api"], "owner_key_id": None},
        "revision": 1,
        "created_at": "2026-09-30T00:00:00Z",
        "updated_at": "2026-09-30T00:00:00Z",
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
) -> tuple[int, str, str]:
    monkeypatch.setenv("CONTEXTCTL_API_BASE_URL", "http://context-api.invalid")
    monkeypatch.delenv("CONTEXTCTL_API_TOKEN", raising=False)
    monkeypatch.delenv("CONTEXTCTL_API_TOKEN_FILE", raising=False)
    serving(api, monkeypatch)
    code = contextctl.main(argv)
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def test_note_create_and_claim_list_show_use_only_phase_six_api(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    api = RecordedApi()
    api.respond("POST", "/api/v3/research/notes", note())
    api.respond("GET", "/api/v3/research/claims", {"items": [claim()], "next_cursor": "next"})
    api.respond("GET", "/api/v3/research/claims/retry-key", claim())

    note_code, note_output, _ = run(
        api,
        monkeypatch,
        capsys,
        "note",
        "create",
        "--research-project-id",
        RESEARCH,
        "--note-id",
        "retry-note",
        "--title",
        "Retry notes",
        "--body",
        "Working artifact only.",
        "--source-ref",
        '{"source_id":"provider-docs"}',
    )
    list_code, list_output, _ = run(
        api,
        monkeypatch,
        capsys,
        "claim",
        "list",
        "--research-project-id",
        RESEARCH,
        "--status",
        "contested",
        "--limit",
        "5",
    )
    show_code, show_output, _ = run(
        api,
        monkeypatch,
        capsys,
        "claim",
        "show",
        "--research-project-id",
        RESEARCH,
        "--claim-id",
        "retry-key",
    )

    assert note_code == EXIT_OK
    assert "retry-note" in note_output
    assert api.body(0)["source_refs"] == [{"source_id": "provider-docs"}]
    assert list_code == EXIT_OK
    assert "Retries require an idempotency key" in list_output
    assert "--cursor next" in list_output
    assert api.query(1) == {
        "research_project_id": RESEARCH,
        "status": "contested",
        "limit": "5",
    }
    assert show_code == EXIT_OK
    assert "Evidence conflicts" in show_output
    assert api.query(2) == {"research_project_id": RESEARCH}


def test_note_cli_rejects_invalid_provenance_json(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    code, _, error = run(
        RecordedApi(),
        monkeypatch,
        capsys,
        "note",
        "create",
        "--research-project-id",
        RESEARCH,
        "--note-id",
        "retry-note",
        "--title",
        "Retry notes",
        "--body",
        "Working artifact only.",
        "--source-ref",
        "not-json",
    )

    assert code == EXIT_USAGE
    assert "--source-ref must be valid JSON" in error
