"""Real disposable replica-set evidence. No mocks satisfy this selector."""

import os
from hashlib import sha256
from urllib.parse import urlsplit
from uuid import uuid4

import httpx
import pytest
import pytest_asyncio
from bson import BSON, ObjectId
from pymongo import AsyncMongoClient
from pymongo.errors import DuplicateKeyError, OperationFailure
from v6_fixtures import legacy_fixture
from v6_isolation_fixtures import archive as authenticated_archive

from app.db.initializer import initialize_database
from app.db.tenant_inventory import references
from app.db.tenant_schema import TENANT_COLLECTIONS
from app.main import create_app
from app.models.organization import OrganizationDocument
from app.repositories.organization_repository import MongoOrganizationRepository
from app.repositories.project_repository import MongoProjectRepository
from app.repositories.tenant_adoption_repository import (
    AdoptionConflict,
    MongoTenantAdoptionRepository,
)
from app.security.tenant import TenantContext, tenant_scope
from app.security.tenant_maintenance import check_operator_context
from app.services.tenant_adoption_service import TenantAdoptionService

pytestmark = pytest.mark.integration


@pytest_asyncio.fixture
async def disposable():
    uri = os.environ.get("V6_TEST_MONGODB_URI")
    if not uri:
        pytest.skip("explicit disposable V6_TEST_MONGODB_URI required; no integration evidence")
    assert urlsplit(uri).hostname == "127.0.0.1" and "directConnection=true" in uri
    client = AsyncMongoClient(uri, tz_aware=True, serverSelectionTimeoutMS=5000)
    hello = await client.admin.command("hello")
    assert hello.get("setName") and hello.get("isWritablePrimary")
    database = client["acp_v6_" + uuid4().hex]
    try:
        await initialize_database(database)
        assert await database["_schema_migrations"].count_documents({}) == 35
        originals = legacy_fixture()
        for collection, records in originals.items():
            await database[collection].insert_many(records)
        # BSON stores dates at millisecond precision. Compare adoption against
        # actual pre-V6 persisted bytes, not Python constructor defaults.
        originals = {name: await database[name].find({}).to_list(length=None) for name in originals}
        await initialize_database(database, tenant_foundation=True)
        await MongoOrganizationRepository(database).create(
            OrganizationDocument(org_id="org-a", slug="a", name="Explicit adoption org")
        )
        yield database, originals
    finally:
        await client.drop_database(database.name)
        await client.close()


async def backup(database, tmp_path):
    assert database.name.startswith("acp_v6_")
    archive, manifest = await authenticated_archive(database, tmp_path)
    return dict(
        org_id="org-a",
        adoption_id="adoption-one",
        archive=archive,
        manifest_path=manifest,
        writers_stopped=True,
        worker_schema_versions=[36],
    )


async def test_all_families_preserve_ids_authored_fields_actors_and_references(
    disposable, tmp_path
):
    database, originals = disposable
    repository = MongoTenantAdoptionRepository(database)
    service = TenantAdoptionService(repository)
    counts_before = await repository.counts()
    state_before = await repository.state()
    before = await service.dry_run(org_id="org-a", adoption_id="adoption-one")
    assert before.compatible, before.issues
    assert await repository.counts() == counts_before and await repository.state() == state_before
    options = await backup(database, tmp_path)
    report = await service.apply(**options)
    assert report.stage == "prepared" and report.compatible and not report.domain_traffic_enabled
    assert set(originals) == set(TENANT_COLLECTIONS)
    for collection, records in originals.items():
        assert await database[collection].count_documents({}) == len(records)
        for original in records:
            row = await database[collection].find_one({"_id": original["_id"]})
            assert {key: row[key] for key in original} == original
            assert row["org_id"] == "org-a" and row["identity_version"] == 1
            preserved = await repository.preservation(collection, original["_id"])
            assert (
                BSON(bytes(preserved["original"])).decode(codec_options=database.codec_options)
                == original
            )
            assert preserved["sha256"] == sha256(bytes(preserved["original"])).hexdigest()
            for reference in references(collection, row):
                assert (
                    await database[reference.target].count_documents(
                        {"$and": [reference.query, {"org_id": "org-a"}]}
                    )
                    == 1
                )
    assert await database["_tenant_preservation"].count_documents({}) == sum(
        map(len, originals.values())
    )
    key_manifest = await repository.preservation("api_keys", originals["api_keys"][0]["_id"])
    assert key_manifest["legacy_resource_modes"] == {
        "projects": "all_in_org",
        "profiles": "all_in_org",
    }
    repeated = await service.apply(**options)
    assert repeated.stage == "prepared" and repeated.compatible
    with pytest.raises(RuntimeError, match="maintenance binary"):
        await initialize_database(database)
    await initialize_database(database, tenant_foundation=True)
    assert await database["_schema_migrations"].count_documents({}) == 36
    assert "project_id_uq" not in await database["projects"].index_information()
    # Updates find the adopted natural key, retain its legacy ObjectId and scheme.
    with tenant_scope(TenantContext("org-a")):
        projects = MongoProjectRepository(database)
        adopted = await projects.get("payment-service")
        updated = await projects.update(
            adopted.model_copy(update={"name": "Updated"}), expected_revision=4
        )
        assert updated.id == originals["projects"][0]["_id"]
        assert updated.identity_version == 1 and updated.revision == 5
    assert (await service.apply(**options)).compatible


