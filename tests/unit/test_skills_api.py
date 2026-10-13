"""Phase 4 skill REST endpoint contract tests."""

from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import ASGITransport, AsyncClient

from app.api.routes.skills import get_skill_service, router
from app.models.common import Page
from app.models.skill import SkillDocument
from app.models.skill_chunk import SkillChunkDocument
from app.skills.errors import SkillNotFoundError
from app.skills.types import SkillDetails, SkillLexicalMatch
from tests.api_doubles import open_application


def skill_document() -> SkillDocument:
    """Build public API test metadata."""
    return SkillDocument(
        skill_id="fastapi-production",
        name="FastAPI Production",
        version="1.0.0",
        description="Production API patterns",
        category="backend",
        tags=["fastapi", "python"],
        trust_level="trusted",
        source_path="skills/fastapi/production.md",
        content_hash="a" * 64,
    )


def chunk_document() -> SkillChunkDocument:
    """Build one persisted API test chunk."""
    return SkillChunkDocument(
        skill_id="fastapi-production",
        skill_version="1.0.0",
        chunk_id="chunk-0000",
        content="# FastAPI\n\nValidate request boundaries.",
        section="FastAPI",
        chunk_index=0,
        tokens=8,
        metadata={"heading_path": ["FastAPI"]},
    )


@pytest.mark.asyncio
async def test_skill_endpoints_return_public_models_and_literal_matches() -> None:
    skill = skill_document()
    chunk = chunk_document()
    service = MagicMock()
    service.list_skills = AsyncMock(return_value=Page(items=(skill,)))
    service.get_skill = AsyncMock(
        return_value=SkillDetails(
            skill=skill,
            versions=(skill,),
            chunks=Page(items=(chunk,)),
        )
    )
    service.search_skills = AsyncMock(
        return_value=(SkillLexicalMatch(skill=skill, matched_chunks=(chunk,)),)
    )
    application = open_application(router)

    async def override_service() -> MagicMock:
        return service

    application.dependency_overrides[get_skill_service] = override_service

    transport = ASGITransport(app=application)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        listed = await client.get("/api/v1/skills?category=backend&tag=fastapi")
        detail = await client.get("/api/v1/skills/fastapi-production?version=1.0.0")
        searched = await client.post(
            "/api/v1/skills/search",
            json={"query": "request boundaries", "tags": ["fastapi"], "limit": 5},
        )

    assert listed.status_code == 200
    assert listed.json()["items"][0]["skill_id"] == "fastapi-production"
    assert "_id" not in listed.json()["items"][0]
    assert detail.status_code == 200
    assert detail.json()["versions"][0]["is_active"] is True
    assert detail.json()["chunks"][0]["chunk_id"] == "chunk-0000"
    assert searched.status_code == 200
    assert searched.json()["results"][0]["matched_chunks"][0]["section"] == "FastAPI"
    service.list_skills.assert_awaited_once()
    service.get_skill.assert_awaited_once()
    service.search_skills.assert_awaited_once()


@pytest.mark.asyncio
async def test_skill_endpoints_map_not_found_and_reject_invalid_search() -> None:
    service = MagicMock()
    service.get_skill = AsyncMock(side_effect=SkillNotFoundError("absent"))
    application = open_application(router)

    async def override_service() -> MagicMock:
        return service

    application.dependency_overrides[get_skill_service] = override_service

    transport = ASGITransport(app=application)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        missing = await client.get("/api/v1/skills/absent")
        malformed = await client.post(
            "/api/v1/skills/search",
            json={"query": "", "unexpected": True},
        )

    assert missing.status_code == 404
    assert missing.json() == {"detail": "skill or requested version was not found"}
    assert malformed.status_code == 422
