"""Browser transport and explicit per-request session checks."""

from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import httpx
import pytest

from app.identity.browser import SESSION_COOKIE
from app.identity.crypto import AuthRejected, SeedVault, csrf, random_token
from app.identity.repository import IdentityStore, SessionRead
from app.identity.service import LocalIdentity
from app.identity.sessions import BrowserSessions
from app.main import create_app
from app.models.common import utc_now


@pytest.mark.parametrize("tx", [None, object()])
async def test_human_session_join_is_exact_bounded_and_hides_private_join_rows(tx):
    from copy import deepcopy

    now = utc_now()
    query = {
        "_id": "hashed-session",
        "kind": "human",
        "revoked": False,
        "expires_at": {"$gt": now},
        "idle_expires_at": {"$gt": now},
    }
    row = {
        "_id": "hashed-session",
        "session_member": [{"user_id": "person", "org_id": "org-a"}],
        "session_user": [{"_id": "person"}],
        "session_org": [{"org_id": "org-a"}],
        "session_identity": [{"_id": "identity"}],
    }
    collection = Mock(
        aggregate=AsyncMock(return_value=Mock(to_list=AsyncMock(side_effect=[[deepcopy(row)], []])))
    )
    store = IdentityStore(Mock())
    store._collection = Mock(return_value=collection)
    result = await store.read_human_session(query, tx)
    assert result and result.row == {"_id": "hashed-session"}
    assert result.user == {"_id": "person"} and result.member["org_id"] == "org-a"
    assert result.identity == {"_id": "identity"} and "person" not in repr(result)
    assert await store.read_human_session(query, tx) is None
    assert collection.aggregate.await_count == 2
    call = collection.aggregate.await_args
    assert call.kwargs == {"session": tx, "maxTimeMS": 3000, "allowDiskUse": False}
    pipeline = call.args[0]
    assert pipeline[:2] == [{"$match": query}, {"$limit": 1}]
    joins = [stage["$lookup"] for stage in pipeline if "$lookup" in stage]
    assert [j["from"] for j in joins] == [
        "organization_memberships",
        "users",
        "organizations",
        "user_identities",
    ]
    assert all(j["pipeline"][1] == {"$limit": 1} for j in joins)
    assert pipeline[3] == {"$match": {"session_member.0._id": {"$exists": True}}}
    assert (
        joins[0]["let"]["org"] == "$org_id"
        and joins[0]["let"]["revision"] == "$membership_revision"
    )
    assert joins[1]["let"]["user"] == {"$arrayElemAt": ["$session_member.user_id", 0]}
    assert joins[1]["let"]["epoch"] == "$auth_epoch"
    assert joins[2]["let"]["version"] == "$policy_version"
    assert joins[3]["let"]["id"] == "$authentication.identity_id"
    assert joins[3]["pipeline"][-1] == {
        "$project": {"_id": 1, "user_id": 1, "provider": 1, "verified_at": 1}
    }
    for unsafe in (
        {},
        {"kind": "preauth", "_id": "hashed-session"},
        {"kind": "human", "_id": {"$ne": None}},
    ):
        with pytest.raises(ValueError):
            await store.read_human_session(unsafe)


@pytest.mark.parametrize("touch", [False, True])
async def test_expiry_during_authority_read_cannot_extend_or_deliver_session(monkeypatch, touch):
    start = utc_now()
    row = {
        "expires_at": start + timedelta(seconds=1),
        "idle_expires_at": start + timedelta(seconds=1),
    }
    inputs = SessionRead(
        row, {"_id": "person"}, {"user_id": "person"}, {"auth_policy": "local_only"}, None
    )
    store = Mock(read_human_session=AsyncMock(return_value=inputs), update=AsyncMock())
    clock = iter((start, start + timedelta(seconds=2)))
    monkeypatch.setattr("app.identity.sessions.utc_now", lambda: next(clock))
    with pytest.raises(AuthRejected):
        await BrowserSessions(store).authenticate(random_token(), touch=touch)
    store.update.assert_not_awaited()


