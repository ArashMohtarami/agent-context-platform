"""Target policy is independent of current membership and authentication claims."""

from datetime import timedelta
from unittest.mock import AsyncMock, Mock

import pytest
from unit.test_v6_oidc import registration

from app.identity.crypto import AuthRejected
from app.identity.org_policy import check_policy
from app.identity.providers import Registry
from app.identity.sso import policy_context
from app.models.common import utc_now


def test_policy_context_changes_on_org_session_membership_or_policy_rotation():
    row = {
        "org_id": "org-a",
        "public_id": "sess-one",
        "membership_revision": 1,
        "policy_version": 1,
    }
    old = policy_context(row)
    for key, value in (
        ("org_id", "org-b"),
        ("public_id", "sess-two"),
        ("membership_revision", 2),
        ("policy_version", 2),
    ):
        assert old["context_id"] != policy_context({**row, key: value})["context_id"]


@pytest.mark.parametrize("policy", ["local_only", "sso_preferred"])
async def test_local_authentication_is_admitted_only_by_explicit_local_policy(policy):
    await check_policy(Mock(), None, {}, {"org_id": "org-a", "auth_policy": policy})


async def test_local_cannot_enter_required_without_scoped_operator_exception():
    store = Mock(find=AsyncMock(return_value=None))
    row = {"user_id": "user-one", "mfa_at": utc_now(), "membership_revision": 1}
    org = {"org_id": "org-a", "auth_policy": "sso_required", "policy_version": 2}
    registry = Registry([registration()], "https://identity.example")
    with pytest.raises(AuthRejected):
        await check_policy(store, registry, row, org)
    query = store.find.call_args.args[1]
    assert query["org_id"] == "org-a" and query["user_id"] == "user-one"
    assert query["policy_version"] == 2 and query["membership_revision"] == 1
    assert "$gt" in query["expires_at"]
    store.find.return_value = {"approval_reference": "explicit-operator-approval"}
    await check_policy(store, registry, row, org)
    with pytest.raises(AuthRejected):
        await check_policy(store, registry, {**row, "mfa_at": None}, org)


@pytest.mark.parametrize(
    "change",
    [
        {"registration_id": "provider-b"},
        {"issuer": "https://other.example"},
        {"client_id": "other-client"},
        {"registration_version": "stale"},
        {"assurance_version": 0},
        {"valid_until": utc_now() - timedelta(seconds=1)},
    ],
)
async def test_foreign_provider_registration_or_stale_assurance_cannot_enter_required(change):
    reg = registration()
    registry = Registry([reg], "https://identity.example")
    evidence = {
        "method": "oidc",
        "identity_id": "identity-one",
        "registration_id": reg.registration_id,
        "registration_version": reg.version,
        "issuer": reg.issuer,
        "client_id": reg.client_id,
        "assurance_version": reg.assurance_version,
        "valid_until": utc_now() + timedelta(minutes=1),
        "acr": None,
        "verified_amr": [],
        **change,
    }
    store = Mock(
        find=AsyncMock(
            return_value={
                "registration_id": reg.registration_id,
                "registration_version": reg.version,
            }
        )
    )
    with pytest.raises(AuthRejected):
        await check_policy(
            store,
            registry,
            {"authentication": evidence, "user_id": "user-one"},
            {"org_id": "org-a", "auth_policy": "sso_required"},
        )


async def test_identity_link_record_is_rechecked_even_for_valid_provider_evidence():
    reg = registration()
    evidence = {
        "method": "oidc",
        "identity_id": "identity-one",
        "registration_id": reg.registration_id,
        "registration_version": reg.version,
        "issuer": reg.issuer,
        "client_id": reg.client_id,
        "assurance_version": reg.assurance_version,
        "valid_until": utc_now() + timedelta(minutes=1),
        "acr": None,
        "verified_amr": [],
    }
    store = Mock(
        find=AsyncMock(
            side_effect=[
                {"registration_id": reg.registration_id, "registration_version": reg.version},
                None,
            ]
        )
    )
    with pytest.raises(AuthRejected):
        await check_policy(
            store,
            Registry([reg], "https://identity.example"),
            {"authentication": evidence, "user_id": "user-one"},
            {"org_id": "org-a", "auth_policy": "sso_required"},
        )
    assert store.find.call_args.args[1] == {
        "_id": "identity-one",
        "user_id": "user-one",
        "provider": reg.issuer,
        "verified_at": {"$ne": None},
    }
