"""Compatibility evidence: actual Keycloak TLS discovery, code exchange and logout."""

from datetime import timedelta
from urllib.parse import parse_qs, urlencode, urlsplit
from uuid import uuid4

import pytest
from pymongo.errors import OperationFailure
from v6_oidc_fixtures import Form

from app.db.initializer import initialize_database
from app.db.migrations.v039_sso_identity import SSOIdentityMigration
from app.identity.browser import SESSION_COOKIE
from app.identity.crypto import AuthRejected, digest
from app.models.common import utc_now

pytestmark = pytest.mark.integration


async def test_real_provider_link_login_assurance_and_rp_logout(sso, caplog):
    h = sso
    await h.setup_user()
    await h.configure()
    await h.local_login()
    old = h.client.cookies.get(SESSION_COOKIE)
    linked = await h.link()
    assert linked.json() == {"linked": True, "reauthentication_required": True}
    with pytest.raises(AuthRejected):
        await h.local.sessions.authenticate(old)
    identity = await h.db.user_identities.find_one(
        {"provider": h.sso.registry.get("provider-a").issuer}
    )
    assert identity["subject"] == "oidc-alice-a" and identity["user_id"] == h.user_id
    result = await h.oidc_login()
    assert result.json()["policy_context"]["org_id"] == "org-a"
    row = await h.row()
    evidence = row["authentication"]
    assert evidence["issuer"] == h.sso.registry.get("provider-a").issuer
    assert evidence["identity_id"] == identity["_id"] and evidence["client_id"] == "acp-a"
    assert evidence["auth_time"] and evidence["assurance_version"] == 1
    assert row["mfa_at"] is None  # default Keycloak password/acr cannot approve admin actions.
    me = (await h.client.get("/api/v6/me")).json()
    assert me["assurance"] == "authenticated" and me["auth_method"] == "oidc"
    assert me["mfa_enrollment_required"] is False and me["step_up_required"] is True
    assert me["effective_corpus_access"] == []
    await h.post("auth/mfa/recovery", {"code": "000000"}, 401)
    stepped = await h.post("auth/step-up", {"code": h.codes.pop(), "recovery": True})
    assert stepped.status_code == 200 and (await h.row())["mfa_at"]
    cookie = h.client.cookies.get(SESSION_COOKIE)
    logout = await h.post("auth/oidc/logout")
    assert not h.client.cookies.get(SESSION_COOKIE)
    with pytest.raises(AuthRejected):
        await h.local.sessions.authenticate(cookie)
    returned = await h.authorize(logout.json()["end_session_url"])
    assert urlsplit(returned).path == "/api/v6/auth/oidc/logged-out"
    assert (await h.callback(returned)).json() == {"logged_out": True}
    await h.callback(returned, 401)
    assert "access_token" not in repr(await h.db.browser_sessions.find({}).to_list())
    assert "disposable-provider-password" not in caplog.text


async def test_equal_verified_email_never_creates_or_links_an_identity(sso):
    h = sso
    await h.setup_user()
    await h.configure()
    before = await h.db.user_identities.count_documents({})
    await h.oidc_login(expected=401)  # Same alice@example.test at both ends, no exact link.
    assert await h.db.user_identities.count_documents({}) == before
    assert await h.db.users.count_documents({}) == 1


async def test_real_code_state_pkce_replay_and_browser_substitution_fail(sso):
    h = sso
    await h.setup_user()
    await h.configure()
    await h.public()
    urls = []
    for _ in range(2):
        started = await h.post("auth/oidc/start", {"org_id": "org-a"})
        query = parse_qs(urlsplit(started.json()["authorization_url"]).query)
        assert query["code_challenge_method"] == ["S256"] and "code_verifier" not in query
        urls.append(await h.authorize(started.json()["authorization_url"]))
    a, b = [parse_qs(urlsplit(url).query) for url in urls]
    foreign_issuer = {key: values[0] for key, values in a.items()}
    foreign_issuer["iss"] = h.sso.registry.get("provider-b").issuer
    await h.callback("/api/v6/auth/oidc/callback?" + urlencode(foreign_issuer), 401)
    foreign_browser = h.client.cookies.get("__Host-acp-login")
    h.client.cookies.clear()
    await h.callback(urls[0], 401)
    h.client.cookies.set(
        "__Host-acp-login", foreign_browser, domain=h.client.base_url.host, path="/"
    )
    swapped = "/api/v6/auth/oidc/callback?state=" + b["state"][0] + "&code=" + a["code"][0]
    await h.callback(swapped, 401)
    await h.callback(urls[1], 401)  # Consumed state cannot retry with the correct code.
    flow = await h.db.oidc_transactions.find_one({"_id": digest("oidc-state", b["state"][0])})
    assert flow["used_at"] and "code_verifier" not in flow
    assert "code" not in flow and "access_token" not in flow
    started = await h.post("auth/oidc/start", {"org_id": "org-a"})
    expired = await h.authorize(started.json()["authorization_url"])
    state = parse_qs(urlsplit(expired).query)["state"][0]
    await h.db.oidc_transactions.update_one(
        {"_id": digest("oidc-state", state)},
        {"$set": {"expires_at": utc_now() - timedelta(seconds=1)}},
    )
    await h.callback(expired, 401)