async def test_joined_authentication_retains_final_session_revocation_update():
    now = utc_now()
    row = {"expires_at": now + timedelta(hours=1), "idle_expires_at": now + timedelta(minutes=30)}
    inputs = SessionRead(
        row, {"_id": "person"}, {"user_id": "person"}, {"auth_policy": "local_only"}, None
    )
    store = Mock(
        read_human_session=AsyncMock(return_value=inputs), update=AsyncMock(return_value=None)
    )
    with pytest.raises(AuthRejected):
        await BrowserSessions(store).authenticate(random_token())
    query = store.update.await_args.args[1]
    assert query["revoked"] is False and query["expires_at"]["$gt"] >= now


@pytest.mark.parametrize("refused", ["browser_sessions", "users", "organization_memberships"])
async def test_transaction_join_never_replaces_current_session_user_member_write_fences(refused):
    now = utc_now()
    row = {
        "_id": "session",
        "user_id": "person",
        "org_id": "org-a",
        "membership_id": "membership",
        "membership_revision": 7,
        "auth_epoch": 3,
        "policy_version": 2,
        "expires_at": now + timedelta(hours=1),
        "idle_expires_at": now + timedelta(minutes=30),
    }
    inputs = SessionRead(
        row, {"_id": "person"}, {"_id": "membership"}, {"auth_policy": "local_only"}, None
    )
    anchor = {"_id": "org-a", "org_id": "org-a", "revision": 4}

    async def update(name, query, change, tx):
        return None if name == refused else {"updated": True}

    store = Mock(
        read_human_session=AsyncMock(return_value=inputs),
        find=AsyncMock(
            side_effect=lambda name, query, tx: anchor if name == "policy_anchors" else row
        ),
        update=AsyncMock(side_effect=update),
    )
    sessions = BrowserSessions(store)
    sessions.policy_engine = Mock(fence=AsyncMock())
    tx = Mock()
    with pytest.raises(AuthRejected):
        await sessions.fence(row, tx)
    query, session = store.read_human_session.await_args.args
    assert session is tx
    assert all(
        query[key] == row[key]
        for key in (
            "_id",
            "user_id",
            "org_id",
            "membership_id",
            "membership_revision",
            "auth_epoch",
            "policy_version",
        )
    )
    assert query["kind"] == "human" and query["revoked"] is False
    sessions.policy_engine.fence.assert_awaited_once_with(anchor, tx)
    writes = store.update.await_args_list
    assert writes[-1].args[0] == refused
    assert all(call.args[-1] is tx and "$inc" in call.args[2] for call in writes)
    for call in writes:
        if call.args[0] == "users":
            assert call.args[1]["auth_epoch"] == 3
        if call.args[0] == "organization_memberships":
            assert call.args[1]["revision"] == 7


async def test_session_authority_join_is_exact_fresh_and_not_a_directory_source():
    from copy import deepcopy

    session = {
        "user_id": "person",
        "org_id": "org-a",
        "membership_id": "membership-a",
        "auth_epoch": 3,
        "membership_revision": 7,
        "policy_version": 2,
    }
    collection = Mock(aggregate=AsyncMock())
    cursor = Mock(
        to_list=AsyncMock(
            side_effect=[
                [
                    deepcopy(
                        {
                            "user_id": "person",
                            "session_user": [{"_id": "person"}],
                            "session_org": [{"org_id": "org-a"}],
                        }
                    )
                ],
                [],
            ]
        )
    )
    collection.aggregate.return_value = cursor
    store = IdentityStore(Mock())
    store._collection = Mock(return_value=collection)
    user, member, org = await store.session_authority(session)
    assert user == {"_id": "person"} and org == {"org_id": "org-a"}
    assert member == {"user_id": "person"}  # No private joined rows enter a member DTO.
    assert await store.session_authority(session) == (None, None, None)
    assert collection.aggregate.await_count == 2
    pipeline = collection.aggregate.await_args.args[0]
    assert pipeline[0]["$match"] == {
        "_id": "membership-a",
        "org_id": "org-a",
        "user_id": "person",
        "revision": 7,
        "status": "active",
        "verified_at": {"$ne": None},
    }
    assert [stage["$lookup"]["from"] for stage in pipeline[2:]] == ["users", "organizations"]
    assert pipeline[2]["$lookup"]["pipeline"][0]["$match"] == {
        "_id": "person",
        "status": "active",
        "auth_epoch": 3,
    }
    assert pipeline[3]["$lookup"]["pipeline"][0]["$match"] == {
        "org_id": "org-a",
        "status": "active",
        "policy_version": 2,
    }


