"""Real replica-set identity transactions and HTTPS browser requests; test mail only."""

import asyncio
import json
from datetime import timedelta
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pyotp
import pytest
import pytest_asyncio
from pymongo.errors import DuplicateKeyError, OperationFailure

from app.db.initializer import initialize_database
from app.db.migrations.v038_local_identity import LocalIdentityMigration
from app.identity.browser import SESSION_COOKIE
from app.identity.crypto import AuthRejected, SeedVault, csrf, digest
from app.identity.mail import LocalMailSink
from app.identity.repository import COLLECTION_OWNERSHIP, IdentityStore
from app.identity.service import LocalIdentity, member_dto
from app.main import create_app
from app.models.common import utc_now

pytestmark = pytest.mark.integration
ORIGIN = "https://identity.example"
PASSWORD = "test-only-long-local-password"
NEW_PASSWORD = "test-only-replacement-password"


class Clock:
    def __init__(self):
        self.now = utc_now()

    def advance(self, **kwargs):
        self.now += timedelta(**kwargs)


class Harness:
    def __init__(self, database, service, app, sink, clock):
        self.db, self.service, self.app, self.sink, self.clock = database, service, app, sink, clock
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=ORIGIN)
        self.csrf = ""
        self.email = "alice@example.test"

    async def public(self):
        self.client.cookies.clear()
        response = await self.client.get("/api/v6/auth/csrf")
        assert response.status_code == 200
        self.csrf = response.json()["csrf_token"]

    async def post(self, path, data=None, expected=200):
        response = await self.client.post(
            "/api/v6/" + path,
            json=data or {},
            headers={"Origin": ORIGIN, "X-CSRF-Token": self.csrf},
        )
        assert response.status_code == expected, (path, response.status_code)
        if response.status_code == 200 and "csrf_token" in response.json():
            self.csrf = response.json()["csrf_token"]
        return response

    def message(self, purpose):
        messages = [
            json.loads(path.read_text())
            for path in sorted(self.sink.glob("*.json"), key=lambda path: path.stat().st_mtime_ns)
        ]
        return next(message for message in reversed(messages) if message["purpose"] == purpose)

    def code(self, seed):
        self.clock.advance(seconds=30)
        return pyotp.TOTP(seed).at(self.clock.now)

    async def signup(self, *, owner=False, mfa=True, email="alice@example.test"):
        self.email = email
        root = (
            [
                {
                    "action": "projects:read",
                    "resource_kind": "project",
                    "selector": "include",
                    "ids": ["payment-service"],
                    "conditions": {},
                }
            ]
            if owner
            else None
        )
        await self.service.operator_invite(
            "org-a",
            self.email,
            "Alice",
            approval_reference="test-operator-approval",
            root_delegation=root,
        )
        await self.public()
        invitation = self.message("invite")["token"]
        await self.post(
            "auth/invitations/accept", {"token": invitation, "password": PASSWORD}, expected=202
        )
        verification = self.message("verify")["token"]
        await self.post("auth/local/verify", {"token": verification}, expected=204)
        await self.login()
        seed, codes = None, []
        if mfa:
            seed = (await self.post("auth/mfa/enroll")).json()["seed"]
            confirmed = await self.post("auth/mfa/confirm", {"code": self.code(seed)})
            codes = confirmed.json()["recovery_codes"]
        return seed, codes, invitation, verification

    async def login(
        self, *, password=PASSWORD, org="org-a", seed=None, recovery=None, expected=200
    ):
        await self.public()
        challenge = await self.post(
            "auth/local/login",
            {"email": self.email, "password": password, "org_id": org},
            expected=expected,
        )
        if expected != 200:
            return challenge
        token = challenge.json()["challenge"]
        code = recovery or (self.code(seed) if seed else "")
        return await self.post(
            "auth/local/complete", {"token": token, "code": code, "recovery": bool(recovery)}
        )

    async def row(self):
        return await self.service.sessions.authenticate(self.client.cookies.get(SESSION_COOKIE))


