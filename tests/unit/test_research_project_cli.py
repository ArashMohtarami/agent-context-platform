"""V3 Phase 1 research-project CLI tests."""

from typing import Any

import httpx
import pytest

from app.cli import contextctl
from app.cli.errors import EXIT_OK, EXIT_USAGE
from tests.mcp_doubles import RecordedApi

PROJECT = "payments-api"
RESEARCH_ID = "mongot-tradeoffs"


def record() -> dict[str, Any]:
    return {
        "research_project_id": RESEARCH_ID,
        "name": "Mongot tradeoffs",
        "question": "What are the operational tradeoffs of self-managed mongot?",
        "type": "technical_research",
        "related_project_ids": [PROJECT],
        "status": "active",
        "authorization": {"project_ids": [PROJECT], "owner_key_id": "0123456789abcdef"},
        "revision": 1,
        "created_at": "2026-09-28T00:00:00Z",
        "updated_at": "2026-09-28T00:00:00Z",
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


def test_research_create_list_and_show_use_only_the_v3_api(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    api = RecordedApi()
    api.respond("POST", "/api/v3/research/projects", record())
    api.respond("GET", "/api/v3/research/projects", {"items": [record()], "next_cursor": None})
    api.respond("GET", f"/api/v3/research/projects/{RESEARCH_ID}", record())

    create_code, create_out, _ = run(
        api,
        monkeypatch,
        capsys,
        "research",
        "create",
        "--research-project-id",
        RESEARCH_ID,
        "--name",
        "Mongot tradeoffs",
        "--question",
        "What are the operational tradeoffs of self-managed mongot?",
        "--type",
        "technical_research",
        "--related-project-id",
        PROJECT,
    )
    list_code, list_out, _ = run(
        api,
        monkeypatch,
        capsys,
        "research",
        "list",
        "--status",
        "active",
        "--limit",
        "5",
    )
    show_code, show_out, _ = run(
        api,
        monkeypatch,
        capsys,
        "research",
        "show",
        "--research-project-id",
        RESEARCH_ID,
    )

    assert create_code == EXIT_OK
    assert "Mongot tradeoffs" in create_out
    assert api.body(0)["related_project_ids"] == [PROJECT]
    assert list_code == EXIT_OK
    assert "technical_research" in list_out
    assert api.query(1) == {"status": "active", "limit": "5"}
    assert show_code == EXIT_OK
    assert "self-managed mongot" in show_out


def test_research_cli_validates_the_phase_one_type_vocabulary(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    code, _, error = run(
        RecordedApi(),
        monkeypatch,
        capsys,
        "research",
        "create",
        "--research-project-id",
        RESEARCH_ID,
        "--name",
        "Invalid",
        "--question",
        "Is an invalid type rejected?",
        "--type",
        "not-a-type",
    )

    assert code == EXIT_USAGE
    assert "invalid choice" in error


def test_research_search_uses_the_phase_five_api_and_pagination_cursor(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    api = RecordedApi()
    api.respond(
        "POST",
        "/api/v3/research/search",
        {
            "research_project_id": RESEARCH_ID,
            "retrieval_profile": "research_general",
            "method": "lexical",
            "items": [
                {
                    "kind": "chunk",
                    "identifier": "retry-chunk",
                    "source": "research_primary",
                    "title": "Retry guide",
                    "retrieval_score": 0.9,
                    "source_id": "retry-guide",
                }
            ],
            "token_budget": 8000,
            "tokens_used": 5,
            "next_cursor": "next",
        },
    )

    code, output, _ = run(
        api,
        monkeypatch,
        capsys,
        "research",
        "search",
        "--research-project-id",
        RESEARCH_ID,
        "--query",
        "retry key",
        "--method",
        "lexical",
        "--source-class",
        "primary",
        "--cursor",
        "before",
    )

    assert code == EXIT_OK
    assert "Retry guide" in output
    assert "--cursor next" in output
    assert api.body() == {
        "research_project_id": RESEARCH_ID,
        "query": "retry key",
        "retrieval_profile": "research_general",
        "method": "lexical",
        "phrase": False,
        "source_classes": ["primary"],
        "source_types": [],
        "publisher": None,
        "domain": None,
        "published_after": None,
        "published_before": None,
        "retrieved_after": None,
        "as_of": None,
        "related_project_id": None,
        "token_budget": None,
        "limit": 10,
        "cursor": "before",
    }
