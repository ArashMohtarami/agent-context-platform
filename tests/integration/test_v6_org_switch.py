"""One human, distinct memberships, different real providers and stale-context denial."""

from datetime import timedelta

import pytest

from app.identity.browser import SESSION_COOKIE
from app.identity.crypto import AuthRejected
from app.models.common import utc_now

pytestmark = pytest.mark.integration


async def test_joined_session_rechecks_exact_bindings_and_verified_identity(sso):
    h = sso
    await h.setup_user()
    await h.local_login()
    token = h.client.cookies.get(SESSION_COOKIE)
    row = await h.row()
    identity = await h.db.user_identities.find_one({"_id": row["authentication"]["identity_id"]})
    foreign_member = await h.db.organization_memberships.find_one(
        {"org_id": "org-b", "user_id": h.user_id}
    )
    # Explicit raw fixture mutations test each persisted boundary independently;
    # these are not public reinstatement or identity-linking operations.
    changes = [
        ("browser_sessions", {"_id": row["_id"]}, "membership_id", foreign_member["_id"]),
        (
            "organization_memberships",
            {"_id": row["membership_id"]},
            "revision",
            row["membership_revision"] + 1,
        ),
        ("users", {"_id": h.user_id}, "auth_epoch", row["auth_epoch"] + 1),
        ("organizations", {"org_id": "org-a"}, "policy_version", row["policy_version"] + 1),
        ("user_identities", {"_id": identity["_id"]}, "provider", "https://foreign.example"),
        ("user_identities", {"_id": identity["_id"]}, "user_id", "foreign-person"),
        ("user_identities", {"_id": identity["_id"]}, "verified_at", None),
        ("browser_sessions", {"_id": row["_id"]}, "revoked", True),
    ]
    for collection, query, field, value in changes:
        before = await h.db[collection].find_one(query)
        await h.db[collection].update_one(query, {"$set": {field: value}})
        try:
            with pytest.raises(AuthRejected):
                await h.local.sessions.authenticate(token, touch=False)
        finally:
            await h.db[collection].update_one(query, {"$set": {field: before[field]}})
        assert (await h.local.sessions.authenticate(token, touch=False))["org_id"] == "org-a"
    assert (await h.db.organization_memberships.find_one({"_id": foreign_member["_id"]}))[
        "status"
    ] == "active"
    assert not any(key.startswith("session_") for key in row)


async def test_explicit_local_switch_rotates_session_csrf_and_rejects_old_context(sso):
    h = sso
    await h.setup_user()
    listing = (await h.client.get("/api/v6/me/organizations")).json()["organizations"]
    assert {m["org_id"]: m["role"] for m in listing} == {
        "org-a": "org_owner",
        "org-b": "contributor",
    }
    assert all(set(m) == {"org_id", "display_name", "role", "status"} for m in listing)
    before = await h.row()
    old_cookie, old_csrf = h.client.cookies.get(SESSION_COOKIE), h.csrf
    old_context = (await h.client.get("/api/v6/me")).json()["policy_context"]
    result = await h.post("me/active-organization", {"org_id": "org-b"})
    assert result.json()["policy_context"]["org_id"] == "org-b"
    assert old_cookie != h.client.cookies.get(SESSION_COOKIE) and old_csrf != h.csrf
    await h.post("me/active-organization", {"org_id": "org-a"}, 403, **{"X-CSRF-Token": old_csrf})
    assert (await h.row())["expires_at"] == before["expires_at"]
    with pytest.raises(AuthRejected):
        await h.local.sessions.authenticate(old_cookie)
    assert (
        await h.client.get(
            "/api/v6/organizations/org-a/session-context",
            headers={"X-Session-Context": old_context["context_id"]},
        )
    ).status_code == 403
    assert (
        await h.client.get("/api/v6/me", headers={"X-Session-Context": old_context["context_id"]})
    ).status_code == 409
    assert (await h.client.get("/api/v6/organizations/org-b/session-context")).status_code == 409
    context = result.json()["policy_context"]["context_id"]
    assert (
        await h.client.get(
            "/api/v6/organizations/org-b/session-context", headers={"X-Session-Context": context}
        )
    ).status_code == 200
    await h.post("me/active-organization", {"org_id": "foreign-org"}, 401)


async def test_required_target_needs_its_own_real_provider_not_local_or_org_a_oidc(sso):
    h = sso
    await h.setup_user()
    await h.configure("org-a")
    await h.configure("org-b")
    await h.local_login()
    await h.link("org-a")
    await h.local_login()
    await h.link("org-b")
    await h.configure("org-b", "sso_required")
    await h.local_login("org-b", expected=401)
    await h.local_login()
    await h.post("me/active-organization", {"org_id": "org-b"}, 401)
    await h.oidc_login("org-a")
    old_public_id = (await h.row())["public_id"]
    await h.post(f"me/sessions/{old_public_id}/revoke", expected=401)
    await h.post("auth/step-up", {"code": h.codes.pop(), "recovery": True})
    old_cookie = h.client.cookies.get(SESSION_COOKIE)
    old_public_id = (await h.row())["public_id"]
    assert (await h.row())["mfa_at"] is not None
    # Even fresh local MFA plus valid org-A OIDC is not org-B provider evidence.
    await h.post("me/active-organization", {"org_id": "org-b"}, 401)
    start = await h.post("auth/oidc/reauthenticate", {"org_id": "org-b", "purpose": "switch"})
    callback = await h.authorize(start.json()["authorization_url"])
    result = await h.callback(callback)
    assert result.json()["policy_context"]["org_id"] == "org-b"
    row = await h.row()
    assert row["authentication"]["registration_id"] == "provider-b"
    assert set(row["authentication"]["verified_amr"]) >= {"pwd", "otp"}
    assert row["mfa_at"] is not None
    await h.post(f"me/sessions/{old_public_id}/revoke", expected=204)
    assert row["authentication"]["issuer"].endswith("/v6-org-b")
    assert (await h.client.get("/api/v6/me")).json()["membership"]["role"] == "contributor"
    with pytest.raises(AuthRejected):
        await h.local.sessions.authenticate(old_cookie)
    await h.callback(callback, 401)
    await h.db.organization_memberships.update_one(
        {"_id": row["membership_id"]}, {"$set": {"status": "revoked"}, "$inc": {"revision": 1}}
    )
    assert (await h.client.get("/api/v6/me")).status_code == 401


async def test_local_only_and_explicit_break_glass_have_no_automatic_fallback(sso):
    h = sso
    await h.setup_user()
    await h.public()
    await h.post("auth/oidc/start", {"org_id": "org-a"}, 401)
    await h.configure("org-b", "sso_required")
    await h.local_login("org-b", expected=401)
    await h.sso.break_glass("org-b", h.user_id, 10, "explicit-test-emergency-approval")
    await h.local_login("org-b")
    row = await h.row()
    assert row["authentication"]["method"] == "local" and row["mfa_at"]
    assert await h.db.identity_audit_events.count_documents({"action": "break_glass_approved"}) == 1
    await h.db.organization_break_glass.update_one(
        {"org_id": "org-b"}, {"$set": {"expires_at": utc_now() - timedelta(seconds=1)}}
    )
    assert (await h.client.get("/api/v6/me")).status_code == 401
    await h.local_login("org-b", expected=401)