@pytest_asyncio.fixture
async def identity(isolated_database, tmp_path, monkeypatch):
    clock = Clock()
    for module in ("app.identity.service", "app.identity.sessions", "app.identity.mfa"):
        monkeypatch.setattr(module + ".utc_now", lambda: clock.now)
    await initialize_database(
        isolated_database, tenant_foundation=True, tenant_isolation=True, local_identity=True
    )
    sink = tmp_path / "mail"
    service = LocalIdentity(
        IdentityStore(isolated_database),
        SeedVault("fixture", {"fixture": b"s" * 32}),
        LocalMailSink(sink),
    )
    app = create_app()
    app.state.local_identity = service
    app.state.settings = SimpleNamespace(v6_browser_origin=ORIGIN)
    app.state.tenant_control_database = isolated_database
    harness = Harness(isolated_database, service, app, sink, clock)
    try:
        yield harness
    finally:
        await harness.client.aclose()
        service.passwords.close()


async def test_real_enrollment_owner_intent_and_hashed_private_storage(identity, caplog):
    h = identity
    seed, codes, invitation, verification = await h.signup(owner=True)
    user = await h.db.users.find_one({})
    credential = await h.db.local_credentials.find_one({})
    factor = await h.db.mfa_factors.find_one({})
    member = await h.db.organization_memberships.find_one({})
    assert credential["password_hash"].startswith("$argon2id$v=19$m=65536,t=3,p=4$")
    assert seed not in repr(factor) and all(code not in repr(factor) for code in codes)
    assert factor["status"] == "active" and len(factor["recovery_hashes"]) == 10
    assert member["verified_at"] and member["bootstrap_intent"]["state"] == "pending_phase5"
    assert [item["action"] for item in member["bootstrap_intent"]["bootstrap_policy"]] == [
        "projects:read",
        "projects:write",
    ]
    assert not {"permission_assignments", "delegations"} & set(await h.db.list_collection_names())
    session = await h.row()
    assert session["user_id"] == user["_id"] and session["amr"] == ["password", "totp"]
    raw_cookie = h.client.cookies.get(SESSION_COOKIE)
    assert session["_id"] == digest("session", raw_cookie) and raw_cookie not in repr(session)
    me = await h.client.get("/api/v6/me", headers={"X-Org-ID": "org-b"}, params={"org_id": "org-b"})
    assert me.status_code == 200 and me.json()["membership"]["org_id"] == "org-a"
    assert "alice@example.test" not in me.text and "password_hash" not in me.text
    assert me.json()["effective_corpus_access"] == []
    assert set(member_dto(member)) == {"org_id", "display_name", "role", "status"}
    for path in ("/api/v1/projects", "/api/v4/gateway/identity", "/mcp"):
        assert (await h.client.get(path)).status_code == 401
    assert (await h.client.post("/api/v6/organizations/org-a/claim-owner")).status_code == 404
    audit = await h.db.identity_audit_events.find({}).to_list(None)
    assert any(item["action"] == "owner_provisioned" for item in audit)
    assert all("key_id" not in item["actor"] for item in audit)
    assert all(
        value not in repr(audit) + caplog.text
        for value in [PASSWORD, raw_cookie, seed, invitation, verification, *codes]
    )


@pytest.mark.parametrize("touch", [False, True])
async def test_real_expiry_during_joined_authority_read_never_extends_session(
    identity, monkeypatch, touch
):
    h = identity
    await h.signup(mfa=False)
    token = h.client.cookies.get(SESSION_COOKIE)
    before = await h.row()
    read = h.service.store.read_human_session

    async def crosses_idle_expiry(query):
        result = await read(query)  # The real persisted aggregation, with its actual predicates.
        assert result is not None
        h.clock.advance(minutes=31)
        return result

    monkeypatch.setattr(h.service.store, "read_human_session", crosses_idle_expiry)
    with pytest.raises(AuthRejected):
        await h.service.sessions.authenticate(token, touch=touch)
    after = await h.db.browser_sessions.find_one({"_id": before["_id"]})
    assert after["last_seen"] == before["last_seen"]
    assert after["idle_expires_at"] == before["idle_expires_at"]


