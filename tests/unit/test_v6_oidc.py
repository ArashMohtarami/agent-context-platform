"""Protocol validation tests complement, and never replace, real provider evidence."""

from datetime import timedelta
from unittest.mock import AsyncMock, Mock

import httpx
import pytest
from joserfc import jwk, jwt
from pydantic import ValidationError

from app.identity.crypto import AuthRejected, SeedVault, digest, random_token
from app.identity.oidc import OIDCClient, provider_mfa, validate_id_token
from app.identity.providers import Registration, Registry
from app.identity.repository import COLLECTION_OWNERSHIP, LOCAL_COLLECTION_OWNERSHIP
from app.identity.schema import VALIDATORS
from app.identity.sso import SSOIdentity
from app.identity.sso_schema import NEW_VALIDATORS
from app.models.common import utc_now


def registration(**changes):
    endpoint = "https://provider.example/realms/a/protocol/openid-connect/"
    return Registration(
        **{
            "registration_id": "provider-a",
            "org_id": "org-a",
            "issuer": "https://provider.example/realms/a",
            "client_id": "acp-a",
            "client_secret": "synthetic-disposable-client-secret",
            "redirect_uri": "https://identity.example/api/v6/auth/oidc/callback",
            "post_logout_redirect_uri": "https://identity.example/api/v6/auth/oidc/logged-out",
            "authorization_endpoint": endpoint + "auth",
            "token_endpoint": endpoint + "token",
            "jwks_uri": endpoint + "certs",
            "end_session_endpoint": endpoint + "logout",
            "connect_ip": "203.0.113.10",
            **changes,
        }
    )


@pytest.fixture(scope="module")
def signing_key():
    return jwk.RSAKey.generate_key(2048, {"kid": "test-signing"})


def claims(**changes):
    now = int(utc_now().timestamp())
    return {
        "iss": "https://provider.example/realms/a",
        "sub": "exact-subject",
        "aud": "acp-a",
        "azp": "acp-a",
        "nonce": "test-nonce",
        "exp": now + 300,
        "iat": now,
        "auth_time": now,
        **changes,
    }


def encoded(key, values):
    return jwt.encode({"alg": "RS256", "kid": "test-signing"}, values, key)


def test_valid_oidc_claims_are_verified_by_maintained_library(signing_key):
    keyset = {"keys": [signing_key.as_dict(private=False)]}
    result = validate_id_token(
        encoded(signing_key, claims()), keyset, registration(), "test-nonce", ""
    )
    assert result["sub"] == "exact-subject"


@pytest.mark.parametrize(
    "changes",
    [
        {"iss": "https://foreign.example"},
        {"aud": "foreign"},
        {"azp": "foreign"},
        {"aud": ["acp-a", "foreign"], "azp": None},
        {"nonce": "substituted"},
        {"exp": 1},
        {"iat": 9999999999},
        {"auth_time": 1},
        {"auth_time": True},
        {"sub": ""},
        {"amr": "mfa"},
        {"acr": ["mfa"]},
    ],
)
def test_oidc_rejects_claim_substitution(signing_key, changes):
    with pytest.raises(AuthRejected):
        validate_id_token(
            encoded(signing_key, claims(**changes)),
            {"keys": [signing_key.as_dict(private=False)]},
            registration(),
            "test-nonce",
            "",
        )


def test_oidc_rejects_foreign_signature_and_symmetric_algorithm(signing_key):
    foreign = jwk.RSAKey.generate_key(2048, {"kid": "test-signing"})
    keyset = {"keys": [signing_key.as_dict(private=False)]}
    for raw in (
        encoded(foreign, claims()),
        jwt.encode({"alg": "HS256"}, claims(), jwk.OctKey.generate_key(256)),
    ):
        with pytest.raises(AuthRejected):
            validate_id_token(raw, keyset, registration(), "test-nonce", "")


def test_unconfigured_or_partial_assurance_cannot_authorize_mfa():
    claims = {"acr": "urn:trusted:2", "amr": ["pwd", "otp"], "roles": ["admin"]}
    assert not provider_mfa(registration(), claims)
    approved = registration(mfa_acr=["urn:trusted:2"], mfa_amr=["otp"], require_mfa=True)
    assert provider_mfa(approved, claims)
    assert not provider_mfa(approved, {**claims, "amr": ["pwd"]})
    assert not provider_mfa(approved, {**claims, "acr": "untrusted"})


def test_pkce_ciphertext_is_bound_to_purpose_transaction_and_organization():
    vault = SeedVault("test", {"test": b"x" * 32})
    value = vault.encrypt("synthetic-verifier", "state-hash", "org-a", purpose="oidc-pkce")
    assert vault.decrypt(value, "state-hash", "org-a", purpose="oidc-pkce") == "synthetic-verifier"
    for transaction, org, purpose in (
        ("other", "org-a", "oidc-pkce"),
        ("state-hash", "org-b", "oidc-pkce"),
        ("state-hash", "org-a", "mfa"),
    ):
        with pytest.raises(AuthRejected):
            vault.decrypt(value, transaction, org, purpose=purpose)


