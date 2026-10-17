"""Browser transport and explicit per-request session checks."""

from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import httpx
import pytest

from app.identity.browser import SESSION_COOKIE
from app.identity.crypto import AuthRejected, SeedVault, csrf, random_token
from app.identity.service import LocalIdentity
from app.identity.sessions import BrowserSessions
from app.main import create_app
from app.models.common import utc_now


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