async def test_real_challenge_purpose_expiry_browser_binding_and_audit_rollback(
    identity, monkeypatch
):
    h = identity
    await h.service.operator_invite(
        "org-a", "alice@example.test", "Alice", approval_reference="fixture"
    )
    await h.public()
    invite = h.message("invite")["token"]
    await h.post("auth/local/verify", {"token": invite}, expected=401)
    await h.post("auth/invitations/accept", {"token": invite, "password": PASSWORD}, expected=202)
    await h.post("auth/invitations/accept", {"token": invite, "password": PASSWORD}, expected=401)
    verify = h.message("verify")["token"]
    original_cookie = list(h.client.cookies.jar)
    await h.public()
    await h.post("auth/local/verify", {"token": verify}, expected=401)
    h.client.cookies.clear()
    for cookie in original_cookie:
        h.client.cookies.jar.set_cookie(cookie)
    from app.identity.browser import PREAUTH_COOKIE

    h.csrf = csrf(h.client.cookies.get(PREAUTH_COOKIE))
    real_audit = h.service.audit

    async def unavailable_audit(*args, **kwargs):
        raise RuntimeError("test-only audit failure")

    monkeypatch.setattr(h.service, "audit", unavailable_audit)
    await h.post("auth/local/verify", {"token": verify}, expected=500)
    assert (await h.db.auth_challenges.find_one({"_id": digest("challenge", verify)}))[
        "used_at"
    ] is None
    assert (await h.db.organization_memberships.find_one({}))["status"] == "verification_pending"
    monkeypatch.setattr(h.service, "audit", real_audit)
    await h.post("auth/local/verify", {"token": verify}, expected=204)
    await h.post("auth/local/verify", {"token": verify}, expected=401)
    login = await h.post(
        "auth/local/login", {"email": "alice@example.test", "password": PASSWORD, "org_id": "org-a"}
    )
    h.clock.advance(minutes=6)
    await h.post("auth/local/complete", {"token": login.json()["challenge"]}, expected=401)
    assert await h.db.auth_challenges.count_documents({"purpose": "login"}) == 1


async def test_real_totp_replay_login_challenge_race_and_single_use_recovery(identity):
    h = identity
    seed, codes, _, _ = await h.signup()
    current = pyotp.TOTP(seed).at(h.clock.now)
    await h.post("auth/step-up", {"code": current}, expected=401)
    await h.post("auth/step-up", {"code": h.code(seed)})
    await h.public()
    challenge = await h.post(
        "auth/local/login", {"email": "alice@example.test", "password": PASSWORD, "org_id": "org-a"}
    )
    payload = {"token": challenge.json()["challenge"], "code": h.code(seed)}
    headers = {"Origin": ORIGIN, "X-CSRF-Token": h.csrf}
    results = await asyncio.gather(
        *(
            h.client.post("/api/v6/auth/local/complete", json=payload, headers=headers)
            for _ in range(2)
        )
    )
    assert sum(result.status_code == 200 for result in results) == 1
    assert all(result.status_code in {200, 401, 409, 503} for result in results)
    await h.login(recovery=codes[0])
    assert (await h.row())["amr"] == ["password", "recovery"]
    await h.public()
    challenge = await h.post(
        "auth/local/login", {"email": "alice@example.test", "password": PASSWORD, "org_id": "org-a"}
    )
    await h.post(
        "auth/local/complete",
        {"token": challenge.json()["challenge"], "code": codes[0], "recovery": True},
        expected=401,
    )
    assert len((await h.db.mfa_factors.find_one({}))["recovery_hashes"]) == 9