async def test_adopted_service_updates_keep_legacy_identity(disposable, tmp_path):
    from app.repositories.agent_repository import MongoAgentClientRepository
    from app.repositories.api_key_repository import MongoApiKeyRepository
    from app.repositories.client_profile_repository import MongoClientProfileRepository
    from app.repositories.content_repository import (
        MongoContentPillarRepository,
        MongoCreatorProfileRepository,
    )
    from app.repositories.context_repository import MongoProjectContextRepository
    from app.repositories.skill_repository import MongoSkillRepository
    from app.services.agent_client_service import AgentClientService
    from app.services.api_key_service import ApiKeyService
    from app.services.client_profile_service import ClientProfileService
    from app.services.context_service import ContextService
    from app.services.creator_service import CreatorService

    database, originals = disposable
    service = TenantAdoptionService(MongoTenantAdoptionRepository(database))
    options = await backup(database, tmp_path)
    await service.apply(**options)
    with tenant_scope(TenantContext("org-a")):
        clients = MongoAgentClientRepository(database)
        client = await AgentClientService(
            clients, ApiKeyService(MongoApiKeyRepository(database))
        ).update_client("agent-one", name="Updated agent", expected_revision=4)
        profile = await ClientProfileService(
            MongoClientProfileRepository(database), clients
        ).update_profile("template", name="Updated template", expected_revision=4)
        creator = await CreatorService(
            MongoCreatorProfileRepository(database),
            lambda profile_id: MongoContentPillarRepository(database, profile_id),
        ).update_profile("personal-brand", changes={"name": "Updated creator"}, expected_revision=4)
        context = await ContextService(
            MongoProjectRepository(database),
            lambda project_id: MongoProjectContextRepository(database, project_id),
        ).update_context(
            "payment-service",
            originals["project_context"][0]["_id"],
            content="Updated context",
            expected_revision=4,
        )
        skill = await MongoSkillRepository(database).set_status(
            "retry-skill", "1.0.0", "active", expected_revision=4
        )
        for name, updated in (
            ("agent_clients", client),
            ("client_profiles", profile),
            ("creator_profiles", creator),
            ("project_context", context),
            ("skills", skill),
        ):
            assert updated.id == originals[name][0]["_id"]
            assert updated.revision == 5 and updated.identity_version == 1
        await check_operator_context(database)
    assert (await service.apply(**options)).compatible