@pytest.mark.parametrize(
    "changes",
    [
        {"issuer": "http://provider.example"},
        {"token_endpoint": "https://foreign.example/token"},
        {"jwks_uri": "https://provider.example/certs?url=internal"},
        {"connect_ip": "169.254.169.254"},
        {"connect_ip": "0.0.0.0"},  # noqa: S104 - rejected address fixture
        {"algorithms": ["HS256"]},
        {"require_mfa": True},
    ],
)
def test_operator_registrations_bound_endpoints_algorithms_and_assurance(changes):
    with pytest.raises(ValidationError):
        registration(**changes)


async def test_pinned_egress_rejects_unapproved_urls_and_bounds_responses():
    transport = registration().transport()
    try:
        for url in ("https://provider.example/arbitrary", "http://provider.example/token"):
            with pytest.raises(AuthRejected):
                await transport.handle_async_request(httpx.Request("GET", url))
        transport.inner = Mock(
            handle_async_request=AsyncMock(return_value=httpx.Response(200, content=b"x" * 131073)),
            aclose=AsyncMock(),
        )
        with pytest.raises(AuthRejected):
            await transport.handle_async_request(httpx.Request("GET", registration().jwks_uri))
        sent = transport.inner.handle_async_request.call_args.args[0]
        assert sent.url.host == "203.0.113.10"
        assert sent.headers["host"] == "provider.example"
        assert sent.extensions["sni_hostname"] == "provider.example"
    finally:
        await transport.aclose()


async def test_discovery_endpoint_substitution_is_refused():
    client = Mock(
        request=AsyncMock(
            return_value=httpx.Response(200, json={"issuer": "https://foreign.example"})
        )
    )
    with pytest.raises(AuthRejected):
        await OIDCClient().discovery(client, registration())


async def test_state_is_browser_purpose_expiry_bound_and_consumed_before_exchange():
    local = Mock(
        store=Mock(
            find=AsyncMock(return_value={"schema_version": 39}), update=AsyncMock(return_value=None)
        )
    )
    service = SSOIdentity(
        local,
        Registry([registration()], "https://identity.example"),
        SeedVault("test", {"test": b"x" * 32}),
    )
    service.client.exchange = AsyncMock()
    state, token = random_token(), random_token()
    with pytest.raises(AuthRejected):
        await service.callback(state, "code", token)
    name, query, change = local.store.update.call_args.args
    assert name == "oidc_transactions" and query["_id"] == digest("oidc-state", state)
    assert query["browser_id"] == digest("oidc-browser", token)
    assert query["used_at"] is None and query["expires_at"]["$gt"] > utc_now() - timedelta(
        seconds=2
    )
    assert "logout" not in query["purpose"]["$in"]
    assert change["$set"]["used_at"]
    service.client.exchange.assert_not_called()
    assert state not in repr(local.store.update.call_args)


def test_collection_and_schema_inventory_remains_explicit():
    from app.db.migrations.runner import MIGRATIONS

    assert set(LOCAL_COLLECTION_OWNERSHIP) == set(VALIDATORS)
    from app.security.policy.schema import NEW_VALIDATORS as POLICY_VALIDATORS

    assert set(COLLECTION_OWNERSHIP) == set(VALIDATORS) | set(NEW_VALIDATORS) | set(
        POLICY_VALIDATORS
    )
    assert MIGRATIONS[-1].version == 40
    assert MIGRATIONS[-2].version == 39


def test_registration_file_failures_and_oauth_logs_do_not_echo_secrets(tmp_path):
    from app.core.logging import _redact_log_text

    secret = "synthetic-client-secret-must-not-appear"
    path = tmp_path / "registration.json"
    path.write_text('{"client_secret":"' + secret + '"}')
    with pytest.raises(ValueError) as error:
        Registry.from_file(path, "https://identity.example")
    assert secret not in str(error.value)
    for field in ("code", "state", "nonce", "code_verifier", "id_token", "client_secret", "cookie"):
        assert secret not in _redact_log_text(f"https://identity.example/callback?{field}={secret}")


def test_mounted_trust_root_changes_invalidate_approved_registration_version(tmp_path):
    path = tmp_path / "ca.pem"
    path.write_bytes(b"synthetic-trust-root-one")
    reg = registration(ca_file=path)
    before = reg.version
    path.write_bytes(b"synthetic-trust-root-two")
    assert reg.version != before


async def test_actual_authlib_adapter_builds_code_pkce_nonce_and_fresh_login(monkeypatch):
    reg = registration()
    metadata = {
        key: getattr(reg, key)
        for key in (
            "issuer",
            "authorization_endpoint",
            "token_endpoint",
            "jwks_uri",
            "end_session_endpoint",
        )
    }
    metadata["code_challenge_methods_supported"] = ["S256"]
    metadata["response_types_supported"] = ["code"]
    metadata["grant_types_supported"] = ["authorization_code"]
    metadata["token_endpoint_auth_methods_supported"] = ["client_secret_basic"]
    metadata["id_token_signing_alg_values_supported"] = ["RS256"]
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json=metadata))
    monkeypatch.setattr(Registration, "transport", lambda _: transport)
    from urllib.parse import parse_qs, urlsplit

    url = await OIDCClient().start(reg, "state", "nonce", "x" * 43)
    query = parse_qs(urlsplit(url).query)
    assert query["response_type"] == ["code"]
    assert query["code_challenge_method"] == ["S256"] and "code_verifier" not in query
    assert query["max_age"] == ["0"] and query["prompt"] == ["login"]
    assert query["nonce"] == ["nonce"] and query["state"] == ["state"]
    assert query["redirect_uri"] == [reg.redirect_uri]
