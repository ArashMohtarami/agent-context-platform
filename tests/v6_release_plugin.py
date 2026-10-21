"""Explicit opt-in test configuration: never consult the installation's dotenv file."""

import os
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

import pytest
import pytest_asyncio
from pymongo import AsyncMongoClient

from app.core.config import Settings, get_settings
from app.db.initializer import initialize_database
from app.models.organization import OrganizationDocument
from app.repositories.organization_repository import MongoOrganizationRepository
from app.repositories.tenant_adoption_repository import MongoTenantAdoptionRepository
from app.security.tenant import TenantContext, tenant_scope
from app.services.tenant_adoption_service import TenantAdoptionService
from app.services.tenant_isolation_service import TenantIsolationService


def pytest_configure(config):
    if os.environ.get("V6_FULL_ACCEPTANCE") != "1":
        raise pytest.UsageError("release plugin requires explicit disposable runner")
    uri = urlsplit(os.environ["V6_TEST_MONGODB_URI"])
    if uri.hostname != "127.0.0.1" or not os.environ["V6_TEST_MONGODB_CONTAINER"].startswith(
        "acp-v6-phase2-mongodb-"
    ):
        raise pytest.UsageError("refusing non-disposable release environment")
    Settings.model_config["env_file"] = None
    get_settings.cache_clear()


@pytest_asyncio.fixture(autouse=True)
async def admitted_legacy_organization(request, tmp_path, monkeypatch):
    """Run V1-V5 behavior within an explicitly adopted organization.

    API requests still discard ambient context and authenticate their real
    credential. This context is only for the test's direct operator provisioning
    and repository checks; it is never injected into a request principal.
    """
    if (
        request.path.parent.name != "integration"
        or request.path.name.startswith("test_v6_")
        or request.path.name == "test_local_embedding_model.py"
    ):
        yield
        return
    from v6_isolation_fixtures import archive

    client = AsyncMongoClient(os.environ["V6_TEST_MONGODB_URI"], tz_aware=True)
    database = client["acp_v6_legacy_" + uuid4().hex]
    settings = get_settings()
    await database.command(
        "createUser",
        settings.mongodb_username,
        pwd=settings.mongodb_password_value(),
        roles=[
            {"role": "readWrite", "db": database.name},
            {"role": "dbAdmin", "db": database.name},
            {"role": "clusterMonitor", "db": "admin"},
        ],
    )
    monkeypatch.setenv("MONGODB_DATABASE", database.name)
    monkeypatch.setenv("V6_TENANT_FOUNDATION", "true")
    monkeypatch.setenv("V6_TENANT_ISOLATION", "true")
    get_settings.cache_clear()
    try:
        await initialize_database(database)
        await initialize_database(database, tenant_foundation=True)
        await MongoOrganizationRepository(database).create(
            OrganizationDocument(org_id="org-test", slug="regression", name="Fictional regression")
        )
        backup, manifest = await archive(database, tmp_path)
        with tenant_scope(TenantContext("org-test")):
            result = await TenantAdoptionService(MongoTenantAdoptionRepository(database)).apply(
                org_id="org-test",
                adoption_id="legacy-regression",
                archive=backup,
                manifest_path=manifest,
                writers_stopped=True,
                worker_schema_versions=[36],
            )
            assert result.compatible and result.stage == "prepared"
            isolation = TenantIsolationService(database)
            await isolation.prepare(writers_stopped=True, worker_schema_versions=[37])
            if os.environ.get("V6_LEGACY_DEVELOPMENT_ONLY") == "1":
                # Same explicit fixture seal as isolated_database. No cutover
                # evidence is claimed by the development-only selector.
                await database["_tenant_control"].update_one(
                    {},
                    {
                        "$set": {
                            "stage": "isolated",
                            "domain_traffic_enabled": True,
                            "worker_schema_versions": [37],
                        }
                    },
                )
                await database.organizations.update_one(
                    {"org_id": "org-test"}, {"$set": {"status": "active"}}
                )
            else:
                await isolation.activate(
                    writers_stopped=True,
                    worker_schema_versions=[37],
                    evidence=[
                        Path(os.environ["V6_UNIT_EVIDENCE_XML"]),
                        Path(os.environ["V6_REAL_EVIDENCE_XML"]),
                    ],
                )
        control = await database["_tenant_control"].find_one({"_id": "installation"})
        if request.path.name == "test_backup_recovery.py":
            # These historical recovery tests assumed a running application's
            # derived skill indexes. Rebuild them in their fresh fixture DB.
            from app.db.text_indexes import ensure_text_indexes
            from app.db.vector_indexes import ensure_vector_indexes

            await ensure_text_indexes(database)
            await ensure_vector_indexes(database, dimensions=settings.embedding_dimensions)
            # Index creation is asynchronous. Require the fixture's actual
            # derived indexes to be ready before backing up/restoring data;
            # MongoDB/mongot health alone does not establish queryability.
            from v6_legacy_integration import search_indexes_ready

            await search_indexes_ready(database, ("skill_chunks",), 2)
        with tenant_scope(TenantContext("org-test", control["cursor_secret"])):
            yield
    finally:
        await client.drop_database(database.name)
        await client.close()
        get_settings.cache_clear()