async def test_two_org_same_natural_keys_strict_constraints_and_new_scheme(
    disposable, tmp_path, settings
):
    from app.db.tenant_inventory import MODEL_BY_COLLECTION

    database, originals = disposable
    service = TenantAdoptionService(MongoTenantAdoptionRepository(database))
    options = await backup(database, tmp_path)
    await service.apply(**options)
    await MongoOrganizationRepository(database).create(
        OrganizationDocument(org_id="org-b", slug="b", name="B")
    )
    for collection in (
        "projects",
        "agent_clients",
        "client_profiles",
        "creator_profiles",
        "skills",
        "project_context",
        "memories",
    ):
        raw = {key: value for key, value in originals[collection][0].items() if key != "_id"}
        with tenant_scope(TenantContext("org-b")):
            fresh = MODEL_BY_COLLECTION[collection].model_validate(raw)
            row = fresh.model_dump(by_alias=True, mode="python")
            await database[collection].insert_one(row)
        assert fresh.id != originals[collection][0]["_id"] and fresh.identity_version == 2
        with pytest.raises(DuplicateKeyError):
            await database[collection].insert_one({**row, "_id": ObjectId()})
        for broken in (
            {key: value for key, value in row.items() if key != "org_id"},
            {**row, "org_id": None},
            {**row, "identity_version": 1, "legacy_adoption_id": "adoption-one"},
        ):
            with pytest.raises(OperationFailure) as failure:
                await database[collection].insert_one({**broken, "_id": ObjectId()})
            assert failure.value.code == 121
    # Frozen model_copy cannot escape ownership CAS on update.
    with tenant_scope(TenantContext("org-a")):
        repo = MongoProjectRepository(database)
        adopted = await repo.get("payment-service")
        with pytest.raises(ValueError):
            await repo.create(adopted)
        with pytest.raises(ValueError):
            await repo.update(adopted.model_copy(update={"org_id": "org-b"}), expected_revision=4)
        # The actual CLI binds the adoption org. Rechecking installation-wide
        # closure must still validate registered version 2 rows in a sealed org B.
        assert (await service.apply(**options)).compatible
    app = create_app()
    app.state.settings = settings
    app.state.tenant_control_database = database
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        for path in ("/api/projects", "/api/v1/projects", "/mcp"):
            assert (await client.get(path)).status_code == 503
        # Raw installation metrics have a separate operator gate even while
        # tenant domain traffic is sealed. No tenant key grants this endpoint.
        assert (await client.get("/metrics")).status_code == 403
        assert (await client.get("/dashboard")).status_code == 404


async def test_transaction_rollback_and_interrupted_resume(disposable, tmp_path):
    database, originals = disposable
    options = await backup(database, tmp_path)

    class Interrupted(MongoTenantAdoptionRepository):
        committed = 0

        async def _adopt_write(self, collection, raw, changes, session):
            if self.committed == 3:
                raise RuntimeError("injected interruption after preservation insert")
            await super()._adopt_write(collection, raw, changes, session)
            self.committed += 1

    with pytest.raises(RuntimeError, match="injected interruption"):
        await TenantAdoptionService(Interrupted(database)).apply(**options)
    assert await database["_tenant_preservation"].count_documents({}) == 3
    assert (
        sum(
            [await database[c].count_documents({"identity_version": 1}) for c in TENANT_COLLECTIONS]
        )
        == 3
    )
    report = await TenantAdoptionService(MongoTenantAdoptionRepository(database)).apply(**options)
    assert report.stage == "prepared" and report.compatible
    assert await database["_tenant_preservation"].count_documents({}) == sum(
        map(len, originals.values())
    )


@pytest.mark.parametrize("bad_owner", [None, "org-b"])
async def test_unknown_and_conflicting_ownership_is_preserved_and_blocks(
    disposable, tmp_path, bad_owner
):
    database, originals = disposable
    unknown = {"_id": ObjectId(), "opaque": {"authored": "keep every byte"}, "org_id": bad_owner}
    await database["unclassified_import"].insert_one(unknown)
    # Simulate a damaged operator import, outside validators, before migration.
    damaged = {**originals["project_context"][0], "org_id": bad_owner}
    await database["project_context"].replace_one(
        {"_id": damaged["_id"]}, damaged, bypass_document_validation=True
    )
    service = TenantAdoptionService(MongoTenantAdoptionRepository(database))
    options = await backup(database, tmp_path)
    report = await service.apply(**options)
    assert report.stage == "blocked" and not report.compatible and report.unresolved_quarantine >= 2
    assert await database["unclassified_import"].count_documents({}) == 0
    row = await database["_tenant_quarantine"].find_one({"collection": "unclassified_import"})
    assert BSON(bytes(row["original"])).decode() == unknown and row["status"] == "unresolved"
    repeated = await service.apply(**options)
    assert repeated.stage == "blocked" and repeated.unresolved_quarantine >= 2
    assert not (await database["_tenant_control"].find_one({}))["domain_traffic_enabled"]