async def test_session_authority_keeps_shared_transaction_operations_sequential():
    import asyncio

    active = 0
    peak = 0

    async def find(name, query, tx):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0)
        active -= 1
        return {"source": name}

    store = IdentityStore(Mock())
    store.find = AsyncMock(side_effect=find)
    tx = Mock()
    result = await store.session_authority(
        {
            "user_id": "person",
            "org_id": "org-a",
            "membership_id": "membership-a",
            "auth_epoch": 3,
            "membership_revision": 7,
            "policy_version": 2,
        },
        tx,
    )
    assert peak == 1 and len(result) == 3
    assert all(call.args[2] is tx for call in store.find.await_args_list)


async def test_cursor_metadata_is_request_local_and_headers_cannot_supply_it():
    from fastapi import FastAPI, Request

    from app.security.tenant_maintenance import TenantMaintenanceMiddleware

    marker = {
        "schema_version": 37,
        "stage": "isolated",
        "worker_schema_versions": [37],
        "domain_traffic_enabled": True,
    }
    read = AsyncMock(
        side_effect=[
            {**marker, "cursor_secret": b"a" * 32},
            {**marker, "cursor_secret": b"b" * 32},
            {**marker, "stage": "prepared", "cursor_secret": b"c" * 32},
        ]
    )
    app = FastAPI()
    app.state.tenant_control_database = {"_tenant_control": SimpleNamespace(find_one=read)}
    app.state.settings = SimpleNamespace(v6_management_policy=False)
    app.add_middleware(TenantMaintenanceMiddleware)
    observed = []

    @app.get("/api/fixture")
    async def fixture(request: Request):
        observed.append(request.state.acp_tenant_cursor_secret)
        return {"ok": True}

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="https://identity.example",
        headers={"X-Acp-Tenant-Cursor-Secret": "forged"},
    ) as client:
        assert (await client.get("/api/fixture")).json() == {"ok": True}
        assert (await client.get("/api/fixture")).json() == {"ok": True}
        assert (await client.get("/api/fixture")).status_code == 503
    assert observed == [b"a" * 32, b"b" * 32]
    assert set(read.await_args.args[1]) == {
        "schema_version",
        "stage",
        "worker_schema_versions",
        "domain_traffic_enabled",
        "cursor_secret",
    }


async def test_expiry_is_in_the_lookup_even_before_ttl_cleanup():
    store = Mock(find=AsyncMock(return_value=None))
    with pytest.raises(AuthRejected):
        await BrowserSessions(store).authenticate(random_token(), kind="preauth")
    query = store.find.call_args.args[1]
    assert query["revoked"] is False
    assert "$gt" in query["expires_at"] and "$gt" in query["idle_expires_at"]
    assert query["kind"] == "preauth"


@pytest.mark.parametrize("delta", [timedelta(minutes=11), timedelta(minutes=-1)])
def test_privileged_actions_refuse_stale_or_future_mfa(delta):
    with pytest.raises(AuthRejected, match="recent"):
        BrowserSessions.recent({"mfa_at": utc_now() - delta})


async def test_all_machine_transports_reject_browser_cookies_and_v6_rejects_bearers():
    app = create_app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="https://identity.example"
    ) as client:
        for path in (
            "/api/v1/projects",
            "/api/v4/gateway/identity",
            "/api/v5/client-profiles",
            "/mcp",
        ):
            result = await client.get(
                path, headers={"Cookie": f"{SESSION_COOKIE}={random_token()}"}
            )
            assert result.status_code == 401
        result = await client.get("/api/v6/me", headers={"Authorization": "Bearer test-only"})
        assert result.status_code == 401
        assert (await client.get("/api/v6/me")).status_code == 404