async def test_real_reset_preserves_mfa_and_suspension_and_revokes_sessions(identity):
    h = identity
    seed, _, _, _ = await h.signup()
    old_session = h.client.cookies.get(SESSION_COOKIE)
    old_factor = await h.db.mfa_factors.find_one({})
    await h.public()
    known = await h.post(
        "auth/password-reset/request", {"email": "alice@example.test"}, expected=202
    )
    unknown = await h.post(
        "auth/password-reset/request", {"email": "unknown@example.test"}, expected=202
    )
    assert known.json() == unknown.json()
    reset = h.message("reset")["token"]
    await h.db.organization_memberships.update_one(
        {}, {"$set": {"status": "suspended"}, "$inc": {"revision": 1}}
    )
    before_sessions = await h.db.browser_sessions.count_documents({"kind": "human"})
    await h.post(
        "auth/password-reset/consume", {"token": reset, "password": NEW_PASSWORD}, expected=204
    )
    assert (await h.db.mfa_factors.find_one({})) == old_factor
    assert (await h.db.organization_memberships.find_one({}))["status"] == "suspended"
    assert await h.db.browser_sessions.count_documents({"kind": "human"}) == before_sessions
    await h.post(
        "auth/password-reset/consume", {"token": reset, "password": NEW_PASSWORD}, expected=401
    )
    assert (
        await h.client.get("/api/v6/me", headers={"Cookie": f"{SESSION_COOKIE}={old_session}"})
    ).status_code == 401
    await h.login(password=NEW_PASSWORD, expected=401)
    # Explicit operator fixture reinstatement does not restore any old session.
    await h.db.organization_memberships.update_one(
        {}, {"$set": {"status": "active"}, "$inc": {"revision": 1}}
    )
    await h.public()
    challenge = await h.post(
        "auth/local/login",
        {"email": "alice@example.test", "password": NEW_PASSWORD, "org_id": "org-a"},
    )
    assert challenge.json()["mfa_required"] is True
    await h.post("auth/local/complete", {"token": challenge.json()["challenge"]}, expected=401)
    await h.post(
        "auth/local/complete", {"token": challenge.json()["challenge"], "code": h.code(seed)}
    )


async def test_real_cookie_rotation_expiry_csrf_logout_and_revoke(identity):
    h = identity
    seed, _, _, _ = await h.signup()
    bob = Harness(h.db, h.service, h.app, h.sink, h.clock)
    try:
        await bob.signup(email="bob@example.test")
        foreign = await bob.row()
        await h.post("me/sessions/" + foreign["public_id"] + "/revoke", expected=401)
        await h.post("me/sessions/sess_missing/revoke", expected=401)
        assert (await bob.client.get("/api/v6/me")).status_code == 200
    finally:
        await bob.client.aclose()
    old = h.client.cookies.get(SESSION_COOKIE)
    rotated = await h.post("auth/session/rotate")
    cookie = rotated.headers.get_list("set-cookie")[0]
    assert (
        "Secure" in cookie
        and "HttpOnly" in cookie
        and "SameSite=lax" in cookie
        and "Path=/" in cookie
    )
    assert "Domain=" not in cookie
    assert (
        await h.client.get("/api/v6/me", headers={"Cookie": f"{SESSION_COOKIE}={old}"})
    ).status_code == 401
    row = await h.row()
    count = await h.db.browser_sessions.count_documents({})
    assert (
        await h.client.post("/api/v6/auth/logout", json={}, headers={"Origin": ORIGIN})
    ).status_code == 403
    assert (
        await h.client.post(
            "/api/v6/auth/logout",
            json={},
            headers={"Origin": "https://evil.example", "X-CSRF-Token": h.csrf},
        )
    ).status_code == 403
    assert await h.db.browser_sessions.count_documents({}) == count
    h.clock.advance(minutes=11)
    await h.post("me/sessions/" + row["public_id"] + "/revoke", expected=401)
    await h.post("auth/step-up", {"code": h.code(seed)})
    row = await h.row()
    await h.post("me/sessions/" + row["public_id"] + "/revoke", expected=204)
    assert (await h.client.get("/api/v6/me")).status_code == 401
    await h.login(seed=seed)
    await h.post("auth/logout", expected=204)
    assert (await h.client.get("/api/v6/me")).status_code == 401
    await h.login(seed=seed)
    row = await h.row()
    h.clock.advance(minutes=31)
    assert (await h.client.get("/api/v6/me")).status_code == 401
    assert await h.db.browser_sessions.find_one({"_id": row["_id"]})  # TTL has not cleaned it.
    # A real browser cannot remove an HttpOnly cookie itself. The server clears
    # it and gives the caller fresh CSRF/preauth state without admitting it as auth.
    bootstrap = await h.client.get("/api/v6/auth/csrf")
    assert bootstrap.status_code == 200 and h.client.cookies.get(SESSION_COOKIE) is None
    h.csrf = bootstrap.json()["csrf_token"]
    login = await h.post(
        "auth/local/login", {"email": h.email, "password": PASSWORD, "org_id": "org-a"}
    )
    await h.post("auth/local/complete", {"token": login.json()["challenge"], "code": h.code(seed)})
    row = await h.row()
    await h.db.browser_sessions.update_one(
        {"_id": row["_id"]}, {"$set": {"idle_expires_at": h.clock.now + timedelta(days=1)}}
    )
    h.clock.advance(hours=9)
    assert (await h.client.get("/api/v6/me")).status_code == 401
    assert await h.db.browser_sessions.find_one({"_id": row["_id"]})
    bootstrap = await h.client.get("/api/v6/auth/csrf")
    assert bootstrap.status_code == 200 and h.client.cookies.get(SESSION_COOKIE) is None
    h.clock.advance(minutes=16)
    # The expired preauth cookie can also be replaced without manual browser work.
    assert (await h.client.get("/api/v6/auth/csrf")).status_code == 200


