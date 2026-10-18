"""Whole-installation recovery on the disposable replica set, including encrypted identity."""

import base64
import json
import os
import shutil
from urllib.parse import urlsplit

import pytest
from bson import BSON
from pydantic import SecretStr

from app.backup.tools import MongoTools
from app.core.config import Settings
from app.db.initializer import initialize_database
from app.identity.crypto import SeedVault
from app.repositories.backup_repository import MongoBackupRepository
from app.security.tenant import TenantContext, tenant_scope
from app.services.backup_service import BackupError, BackupService
from app.services.tenant_isolation_service import TenantIsolationService
from tests.v6_deployment_fixtures import docker

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


async def test_all_management_state_restores_with_exact_bson_and_recovered_key(
    managed_agents_db, tmp_path, monkeypatch
):
    h = managed_agents_db
    container = os.environ["V6_TEST_MONGODB_CONTAINER"]
    assert container.startswith("acp-v6-phase2-mongodb-")
    tools = tmp_path / "tools"
    tools.mkdir()
    for name in ("mongodump", "mongorestore"):
        await docker("cp", container + ":/usr/bin/" + name, str(tools / name))
    monkeypatch.setenv("PATH", str(tools) + os.pathsep + os.environ["PATH"])
    uri = urlsplit(os.environ["V6_TEST_MONGODB_URI"])
    password = "disposable-backup-operator-password"
    await h.db.command(
        "createUser",
        "v6-backup-test",
        pwd=password,
        roles=[{"role": "readWrite", "db": h.db.name}, {"role": "dbAdmin", "db": h.db.name}],
    )
    ring = tmp_path / "keyring.json"
    ring.write_text(
        json.dumps(
            {"active": "fixture", "keys": {"fixture": base64.urlsafe_b64encode(b"p" * 32).decode()}}
        )
    )
    ring.chmod(0o600)
    async with h.store.transaction() as tx:
        seed, _ = await h.local.mfa.begin("owner", tx)
        await h.local.mfa.recovery_codes("owner", tx, activate=True)
    names = await h.db.list_collection_names()
    before = {name: await h.db[name].find({}).to_list() for name in names}
    assert before["users"] and before["organization_memberships"] and before["policy_assignments"]
    assert before["_tenant_preservation"] and before["governance_events"] and before["mfa_factors"]
    captured = False

    class ExactRecovery(MongoBackupRepository):
        async def collection_counts(self):
            nonlocal captured
            counts = await super().collection_counts()
            restored = [name for name in counts if name.startswith("restorecheck_")]
            if restored:
                captured = True
                for name in restored:
                    source = name.split("__", 1)[1]
                    documents = await h.db[name].find({}).to_list()

                    def encode(rows):
                        return sorted(BSON.encode(row) for row in rows)

                    assert encode(documents) == encode(before[source]), source
                    if source == "mfa_factors":
                        factor = next(row for row in documents if row["_id"] == "owner")
                        recovered = tmp_path / "recovered-keyring.json"
                        shutil.copyfile(ring, recovered)
                        vault = SeedVault.from_file(recovered)
                        assert (
                            vault.decrypt(factor["encrypted_seed"], "owner", factor["factor_id"])
                            == seed
                        )
            return counts

    settings = Settings(
        _env_file=None,
        mongodb_uri=f"mongodb://{uri.hostname}:{uri.port}/?directConnection=true",
        mongodb_database=h.db.name,
        mongodb_username="v6-backup-test",
        mongodb_password=SecretStr(password),
        mongodb_replica_set="acp_v6_phase2",
        backup_directory=tmp_path / "archives",
    ).model_copy(update={"v6_local_identity": True, "v6_mfa_keyring_file": ring})
    service = BackupService(settings, MongoTools(settings), repository=ExactRecovery(h.db))
    stored = await service.create(prune=False)
    assert set(stored.manifest.collections) == set(names)
    assert stored.archive.stat().st_mode & 0o777 == 0o600
    assert ring.read_text() not in stored.manifest.model_dump_json()
    result = await service.verify(stored, restore=True)
    assert result.sound, result.failures
    assert captured and result.expected_documents == result.restored_documents
    assert not any(name.startswith("restorecheck_") for name in await h.db.list_collection_names())
    wrong = tmp_path / "wrong.json"
    wrong.write_text(
        json.dumps(
            {"active": "fixture", "keys": {"fixture": base64.urlsafe_b64encode(b"w" * 32).decode()}}
        )
    )
    bad_settings = settings.model_copy(update={"v6_mfa_keyring_file": wrong})
    refused = await BackupService(
        bad_settings, MongoTools(bad_settings), repository=ExactRecovery(h.db)
    ).verify(stored, restore=True)
    assert not refused.sound and "encryption keys" in " ".join(refused.failures)

    await h.db.mfa_factors.update_one(
        {"_id": "owner"}, {"$set": {"encrypted_seed.key_id": "retired-missing"}}
    )
    with pytest.raises(BackupError, match="missing keys referenced"):
        await service.create(prune=False)


