"""Live MongoDB skill import, version, chunk, and REST API checks."""

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
from bson import ObjectId
from httpx import ASGITransport, AsyncClient
from v6_legacy_integration import initialize_database

from app.api.routes.skills import router as skills_router
from app.core.config import get_settings
from app.db.client import MongoDBClient
from app.repositories.api_key_repository import MongoApiKeyRepository
from app.repositories.credential_repository import MongoCredentialResolver
from app.repositories.skill_chunk_repository import MongoSkillChunkRepository
from app.repositories.skill_repository import MongoSkillRepository
from app.security.scopes import ALL_SCOPES
from app.services.api_key_service import ApiKeyService
from app.services.auth_service import AuthService
from app.services.skill_service import SkillService
from app.skills.chunker import MarkdownSkillChunker
from app.skills.errors import SkillVersionConflictError
from app.skills.importer import SkillImporter
from app.skills.parser import SkillParser
from tests.api_doubles import authenticated_application

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("RUN_MONGODB_INTEGRATION") != "1",
        reason="set RUN_MONGODB_INTEGRATION=1 inside the Compose test service",
    ),
]


def skill_markdown(skill_id: str, version: str, keyword: str, extra: str = "") -> str:
    """Build a canonical version used only by this integration run."""
    return f"""---
id: {skill_id}
name: Phase 4 Integration Skill
version: {version}
description: Transactional skill import integration coverage.
category: phase4-integration
trust_level: generated
tags:
  - phase4
  - integration
---

# Phase 4 Integration Skill

## Rules

The literal marker is {keyword}. {extra}

```python
def preserved_code_block() -> str:
    return "{keyword}"
```

## Verification

Preserve headings, version history, and separate chunks.
"""


@asynccontextmanager
async def skill_database(skill_id: str) -> AsyncIterator[MongoDBClient]:
    """Connect and remove only records owned by this test."""
    mongodb = MongoDBClient(get_settings())
    await mongodb.connect()
    try:
        await initialize_database(mongodb.database)
        yield mongodb
    finally:
        await mongodb.database["skill_chunks"].delete_many({"skill_id": skill_id})
        await mongodb.database["skills"].delete_many({"skill_id": skill_id})
        await mongodb.close()


@pytest.mark.asyncio
async def test_complete_skill_import_version_chunk_search_and_api_lifecycle(
    tmp_path: Path,
) -> None:
    run_id = str(ObjectId())
    skill_id = f"phase4-skill-{run_id}"
    keyword = f"phase4keyword{run_id}"
    root = tmp_path / "skills"
    root.mkdir()
    path = root / "integration.md"
    path.write_text(skill_markdown(skill_id, "1.0.0", keyword), encoding="utf-8")

    async with skill_database(skill_id) as mongodb:
        skill_repository = MongoSkillRepository(mongodb.database)
        chunk_repository = MongoSkillChunkRepository(mongodb.database)
        service = SkillService(skill_repository, chunk_repository)
        importer = SkillImporter(
            SkillParser(max_file_bytes=100_000),
            MarkdownSkillChunker(
                min_tokens=20,
                target_tokens=30,
                max_tokens=45,
                overlap_tokens=5,
            ),
            service,
        )

        first = await importer.import_directory(root)
        repeated = await importer.import_directory(root)
        version_one = await skill_repository.get(skill_id, "1.0.0")
        version_one_chunks = await chunk_repository.list_for_version(
            skill_id,
            "1.0.0",
            limit=50,
        )

        assert (first.new, first.updated, first.unchanged) == (1, 0, 0)
        assert (repeated.new, repeated.updated, repeated.unchanged) == (0, 0, 1)
        assert repeated.chunks_created == 0
        assert version_one is not None and version_one.status == "active"
        assert version_one_chunks.items
        assert all(chunk.skill_version == "1.0.0" for chunk in version_one_chunks.items)
        assert any("preserved_code_block" in chunk.content for chunk in version_one_chunks.items)

        issued = await ApiKeyService(MongoApiKeyRepository(mongodb.database)).create_key(
            name="synthetic-skill-regression", scopes=list(ALL_SCOPES)
        )
        headers = {"Authorization": "Bearer " + issued.token}
        application = authenticated_application(
            skills_router,
            mongodb=mongodb,
            auth=AuthService(MongoCredentialResolver(mongodb.database)),
            tenant_control_database=mongodb.database,
        )
        transport = ASGITransport(app=application)
        async with AsyncClient(
            transport=transport, base_url="http://test", headers=headers
        ) as client:
            listed = await client.get(
                "/api/v1/skills",
                params={"category": "phase4-integration"},
            )
            detail = await client.get(f"/api/v1/skills/{skill_id}")
            searched = await client.post(
                "/api/v1/skills/search",
                json={"query": keyword, "tags": ["phase4"], "limit": 5},
            )

        assert listed.status_code == 200
        assert any(item["skill_id"] == skill_id for item in listed.json()["items"])
        assert detail.status_code == 200
        assert detail.json()["skill"]["version"] == "1.0.0"
        assert searched.status_code == 200
        assert searched.json()["results"][0]["skill"]["skill_id"] == skill_id
        assert keyword in searched.json()["results"][0]["matched_chunks"][0]["content"]

        path.write_text(
            skill_markdown(skill_id, "1.1.0", keyword, "This is the changed version."),
            encoding="utf-8",
        )
        updated = await importer.import_directory(root)
        version_one = await skill_repository.get(skill_id, "1.0.0")
        version_two = await skill_repository.get(skill_id, "1.1.0")
        version_two_chunks = await chunk_repository.list_for_version(
            skill_id,
            "1.1.0",
            limit=50,
        )

        assert updated.updated == 1
        assert version_one is not None and version_one.status == "superseded"
        assert version_two is not None and version_two.status == "active"
        assert version_one_chunks.items
        assert version_two_chunks.items
        assert await importer.import_directory(root) == updated.__class__(1, 0, 0, 1, 0)

        path.write_text(
            skill_markdown(skill_id, "1.1.0", keyword, "Changed without a version bump."),
            encoding="utf-8",
        )
        with pytest.raises(SkillVersionConflictError):
            await importer.import_directory(root)
        assert (await skill_repository.get_active(skill_id)) == version_two

        transport = ASGITransport(app=application)
        async with AsyncClient(
            transport=transport, base_url="http://test", headers=headers
        ) as client:
            current = await client.get(f"/api/v1/skills/{skill_id}")
            historical = await client.get(
                f"/api/v1/skills/{skill_id}",
                params={"version": "1.0.0"},
            )

        assert current.json()["skill"]["version"] == "1.1.0"
        assert len(current.json()["versions"]) == 2
        assert historical.json()["skill"]["status"] == "superseded"