async def test_link_requires_both_identities_and_cannot_take_an_existing_link(sso):
    h = sso
    await h.setup_user()
    await h.configure()
    await h.local_login()
    foreign_user = "user_" + uuid4().hex
    await h.db.users.insert_one(
        {
            "_id": foreign_user,
            "display_name": "Other",
            "status": "active",
            "auth_epoch": 1,
            "fence": 0,
            "created_at": utc_now(),
        }
    )
    await h.db.user_identities.insert_one(
        {
            "_id": uuid4().hex,
            "user_id": foreign_user,
            "provider": h.sso.registry.get("provider-a").issuer,
            "subject": "oidc-other-a",
            "verified_at": utc_now(),
            "created_at": utc_now(),
        }
    )
    await h.link(username="other", expected=401)
    assert (await h.row())["user_id"] == h.user_id
    linked = await h.db.user_identities.find_one({"subject": "oidc-other-a"})
    assert linked["user_id"] == foreign_user
    # An ordinary provider proof without trusted/local MFA cannot start a link.
    h.provider.cookies.clear()  # Explicitly select Alice after proving the other account.
    await h.link()
    await h.oidc_login()
    await h.post("auth/identities/link", {"org_id": "org-a"}, 401)


async def test_actual_idp_disable_is_bounded_and_app_suspension_is_immediate(sso):
    h = sso
    await h.setup_user()
    await h.configure()
    await h.local_login()
    await h.link()
    await h.oidc_login()
    row = await h.row()
    assert (
        await h.admin("PUT", "v6-org-a/users/oidc-alice-a", {"enabled": False})
    ).status_code == 204
    try:
        # No SCIM/backchannel integration: disabling IdP account is not an instant app logout.
        assert (await h.client.get("/api/v6/me")).status_code == 200
        await h.db.organization_memberships.update_one(
            {"_id": row["membership_id"]},
            {"$set": {"status": "suspended"}, "$inc": {"revision": 1}},
        )
        assert (await h.client.get("/api/v6/me")).status_code == 401
        await h.public()
        start = await h.post("auth/oidc/start", {"org_id": "org-a"})
        response = await h.provider.get(start.json()["authorization_url"])
        form = Form(response.text)
        response = await h.provider.post(
            form.action,
            data={**form.fields, "username": "alice", "password": "disposable-provider-password"},
        )
        assert response.status_code == 200 and "code=" not in response.headers.get("location", "")
        assert "Account is disabled" in response.text
    finally:
        assert (
            await h.admin("PUT", "v6-org-a/users/oidc-alice-a", {"enabled": True})
        ).status_code == 204


async def test_schema_39_guard_resume_and_old_runtime_refusal(sso):
    h = sso
    await h.setup_user()
    await h.configure()
    await h.local_login()
    cookie = h.client.cookies.get(SESSION_COOKIE)
    await SSOIdentityMigration().up(h.db)  # Repeat does not log out current schema-39 sessions.
    await h.local.sessions.authenticate(cookie)
    # Simulate an already-running schema-38 local worker at its per-request gate.
    h.local.sso = None
    try:
        with pytest.raises(AuthRejected):
            await h.local.enabled()
    finally:
        h.local.sso = h.sso
    with pytest.raises(RuntimeError, match="SSO-aware"):
        await initialize_database(
            h.db, tenant_foundation=True, tenant_isolation=True, local_identity=True
        )
    with pytest.raises(OperationFailure):
        await h.db.organization_oidc.insert_one({"_id": "invalid"})
    await h.db.browser_sessions.update_one(
        {"_id": (await h.row())["_id"]}, {"$set": {"expires_at": utc_now() - timedelta(seconds=1)}}
    )
    with pytest.raises(AuthRejected):
        await h.local.sessions.authenticate(cookie)