async def test_maintenance_adoption_refuses_mixed_workers_and_preserves_seal(managed_agents_db):
    h = managed_agents_db
    assert await h.db["_tenant_preservation"].count_documents({}) > 0
    control = await h.db["_tenant_control"].find_one({"_id": "installation"})
    assert control["stage"] == "managed" and control["worker_schema_versions"] == [42]
    await h.db.organizations.update_one({"org_id": "org-b"}, {"$set": {"status": "sealed"}})
    with tenant_scope(TenantContext("org-a")):
        with pytest.raises(ValueError, match="stop all writers"):
            await TenantIsolationService(h.db).prepare(
                writers_stopped=True, worker_schema_versions=[36, 42]
            )
    with pytest.raises(RuntimeError):
        await initialize_database(h.db, tenant_foundation=True)
    assert (await h.db.organizations.find_one({"org_id": "org-b"}))["status"] == "sealed"
    assert await h.db["_tenant_control"].find_one({"_id": "installation"}) == control


async def test_optional_provider_has_its_own_recoverable_postgresql_state():
    run = os.environ["V6_TEST_RUN"]
    container = "acp-v6-oidc-test-" + run + "-keycloak-db-1"
    assert container.startswith("acp-v6-oidc-test-")
    scratch = (await docker("exec", container, "mktemp", "-d")).strip()
    assert scratch.startswith("/tmp/tmp.")  # noqa: S108 - checking mktemp-created private path.
    archive = scratch + "/provider.dump"
    query = (
        "select json_build_object('realms',(select json_agg(name order by name) from realm),"
        "'users',(select count(*) from user_entity),'clients',(select count(*) from client),"
        "'credentials',(select count(*) from credential),"
        "'components',(select count(*) from component))"
    )
    source = json.loads(
        await docker("exec", container, "psql", "-U", "keycloak", "-d", "keycloak", "-Atc", query)
    )
    assert {"master", "v6-org-a", "v6-org-b"} <= set(source["realms"])
    try:
        await docker(
            "exec",
            container,
            "pg_dump",
            "-U",
            "keycloak",
            "-d",
            "keycloak",
            "-Fc",
            "-f",
            archive,
        )
        await docker("exec", container, "createdb", "-U", "keycloak", "keycloak_recovery")
        await docker(
            "exec",
            container,
            "pg_restore",
            "-U",
            "keycloak",
            "-d",
            "keycloak_recovery",
            "--exit-on-error",
            archive,
        )
        restored = json.loads(
            await docker(
                "exec",
                container,
                "psql",
                "-U",
                "keycloak",
                "-d",
                "keycloak_recovery",
                "-Atc",
                query,
            )
        )
        assert restored == source
    finally:
        await docker(
            "exec", container, "dropdb", "-U", "keycloak", "--if-exists", "keycloak_recovery"
        )
        await docker("exec", container, "rm", "-rf", scratch)
