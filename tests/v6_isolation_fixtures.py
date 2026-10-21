"""A real pre-V6 adoption followed by two colliding tenant data sets."""

import asyncio
import os
import shutil
from datetime import UTC, timedelta
from urllib.parse import urlparse
from uuid import uuid4

import pytest
import pytest_asyncio
from pymongo import AsyncMongoClient
from v6_fixtures import legacy_fixture

from app.backup.manifest import build, write
from app.db.initializer import initialize_database
from app.db.tenant_inventory import MODEL_BY_COLLECTION
from app.models.common import utc_now
from app.models.organization import OrganizationDocument
from app.repositories.organization_repository import MongoOrganizationRepository
from app.repositories.tenant_adoption_repository import MongoTenantAdoptionRepository
from app.security.keys import hash_secret
from app.security.scopes import ALL_SCOPES
from app.security.tenant import TenantContext, tenant_scope
from app.services.tenant_adoption_service import TenantAdoptionService


async def archive(database, tmp_path):
    container = os.environ["V6_TEST_MONGODB_CONTAINER"]
    assert container.startswith("acp-v6-phase2-mongodb-")
    docker = shutil.which("docker")
    assert docker
    # Only a disposable container and disposable database are allowed. The
    # password remains inside the container; never logged or included in XML.
    process = await asyncio.create_subprocess_exec(
        docker,
        "exec",
        container,
        "sh",
        "-c",
        "exec mongodump --quiet --username v6-test-root "
        '--password "$(cat /run/secrets/root)" --authenticationDatabase admin '
        '--archive --db "$1"',
        "v6-disposable-backup",
        database.name,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    output, diagnostics = await process.communicate()
    # Fixed classifications aid disposable infrastructure diagnosis without
    # putting credentials or arbitrary process output into reports.
    categories = [
        name
        for name in (
            "Authentication failed",
            "not primary",
            "connection refused",
            "context deadline exceeded",
            "Failed",
            "OCI runtime",
            "permission denied",
            "not found",
        )
        if name.lower().encode() in diagnostics.lower()
    ]
    assert process.returncode == 0, "disposable backup failed (credentials withheld): " + ", ".join(
        categories
    )
    backup = tmp_path / "pre-v6.archive"
    backup.write_bytes(output)
    manifest = tmp_path / "pre-v6.manifest.json"
    counts = await MongoTenantAdoptionRepository(database).counts()
    write(
        manifest,
        build(
            archive=backup,
            created_at=utc_now(),
            database=database.name,
            gzip=False,
            collections=counts,
        ),
    )
    return backup, manifest


@pytest_asyncio.fixture
async def isolated_database(tmp_path):
    uri = os.environ.get("V6_TEST_MONGODB_URI")
    if not uri:
        pytest.skip("explicit disposable MongoDB and mongot required; no integration evidence")
    parsed = urlparse(uri)
    assert parsed.hostname == "127.0.0.1" and "directConnection=true" in parsed.query
    # Match the shipped console's transport/UTC codec. This initializes
    # connections only; it never preloads domain data or authorization decisions.
    client = AsyncMongoClient(
        uri,
        tz_aware=True,
        tzinfo=UTC,
        minPoolSize=50,
        maxPoolSize=50,
        serverSelectionTimeoutMS=5000,
    )
    hello = await client.admin.command("hello")
    assert hello.get("setName") == "acp_v6_phase2" and hello.get("isWritablePrimary")
    database = client["acp_v6_phase2_" + uuid4().hex]
    try:
        await initialize_database(database)
        originals = legacy_fixture()
        for collection, rows in originals.items():
            await database[collection].insert_many(rows)
        await initialize_database(database, tenant_foundation=True)
        orgs = MongoOrganizationRepository(database)
        for org in ("a", "b"):
            await orgs.create(OrganizationDocument(org_id="org-" + org, slug=org, name=org))
        backup, manifest = await archive(database, tmp_path)
        report = await TenantAdoptionService(MongoTenantAdoptionRepository(database)).apply(
            org_id="org-a",
            adoption_id="phase2-fixture",
            archive=backup,
            manifest_path=manifest,
            writers_stopped=True,
            worker_schema_versions=[36],
        )
        assert report.compatible and report.stage == "prepared"
        # Reusing every logical ID is intentional. API key public IDs are the
        # sole installation lookup exception and must remain globally unique.
        for collection, rows in originals.items():
            for old in rows:
                data = {key: value for key, value in old.items() if key != "_id"}
                if collection == "api_keys":
                    data["key_id"] = "b" * 16
                with tenant_scope(TenantContext("org-b")):
                    document = MODEL_BY_COLLECTION[collection].model_validate(data)
                await database[collection].insert_one(
                    document.model_dump(by_alias=True, mode="python")
                )
        await initialize_database(database, tenant_foundation=True, tenant_isolation=True)
        assert (await database["_tenant_control"].find_one({}))["domain_traffic_enabled"] is False
        for label in ("a", "b"):
            org_id = "org-" + label
            sentinel = "PRIVATE_SENTINEL_" + label.upper()
            # Same terms and vectors force both tenants into the candidate
            # universe. Only the server's candidate envelope may separate them.
            for name, field in (
                ("project_context", "content"),
                ("memories", "content"),
                ("skill_chunks", "content"),
                ("research_chunks", "content"),
                ("research_documents", "content"),
                ("content_items", "body"),
            ):
                await database[name].update_many(
                    {"org_id": org_id}, {"$set": {field: sentinel + " retry isolation"}}
                )
            await database["api_keys"].update_many(
                {"org_id": org_id},
                {
                    "$set": {
                        "key_hash": hash_secret(label * 40),
                        "scopes": list(ALL_SCOPES),
                        "expires_at": utc_now() + timedelta(days=1),
                    }
                },
            )
            await database["organizations"].update_one(
                {"org_id": org_id}, {"$set": {"status": "active"}}
            )
        # Explicit test cutover. Production activation is an operator command;
        # no HTTP selector can write control metadata or make an org active.
        await database["_tenant_control"].update_one(
            {"_id": "installation"},
            {
                "$set": {
                    "stage": "isolated",
                    "domain_traffic_enabled": True,
                    "worker_schema_versions": [37],
                }
            },
        )
        yield database
    finally:
        try:
            await client.drop_database(database.name)
        finally:
            # A failed disposable drop must still close its sockets. Keep the
            # original cleanup error visible to pytest and the release gate.
            await client.close()


def token(label):
    return "acp_" + label * 16 + "_" + label * 40
