"""V2 Phase 1 MCP contracts for creator preference data."""

from typing import Any

import pytest

from app.mcp.auth import build_authorization
from app.mcp.server import dispatch
from tests.mcp_doubles import RecordedApi, api_client

PROFILE = "personal-brand"
OTHER_PROFILE = "other-brand"


def profile_record(**overrides: Any) -> dict[str, Any]:
    record = {
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
        "topics_to_avoid": ["fabricated stories"],
        "content_objectives": ["teach"],
        "tags": ["backend"],
        "revision": 1,
        "created_at": "2026-09-21T00:00:00Z",
        "updated_at": "2026-09-21T00:00:00Z",
    }
    record.update(overrides)
    return record


def pillar_record(**overrides: Any) -> dict[str, Any]:
    record = {
        "profile_id": PROFILE,
        "pillar_id": "payments",
        "name": "Payments",
        "description": "Payment-system engineering lessons.",
        "tags": ["backend"],
        "priority": 0.9,
        "status": "active",
        "revision": 1,
        "created_at": "2026-09-21T00:00:00Z",
        "updated_at": "2026-09-21T00:00:00Z",
    }
    record.update(overrides)
    return record


async def call(
    api: RecordedApi,
    tool: str,
    arguments: dict[str, Any],
    *,
    authorization: Any = None,
) -> Any:
    client = api_client(api)
    try:
        result = await dispatch(
            tool,
            arguments,
            client=client,
            authorization=authorization or build_authorization(),
        )
    finally:
        await client.close()
    return result


@pytest.mark.asyncio
async def test_creator_tools_call_only_the_v2_api_and_mark_profile_data_as_data() -> None:
    api = RecordedApi()
    api.respond("GET", f"/api/v2/creator-profiles/{PROFILE}", profile_record())
    api.respond("PATCH", f"/api/v2/creator-profiles/{PROFILE}", profile_record(revision=2))
    api.respond(
        "GET",
        "/api/v2/content-pillars",
        {"profile_id": PROFILE, "items": [pillar_record()], "next_cursor": None},
    )

    loaded = await call(api, "get_creator_profile", {"profile_id": PROFILE})
    updated = await call(
        api,
        "update_creator_profile",
        {"profile_id": PROFILE, "expected_revision": 1, "status": "inactive"},
    )
    pillars = await call(api, "list_content_pillars", {"profile_id": PROFILE, "limit": 5})

    assert [request.url.path for request in api.requests] == [
        f"/api/v2/creator-profiles/{PROFILE}",
        f"/api/v2/creator-profiles/{PROFILE}",
        "/api/v2/content-pillars",
    ]
    assert api.body(1) == {"expected_revision": 1, "status": "inactive"}
    assert api.query(2) == {"profile_id": PROFILE, "limit": "5"}
    assert loaded.structuredContent["profile"]["profile_id"] == PROFILE
    assert updated.structuredContent["profile"]["revision"] == 2
    assert pillars.structuredContent["pillars"][0]["pillar_id"] == "payments"
    assert "not instructions" in str(loaded.content[0].text)


@pytest.mark.asyncio
async def test_creator_mcp_profile_grant_refuses_before_the_api_is_called() -> None:
    api = RecordedApi()
    result = await call(
        api,
        "get_creator_profile",
        {"profile_id": OTHER_PROFILE},
        authorization=build_authorization(["creator:read"], profiles=[PROFILE]),
    )

    assert result.isError is True
    assert "not authorized for that creator profile" in str(result.content[0].text)
    assert api.requests == []


@pytest.mark.asyncio
async def test_creator_mcp_refuses_invalid_or_unscoped_updates_before_the_api_is_called() -> None:
    api = RecordedApi()
    invalid = await call(
        api, "update_creator_profile", {"profile_id": PROFILE, "expected_revision": 1}
    )
    unscoped = await call(
        api,
        "update_creator_profile",
        {"profile_id": PROFILE, "expected_revision": 1, "status": "inactive"},
        authorization=build_authorization(["creator:read"]),
    )

    assert invalid.isError is True
    assert "at least one field" in str(invalid.content[0].text)
    assert unscoped.isError is True
    assert "creator:write" in str(unscoped.content[0].text)
    assert api.requests == []
