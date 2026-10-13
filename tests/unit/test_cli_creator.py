"""V2 Phase 1 creator CLI commands call the API rather than MongoDB."""

from typing import Any

import httpx
import pytest

from app.cli import contextctl
from app.cli.errors import EXIT_OK, EXIT_USAGE
from tests.mcp_doubles import RecordedApi

PROFILE = "personal-brand"


def profile() -> dict[str, Any]:
    return {
        "profile_id": PROFILE,
        "name": "Technical Personal Brand",
        "status": "active",
        "audiences": ["backend engineers"],
        "domains": ["payments"],
        "voice": {"tone": ["direct"], "avoid": ["hype"]},
        "writing_preferences": [],
        "formatting_preferences": [],
        "terminology_preferences": [],
        "language_preferences": ["English"],
        "tone_preferences": ["practical"],
        "topics_of_interest": ["idempotency"],
        "topics_to_avoid": [],
        "content_objectives": ["teach"],
        "tags": ["backend"],
        "revision": 1,
        "created_at": "2026-09-21T00:00:00Z",
        "updated_at": "2026-09-21T00:00:00Z",
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


def test_creator_show_and_pillars_read_v2_api_data(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    api = RecordedApi()
    api.respond("GET", f"/api/v2/creator-profiles/{PROFILE}", profile())
    api.respond(
        "GET",
        "/api/v2/content-pillars",
        {
            "profile_id": PROFILE,
            "items": [
                {
                    "pillar_id": "payments",
                    "name": "Payments",
                    "status": "active",
                    "priority": 0.9,
                    "tags": ["backend"],
                }
            ],
            "next_cursor": None,
        },
    )

    show_code, show_out, _ = run(
        api, monkeypatch, capsys, "creator", "show", "--profile-id", PROFILE
    )
    pillar_code, pillar_out, _ = run(
        api,
        monkeypatch,
        capsys,
        "creator",
        "pillars",
        "--profile-id",
        PROFILE,
        "--limit",
        "5",
    )

    assert show_code == EXIT_OK
    assert "Technical Personal Brand" in show_out
    assert pillar_code == EXIT_OK
    assert "Payments" in pillar_out
    assert api.query() == {"profile_id": PROFILE, "limit": "5"}


def test_creator_update_sends_only_explicit_changes_with_the_revision_guard(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    api = RecordedApi()
    api.respond("PATCH", f"/api/v2/creator-profiles/{PROFILE}", profile() | {"revision": 2})

    code, out, _ = run(
        api,
        monkeypatch,
        capsys,
        "creator",
        "update",
        "--profile-id",
        PROFILE,
        "--expected-revision",
        "1",
        "--status",
        "inactive",
        "--audience",
        "software architects",
    )

    assert code == EXIT_OK
    assert "revision" in out
    assert api.body() == {
        "expected_revision": 1,
        "status": "inactive",
        "audiences": ["software architects"],
    }


def test_creator_update_requires_a_real_change(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    code, _, err = run(
        RecordedApi(),
        monkeypatch,
        capsys,
        "creator",
        "update",
        "--profile-id",
        PROFILE,
        "--expected-revision",
        "1",
    )

    assert code == EXIT_USAGE
    assert "at least one" in err
