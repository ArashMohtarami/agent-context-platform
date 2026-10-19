"""Recovery gates cover collection substitution and mounted-key loss, not mock success."""

import base64
import json

import pytest

from app.backup.access import require_operator
from app.backup.recovery import check_keys, key_commitments
from app.identity.crypto import AuthRejected, SeedVault
from app.security.tenant import TenantContext, tenant_scope
from app.security.tenant_policy import TenantPolicy, bind_policy, policy_request_boundary
from tests.unit.test_backup_service import fake_database, service_for


def keyring(path, byte=b"a"):
    path.write_text(
        json.dumps({"active": "one", "keys": {"one": base64.urlsafe_b64encode(byte * 32).decode()}})
    )
    path.chmod(0o600)
    return path


@pytest.mark.asyncio
async def test_same_total_cannot_mask_missing_management_collection(tmp_path):
    counts = {
        "global_users": 1,
        "organization_memberships": 3,
        "mfa_factors": 1,
        "policy_assignments": 2,
    }
    database = fake_database(
        counts, restores_as={**counts, "mfa_factors": 0, "policy_assignments": 3}
    )
    service, _ = service_for(tmp_path, database=database)
    stored = await service.create()
    verification = await service.verify(stored, restore=True)
    assert verification.restored_documents == verification.expected_documents == 7
    assert not verification.sound
    assert "per-collection" in " ".join(verification.failures)
    assert not any(name.startswith("restorecheck_") for name in database.counts)


def test_recovery_requires_actual_key_bytes_not_only_same_key_name(tmp_path, settings):
    first = keyring(tmp_path / "first")
    expected = key_commitments(first)
    configured = settings.model_copy(
        update={"v6_local_identity": True, "v6_mfa_keyring_file": first}
    )
    check_keys(configured, expected)
    value = SeedVault.from_file(first).encrypt("fictional-factor", "person", "factor")
    recovered = keyring(tmp_path / "recovered")
    assert SeedVault.from_file(recovered).decrypt(value, "person", "factor") == "fictional-factor"
    wrong = keyring(tmp_path / "wrong", b"b")
    with pytest.raises(ValueError, match="does not match"):
        check_keys(configured.model_copy(update={"v6_mfa_keyring_file": wrong}), expected)
    with pytest.raises(AuthRejected):
        SeedVault.from_file(wrong).decrypt(value, "person", "factor")
    assert base64.urlsafe_b64encode(b"a" * 32).decode() not in json.dumps(expected)


@pytest.mark.asyncio
async def test_org_policy_cannot_list_or_verify_installation_archives(tmp_path):
    service, _ = service_for(tmp_path, database=fake_database())
    stored = await service.create()
    with tenant_scope(TenantContext("org-a")), policy_request_boundary():
        bind_policy(TenantPolicy("org-a", "owner", (), managed=True))
        for operation in (
            require_operator,
            service.stored,
            service.latest,
            service.plan_prune,
            service.prune,
        ):
            with pytest.raises(ValueError, match="installation-operator-only"):
                operation()
        with pytest.raises(ValueError, match="installation-operator-only"):
            await service.verify(stored, restore=True)
        with pytest.raises(ValueError, match="installation-operator-only"):
            await service.create()