async def test_backup_and_old_worker_checks_refuse_before_domain_mutation(disposable, tmp_path):
    database, _ = disposable
    options = await backup(database, tmp_path)
    service = TenantAdoptionService(MongoTenantAdoptionRepository(database))
    for versions in ([], [35], [36, 35]):
        with pytest.raises(AdoptionConflict):
            await service.apply(**{**options, "worker_schema_versions": versions})
    with pytest.raises(AdoptionConflict):
        await service.apply(**{**options, "writers_stopped": False})
    options["archive"].write_bytes(b"damaged archive")
    with pytest.raises(AdoptionConflict):
        await service.apply(**options)
    assert await database["projects"].count_documents({"org_id": {"$exists": True}}) == 0
    with pytest.raises(ValueError, match="explicit --org-id"):
        await check_operator_context(database)


async def test_missing_preserved_record_cannot_prepare_without_reference(disposable, tmp_path):
    database, originals = disposable
    options = await backup(database, tmp_path)

    class Disappearing(MongoTenantAdoptionRepository):
        async def preserve_and_adopt(self, collection, raw, org_id, adoption_id, lease):
            await super().preserve_and_adopt(collection, raw, org_id, adoption_id, lease)
            if collection == "audit_logs":
                await self.database[collection].delete_one({"_id": raw["_id"]})

    report = await TenantAdoptionService(Disappearing(database)).apply(**options)
    assert report.stage == "blocked" and not report.compatible
    assert any(issue.code == "preserved_source_missing" for issue in report.issues)
    preserved = await MongoTenantAdoptionRepository(database).preservation(
        "audit_logs", originals["audit_logs"][0]["_id"]
    )
    assert preserved is not None and not report.domain_traffic_enabled


async def test_pre_v6_reserved_names_do_not_infer_metadata_ownership(disposable):
    from app.db.migrations.v036_tenant_foundation import TenantFoundationMigration

    database, _ = disposable
    # Simulate a pre-V6 import that happened to use a future reserved spelling.
    for name in ("organizations", "_tenant_control", "_tenant_preservation", "_tenant_quarantine"):
        await database.drop_collection(name)
    await database["_schema_migrations"].delete_one({"version": 36})
    unknown = {
        "_id": ObjectId(),
        "email": "no-owner-inference@example.test",
        "name": "Keep this data",
    }
    await database["organizations"].insert_one(unknown)
    report = await TenantAdoptionService(MongoTenantAdoptionRepository(database)).dry_run(
        org_id="org-a", adoption_id="adoption-one"
    )
    assert any(
        issue.collection == "organizations" and issue.code == "unknown_collection"
        for issue in report.issues
    )
    with pytest.raises(AdoptionConflict, match="unclassified legacy data"):
        await TenantFoundationMigration().up(database)
    assert await database["organizations"].find_one({}) == unknown
    assert await database["_tenant_control"].count_documents({}) == 0


async def test_model_defaults_cannot_hide_invalid_persisted_legacy_rows(disposable, tmp_path):
    database, originals = disposable
    missing_revision = {
        key: value for key, value in originals["audit_logs"][0].items() if key != "revision"
    }
    await database["audit_logs"].replace_one(
        {"_id": missing_revision["_id"]}, missing_revision, bypass_document_validation=True
    )
    service = TenantAdoptionService(MongoTenantAdoptionRepository(database))
    report = await service.apply(**await backup(database, tmp_path))
    assert report.stage == "blocked" and not report.compatible
    preserved = await database["_tenant_quarantine"].find_one({"collection": "audit_logs"})
    assert preserved["reason"] == "invalid_persisted_schema"
    assert (
        BSON(bytes(preserved["original"])).decode(codec_options=database.codec_options)
        == missing_revision
    )
