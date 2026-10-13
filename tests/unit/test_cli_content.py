"""V2 Phase 5-7 content CLI commands delegate work to the protected API."""

from pathlib import Path
from typing import Any

import httpx
import pytest

from app.cli import contextctl
from app.cli.errors import EXIT_OK
from tests.mcp_doubles import RecordedApi

PROFILE = "personal-brand"


def serving(api: RecordedApi, monkeypatch: pytest.MonkeyPatch) -> None:
    """Install a recording HTTP transport without needing MongoDB in a CLI test."""
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


def test_content_commands_discover_save_reviewed_ideas_and_prepare_briefs_via_api(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    api = RecordedApi()
    api.respond(
        "POST",
        "/api/v2/content-ideas/discover",
        {
            "profile_id": PROFILE,
            "project_id": "payment-service",
            "pillar_id": "payments",
            "platform": "linkedin",
            "candidates": [
                {
                    "topic": "Idempotent payment retries",
                    "related_pillar_id": "payments",
                    "related_project_id": "payment-service",
                    "suggested_platforms": ["linkedin"],
                    "source_refs": [{"source_type": "project_context", "source_id": "retry-rule"}],
                    "freshness": {"updated_at": "2026-09-22T00:00:00Z"},
                }
            ],
            "existing_unpublished_ideas": [],
        },
    )
    api.respond(
        "POST",
        "/api/v2/content-ideas",
        {
            "profile_id": PROFILE,
            "idea_id": "idempotency",
            "title": "Idempotent payment retries",
            "status": "idea",
            "pillar_id": "payments",
            "platforms": ["linkedin"],
            "origin": {"source_ids": ["retry-rule"]},
        },
    )
    api.respond(
        "POST",
        "/api/v2/content-briefs/prepare",
        {
            "objective": "Prepare a linkedin piece about Idempotent payment retries",
            "audience": ["backend engineers"],
            "primary_message": "Retries reuse the original request key.",
            "supporting_points": ["Idempotent payment retries"],
            "source_refs": [{"source_type": "project_context", "source_id": "retry-rule"}],
            "technical_skills": [],
            "content_skill": {"skill_id": "linkedin-writing"},
            "similar_previous_content": [],
            "content_constraints": ["do not fabricate project experiences"],
        },
    )

    discovery_code, discovery_out, _ = run(
        api,
        monkeypatch,
        capsys,
        "content",
        "ideas",
        "--profile-id",
        PROFILE,
        "--project-id",
        "payment-service",
        "--pillar-id",
        "payments",
        "--platform",
        "linkedin",
        "--since",
        "2026-09-01T00:00:00Z",
    )
    save_code, save_out, _ = run(
        api,
        monkeypatch,
        capsys,
        "content",
        "idea",
        "create",
        "--profile-id",
        PROFILE,
        "--idea-id",
        "idempotency",
        "--title",
        "Idempotent payment retries",
        "--description",
        "Retries reuse the original request key.",
        "--platform",
        "linkedin",
        "--origin-type",
        "project_context",
        "--origin-source-id",
        "retry-rule",
        "--project-id",
        "payment-service",
        "--priority",
        "0.9",
    )
    brief_code, brief_out, _ = run(
        api,
        monkeypatch,
        capsys,
        "content",
        "brief",
        "--profile-id",
        PROFILE,
        "--idea-id",
        "idempotency",
        "--platform",
        "linkedin",
    )

    assert (discovery_code, save_code, brief_code) == (EXIT_OK, EXIT_OK, EXIT_OK)
    assert "Idempotent payment retries" in discovery_out
    assert "idempotency" in save_out
    assert "do not fabricate project experiences" in brief_out
    assert api.paths == [
        "/api/v2/content-ideas/discover",
        "/api/v2/content-ideas",
        "/api/v2/content-briefs/prepare",
    ]
    assert api.body(0)["time_range"] == {"start_at": "2026-09-01T00:00:00Z"}
    assert api.body(1)["origin"]["source_ids"] == ["retry-rule"]
    assert api.body(2)["idea_id"] == "idempotency"


def test_content_phase6_commands_delegate_novelty_and_repurpose_to_api(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    api = RecordedApi()
    api.respond(
        "POST",
        "/api/v2/content/novelty",
        {
            "classification": "same-topic-new-angle",
            "topic_overlap": 0.7,
            "angle_overlap": 0.1,
            "similar_content": [
                {
                    "identifier": "outbox-medium",
                    "title": "The Outbox lesson",
                    "classification": "same-topic-new-angle",
                    "topic_overlap": 0.7,
                    "angle_overlap": 0.1,
                }
            ],
        },
    )
    api.respond(
        "POST",
        "/api/v2/content/outbox-medium/repurpose",
        {
            "source_kind": "content",
            "source_id": "outbox-medium",
            "core_thesis": "The Outbox lesson",
            "source_refs": [{"source_type": "project_context", "source_id": "outbox-adr"}],
            "targets": [
                {
                    "platform": "linkedin",
                    "target_content_skill": {"skill_id": "linkedin-writing"},
                    "what_to_shorten": ["background context"],
                    "what_requires_expansion": ["practical conclusion"],
                },
                {
                    "platform": "github",
                    "target_content_skill": {"skill_id": "github-writing"},
                    "what_to_shorten": ["narrative framing"],
                    "what_requires_expansion": ["implementation details"],
                },
            ],
        },
    )

    novelty_code, novelty_out, _ = run(
        api,
        monkeypatch,
        capsys,
        "content",
        "novelty",
        "--profile-id",
        PROFILE,
        "--title",
        "An operational Outbox angle",
        "--description",
        "Monitor delivery lag rather than repeat implementation details.",
        "--tag",
        "outbox",
        "--project-id",
        "payment-service",
    )
    repurpose_code, repurpose_out, _ = run(
        api,
        monkeypatch,
        capsys,
        "content",
        "repurpose",
        "--profile-id",
        PROFILE,
        "--content-id",
        "outbox-medium",
        "--target",
        "linkedin",
        "--target",
        "github",
    )

    assert (novelty_code, repurpose_code) == (EXIT_OK, EXIT_OK)
    assert "same-topic-new-angle" in novelty_out
    assert "linkedin-writing" in repurpose_out
    assert api.paths == ["/api/v2/content/novelty", "/api/v2/content/outbox-medium/repurpose"]
    assert api.body(0)["tags"] == ["outbox"]
    assert api.body(1)["targets"] == ["linkedin", "github"]


def test_content_export_prints_or_writes_only_the_api_manual_representation(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    api = RecordedApi()
    api.respond(
        "POST",
        "/api/v2/content/idempotency-post/export",
        {
            "profile_id": PROFILE,
            "content_id": "idempotency-post",
            "platform": "medium",
            "mode": "markdown",
            "format": "markdown",
            "text": "# Idempotency — سلام 👋\n\nA reproducible retry guide.\n",
            "suggested_file_name": "idempotency.md",
            "suggested_path": None,
            "metadata": {"representation": "markdown"},
        },
    )
    output = tmp_path / "idempotency.md"

    stdout_code, stdout, _ = run(
        api,
        monkeypatch,
        capsys,
        "content",
        "export",
        "--profile-id",
        PROFILE,
        "--content-id",
        "idempotency-post",
        "--mode",
        "markdown",
    )
    file_code, file_out, _ = run(
        api,
        monkeypatch,
        capsys,
        "content",
        "export",
        "--profile-id",
        PROFILE,
        "--content-id",
        "idempotency-post",
        "--mode",
        "file",
        "--output",
        str(output),
    )

    assert (stdout_code, file_code) == (EXIT_OK, EXIT_OK)
    assert "Idempotency — سلام 👋" in stdout
    assert "written_to" in file_out
    assert output.read_text(encoding="utf-8").startswith("# Idempotency — سلام 👋")
    assert api.paths == [
        "/api/v2/content/idempotency-post/export",
        "/api/v2/content/idempotency-post/export",
    ]
    assert api.body(0) == {"profile_id": PROFILE, "mode": "markdown"}
    assert api.body(1) == {"profile_id": PROFILE, "mode": "file"}