async def test_real_strict_validators_identity_uniqueness_and_fail_closed_migration(identity):
    h = identity
    await h.signup(owner=True, mfa=False)
    await LocalIdentityMigration().up(h.db)  # Repeat is safe and preserves state.
    from app.models.actor import ActorProvenance, ActorType

    user = await h.db.users.find_one({})
    human = ActorProvenance(
        actor_type=ActorType.HUMAN,
        user_id=user["_id"],
        org_id="org-a",
        auth_method="local",
        assurance="password",
    )
    await h.db.project_context.update_one(
        {"org_id": "org-a"}, {"$set": {"actor": human.model_dump(mode="json")}}
    )
    authored = await h.db.project_context.find_one({"org_id": "org-a"})
    assert authored["actor"]["user_id"] == user["_id"] and "key_id" not in authored["actor"]
    with pytest.raises(OperationFailure):
        await h.db.users.update_one({}, {"$set": {"unencrypted_password": "forbidden"}})
    row = await h.db.organization_memberships.find_one({})
    with pytest.raises(DuplicateKeyError):
        await h.db.organization_memberships.insert_one({**row, "_id": uuid4().hex})
    identity_row = await h.db.user_identities.find_one({})
    with pytest.raises(DuplicateKeyError):
        await h.db.user_identities.insert_one({**identity_row, "_id": uuid4().hex})
    names = set(await h.db.list_collection_names())
    from app.db.tenant_schema import INSTALLATION_COLLECTIONS, TENANT_COLLECTIONS

    assert (
        not names
        - set(INSTALLATION_COLLECTIONS)
        - set(TENANT_COLLECTIONS)
        - set(COLLECTION_OWNERSHIP)
    )
    await h.db["_local_identity_control"].delete_one({})
    with pytest.raises(RuntimeError, match="unclassified"):
        await LocalIdentityMigration().up(h.db)
    assert await h.db.users.count_documents({}) == 1


async def test_real_recovery_replacement_password_change_and_explicit_mfa_removal(identity):
    h = identity
    seed, old_codes, _, _ = await h.signup()
    old_session = h.client.cookies.get(SESSION_COOKIE)
    renewed = await h.post("auth/mfa/recovery", {"code": h.code(seed)})
    new_codes = renewed.json()["recovery_codes"]
    factor = await h.db.mfa_factors.find_one({})
    assert len(new_codes) == 10 and not set(old_codes) & set(new_codes)
    assert digest("recovery:" + factor["user_id"], old_codes[0]) not in factor["recovery_hashes"]
    assert (
        await h.client.get("/api/v6/me", headers={"Cookie": f"{SESSION_COOKIE}={old_session}"})
    ).status_code == 401
    before = h.client.cookies.get(SESSION_COOKIE)
    await h.post(
        "auth/password/change",
        {"old_password": PASSWORD, "new_password": NEW_PASSWORD},
        expected=204,
    )
    assert (
        await h.client.get("/api/v6/me", headers={"Cookie": f"{SESSION_COOKIE}={before}"})
    ).status_code == 401
    assert (await h.db.mfa_factors.find_one({}))["status"] == "active"
    await h.login(password=NEW_PASSWORD, recovery=new_codes[0])
    await h.post("auth/mfa/remove", expected=204)
    assert (await h.db.mfa_factors.find_one({}))["status"] == "disabled"
    assert (await h.db.mfa_factors.find_one({}))["recovery_hashes"] == []
    assert (await h.client.get("/api/v6/me")).status_code == 401
    await h.login(password=NEW_PASSWORD)
    assert (await h.client.get("/api/v6/me")).json()["mfa_enrollment_required"] is True