async def test_origin_and_csrf_apply_to_logout_and_validation_never_echoes_secrets(caplog):
    app = create_app()
    service = LocalIdentity(Mock(), SeedVault("test", {"test": b"x" * 32}), Mock())
    service.enabled = AsyncMock()
    service.throttle = AsyncMock()
    row = {"user_id": "user_test", "org_id": "org-a", "mfa_at": None}
    service.sessions.authenticate = AsyncMock(return_value=row)
    service.logout = AsyncMock()
    app.state.local_identity = service
    app.state.settings = SimpleNamespace(v6_browser_origin="https://identity.example")
    token = random_token()
    headers = {"Cookie": f"{SESSION_COOKIE}={token}", "X-CSRF-Token": csrf(token)}
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="https://identity.example"
        ) as client:
            assert (await client.post("/api/v6/auth/logout", headers=headers)).status_code == 403
            assert (
                await client.post(
                    "/api/v6/auth/logout", headers={**headers, "Origin": "https://foreign.example"}
                )
            ).status_code == 403
            result = await client.post(
                "/api/v6/auth/logout", headers={**headers, "Origin": "https://identity.example"}
            )
            assert result.status_code == 204
            cookie = result.headers["set-cookie"]
            assert "Secure" in cookie and "HttpOnly" in cookie and "Domain=" not in cookie
            secret = "private-factor-secret"
            invalid = await client.post(
                "/api/v6/auth/step-up",
                json={"code": secret, "extra": secret},
                headers={**headers, "Origin": "https://identity.example"},
            )
            assert invalid.status_code == 422 and secret not in invalid.text
            assert secret not in caplog.text and token not in caplog.text
    finally:
        service.passwords.close()


def test_local_identity_configuration_refuses_missing_isolation_origin_and_key_mount(settings):
    from pydantic import ValidationError

    from app.core.config import Settings

    data = settings.model_dump()
    with pytest.raises(ValidationError):
        Settings(**{**data, "v6_local_identity": True})


async def test_verified_browser_human_cannot_enter_the_operator_backup_boundary():
    from app import provenance
    from app.repositories.backup_repository import MongoBackupRepository

    token = provenance.begin()
    try:
        provenance.set_human("user_test", "org-a", "mfa")
        with pytest.raises(ValueError, match="installation-operator-only"):
            await MongoBackupRepository(Mock()).collection_counts()
    finally:
        provenance.end(token)


async def test_invalid_http_only_cookies_are_cleared_for_fresh_preauthentication():
    from app.identity.browser import PREAUTH_COOKIE
    from app.identity.sessions import IssuedSession

    app = create_app()
    service = LocalIdentity(Mock(), SeedVault("test", {"test": b"x" * 32}), Mock())
    service.enabled = AsyncMock()
    service.throttle = AsyncMock()
    service.sessions.authenticate = AsyncMock(side_effect=AuthRejected("invalid"))
    token = random_token()
    service.sessions.preauth = AsyncMock(return_value=IssuedSession(token, {}))
    app.state.local_identity = service
    app.state.settings = SimpleNamespace(v6_browser_origin="https://identity.example")
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="https://identity.example"
        ) as client:
            client.cookies.set(SESSION_COOKIE, random_token(), domain="identity.example", path="/")
            client.cookies.set(PREAUTH_COOKIE, random_token(), domain="identity.example", path="/")
            response = await client.get("/api/v6/auth/csrf")
            assert response.status_code == 200
            assert client.cookies.get(SESSION_COOKIE) is None
            assert client.cookies.get(PREAUTH_COOKIE) == token
            assert response.json()["csrf_token"] == csrf(token)
    finally:
        service.passwords.close()


def test_malformed_non_ascii_csrf_is_a_browser_refusal_not_a_server_error():
    from fastapi import HTTPException
    from starlette.requests import Request

    from app.identity.browser import check_csrf

    request = Request({"type": "http", "headers": [(b"x-csrf-token", b"\xff" * 64)]})
    with pytest.raises(HTTPException) as error:
        check_csrf(request, random_token())
    assert error.value.status_code == 403
