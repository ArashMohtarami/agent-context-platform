"""V3 Phase 8 research-brief CLI tests."""

from typing import Any

import httpx
import pytest

from app.cli import contextctl
from app.cli.errors import EXIT_OK
from tests.mcp_doubles import RecordedApi

RESEARCH = "retry-evidence"


def response() -> dict[str, Any]:
    return {
        "research_project_id": RESEARCH,
        "brief_id": "retry-summary",
        "question": "What constrains safe payment retries?",
        "scope": "Provider documentation.",
        "key_findings": ["Retries use an idempotency key."],
        "claim_refs": [],
        "citation_refs": [],
        "contradictions": [],
        "unknowns": ["This brief is structured evidence material and does not claim completeness."],
        "sources": [],
        "next_questions": ["Which versions enforce the requirement?"],
        "status": "draft",
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


def test_research_brief_cli_forwards_only_typed_brief_fields(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    api = RecordedApi()
    api.respond("POST", "/api/v3/research/briefs", response(), status_code=201)

    code, output, _ = run(
        api,
        monkeypatch,
        capsys,
        "research",
        "brief",
        "--research-project-id",
        RESEARCH,
        "--brief-id",
        "retry-summary",
        "--scope",
        "Provider documentation.",
        "--additional-unknown",
        "SDK coverage is incomplete.",
        "--next-question",
        "Which versions enforce the requirement?",
        "--status",
        "draft",
    )

    assert code == EXIT_OK
    assert "retry-summary" in output
    assert "structured evidence" in output
    assert api.paths == ["/api/v3/research/briefs"]
    assert api.body() == {
        "research_project_id": RESEARCH,
        "brief_id": "retry-summary",
        "scope": "Provider documentation.",
        "additional_unknowns": ["SDK coverage is incomplete."],
        "next_questions": ["Which versions enforce the requirement?"],
        "status": "draft",
    }