async def test_real_second_membership_requires_existing_identity_proof_and_own_sessions_only(
    identity,
):
    h = identity
    seed, _, _, _ = await h.signup()
    user = await h.db.users.find_one({})
    await h.service.operator_invite(
        "org-b", "alice@example.test", "Alice B", approval_reference="independent-fixture"
    )
    await h.public()
    invite = h.message("invite")["token"]
    await h.post(
        "auth/invitations/accept",
        {"token": invite, "password": "wrong-test-password"},
        expected=401,
    )
    await h.post("auth/invitations/accept", {"token": invite, "password": PASSWORD}, expected=401)
    await h.post(
        "auth/invitations/accept",
        {"token": invite, "password": PASSWORD, "code": h.code(seed)},
        expected=202,
    )
    await h.post("auth/local/verify", {"token": h.message("verify")["token"]}, expected=204)
    assert await h.db.users.count_documents({}) == 1
    assert await h.db.organization_memberships.count_documents({"user_id": user["_id"]}) == 2
    await h.login(org="org-b", seed=seed)
    me = await h.client.get("/api/v6/me")
    assert me.json()["membership"]["org_id"] == "org-b"
    assert "org-a" not in me.text and "alice@example.test" not in me.text
    # An explicit operator auth-policy change invalidates the local session.
    await h.db.organizations.update_one(
        {"org_id": "org-b"},
        {"$set": {"auth_policy": "sso_required"}, "$inc": {"policy_version": 1}},
    )
    assert (await h.client.get("/api/v6/me")).status_code == 401
    await h.login(org="org-b", expected=401)


async def test_real_account_throttle_and_password_enumeration_resistance(identity):
    h = identity
    await h.signup(mfa=False)
    await h.public()
    known = await h.post(
        "auth/local/login",
        {"email": "alice@example.test", "password": "wrong-test-password", "org_id": "org-a"},
        expected=401,
    )
    unknown = await h.post(
        "auth/local/login",
        {"email": "unknown@example.test", "password": "wrong-test-password", "org_id": "org-a"},
        expected=401,
    )
    assert known.json() == unknown.json()
    # Shared persisted admission is independently bounded across store instances.
    second = LocalIdentity(
        IdentityStore(h.db), SeedVault("fixture", {"fixture": b"s" * 32}), LocalMailSink(h.sink)
    )
    try:
        from app.identity.crypto import AuthBusy

        for _ in range(2):
            await h.service.throttle("fixture-bucket", "same-account", 2)
        with pytest.raises(AuthBusy):
            await second.throttle("fixture-bucket", "same-account", 2)
        private_bucket = await h.db.auth_throttles.find_one({"count": 3})
        assert private_bucket and "same-account" not in repr(private_bucket)
        import asyncio

        from app.identity.crypto import digest

        key = digest("throttle", "fictional-concurrent-initial-bucket")
        expires = utc_now() + timedelta(hours=1)
        results = await asyncio.gather(
            *(instance.store.throttle_bucket(key, expires) for instance in [h.service, second] * 8)
        )
        assert sorted(row["count"] for row in results) == list(range(1, 17))
        persisted = await h.db.auth_throttles.find_one({"_id": key})
        assert persisted["count"] == 16
        assert persisted["expires_at"] == expires.replace(
            microsecond=(expires.microsecond // 1000) * 1000
        )
    finally:
        second.passwords.close()
