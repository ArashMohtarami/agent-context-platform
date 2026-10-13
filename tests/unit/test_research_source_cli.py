"""V3 source-registry and constrained ingestion CLI tests."""

from typing import Any

import httpx
import pytest

from app.cli import contextctl
from app.cli.errors import EXIT_OK, EXIT_USAGE
from tests.mcp_doubles import RecordedApi

RESEARCH_ID = "source-registry"
SOURCE_ID = "provider-docs"


def record() -> dict[str, Any]:
    return {
        "research_project_id": RESEARCH_ID,
        "source_id": SOURCE_ID,
        "source_type": "url",
        "uri": "https://example.test/retries",
        "canonical_uri": "https://example.test/retries",
        "title": "Provider retry guide",
        "publisher": "Example",
        "author": "Documentation team",
        "published_at": None,
        "retrieved_at": "2026-09-28T10:00:00Z",
        "source_class": "official_documentation",
        "trust_metadata": {},
        "status": "active",
        "authorization": {"project_ids": ["payments-api"], "owner_key_id": "writer-key"},
        "revision": 1,
        "created_at": "2026-09-28T10:00:00Z",
        "updated_at": "2026-09-28T10:00:00Z",
        "registered_snapshot": None,
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


def test_source_add_list_and_show_use_only_the_v3_source_api(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    api = RecordedApi()
    api.respond("POST", "/api/v3/research/sources", record())
    api.respond("GET", "/api/v3/research/sources", {"items": [record()], "next_cursor": None})
    api.respond("GET", f"/api/v3/research/sources/{SOURCE_ID}", record())

    created, created_out, _ = run(
        api,
        monkeypatch,
        capsys,
        "source",
        "add",
        "--research-project-id",
        RESEARCH_ID,
        "--source-id",
        SOURCE_ID,
        "--source-type",
        "url",
        "--uri",
        "https://example.test/retries",
        "--title",
        "Provider retry guide",
        "--source-class",
        "official_documentation",
    )
    listed, listed_out, _ = run(
        api,
        monkeypatch,
        capsys,
        "source",
        "list",
        "--research-project-id",
        RESEARCH_ID,
        "--status",
        "active",
        "--limit",
        "5",
    )
    shown, shown_out, _ = run(api, monkeypatch, capsys, "source", "show", "--source-id", SOURCE_ID)

    assert created == EXIT_OK
    assert "Provider retry guide" in created_out
    assert api.body(0)["source_type"] == "url"
    assert listed == EXIT_OK
    assert "official_documentation" in listed_out
    assert api.query(1) == {
        "research_project_id": RESEARCH_ID,
        "status": "active",
        "limit": "5",
    }
    assert shown == EXIT_OK
    assert "https://example.test/retries" in shown_out


def test_source_cli_refuses_unsupported_source_types_before_network_or_api_use(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    code, _, error = run(
        RecordedApi(),
        monkeypatch,
        capsys,
        "source",
        "add",
        "--research-project-id",
        RESEARCH_ID,
        "--source-id",
        SOURCE_ID,
        "--source-type",
        "shell",
        "--uri",
        "shell:run",
        "--title",
        "No execution",
    )

    assert code == EXIT_USAGE
    assert "invalid choice" in error


def test_source_ingest_and_status_use_only_documented_phase_three_api_routes(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    api = RecordedApi()
    api.respond(
        "POST",
        f"/api/v3/research/sources/{SOURCE_ID}/ingest",
        {
            "source_id": SOURCE_ID,
            "snapshot": {"snapshot_id": "snapshot-a"},
            "job": {
                "job_id": "ingest-a",
                "status": "completed",
                "documents_created": 1,
                "chunks_created": 2,
            },
            "idempotent": False,
        },
    )
    api.respond(
        "GET",
        f"/api/v3/research/sources/{SOURCE_ID}/ingestion-jobs/ingest-a",
        {
            "job_id": "ingest-a",
            "source_id": SOURCE_ID,
            "snapshot_id": "snapshot-a",
            "status": "completed",
            "error": None,
            "documents_created": 1,
            "chunks_created": 2,
        },
    )

    ingested, ingest_out, _ = run(
        api,
        monkeypatch,
        capsys,
        "source",
        "ingest",
        "--source-id",
        SOURCE_ID,
        "--text",
        "Retries require a stable key.",
        "--content-type",
        "text/markdown",
    )
    observed, status_out, _ = run(
        api,
        monkeypatch,
        capsys,
        "source",
        "status",
        "--source-id",
        SOURCE_ID,
        "--job-id",
        "ingest-a",
    )

    assert ingested == EXIT_OK
    assert "completed" in ingest_out
    assert api.body(0)["content"] == "Retries require a stable key."
    assert "content_base64" not in api.body(0)
    assert observed == EXIT_OK
    assert "ingest-a" in status_out
    assert api.paths == [
        f"/api/v3/research/sources/{SOURCE_ID}/ingest",
        f"/api/v3/research/sources/{SOURCE_ID}/ingestion-jobs/ingest-a",
    ]


def test_source_cli_fetch_url_uses_only_the_registered_source_identifier(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    api = RecordedApi()
    api.respond(
        "POST",
        f"/api/v3/research/sources/{SOURCE_ID}/ingest",
        {
            "source_id": SOURCE_ID,
            "snapshot": {"snapshot_id": "snapshot-url"},
            "job": {"job_id": "ingest-url", "status": "completed"},
            "idempotent": False,
        },
    )

    code, output, _ = run(
        api,
        monkeypatch,
        capsys,
        "source",
        "ingest",
        "--source-id",
        SOURCE_ID,
        "--fetch-url",
    )

    assert code == EXIT_OK
    assert "ingest-url" in output
    assert api.paths == [f"/api/v3/research/sources/{SOURCE_ID}/ingest"]
    assert api.body() == {"fetch_url": True}


def test_source_cli_fetch_repository_uses_only_the_registered_source_identifier(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    api = RecordedApi()
    api.respond(
        "POST",
        f"/api/v3/research/sources/{SOURCE_ID}/ingest",
        {
            "source_id": SOURCE_ID,
            "snapshot": {"snapshot_id": "snapshot-repository"},
            "job": {"job_id": "ingest-repository", "status": "completed"},
            "idempotent": False,
        },
    )

    code, output, _ = run(
        api,
        monkeypatch,
        capsys,
        "source",
        "ingest",
        "--source-id",
        SOURCE_ID,
        "--fetch-repository",
    )

    assert code == EXIT_OK
    assert "ingest-repository" in output
    assert api.paths == [f"/api/v3/research/sources/{SOURCE_ID}/ingest"]
    assert api.body() == {"fetch_repository": True}
