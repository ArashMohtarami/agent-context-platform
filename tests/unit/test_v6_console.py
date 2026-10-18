"""Safe self-only console read contracts and fixed browser callback navigation."""

from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException, Response
from starlette.requests import Request

from app.api.routes.console import enabled
from app.api.routes.sso_identity import browser_callback
from app.identity.console import bootstrap
from app.identity.http_errors import auth_busy, auth_rejected, private_validation_error
from app.models.common import utc_now


async def test_self_bootstrap_uses_verified_own_memberships_and_omits_private_secrets():
    now = utc_now()
    row = {
        "user_id": "person",
        "org_id": "org-a",
        "password_at": now,
        "mfa_at": now,
        "public_id": "browser",
        "created_at": now,
        "expires_at": now + timedelta(hours=1),
        "idle_expires_at": now + timedelta(minutes=30),
        "authentication": {"method": "local"},
    }
    calls = []

    async def listed(name, query):
        calls.append((name, query))
        if name == "organization_memberships":
            return [
                {"org_id": "org-a", "role": "contributor"},
                {"org_id": "org-b", "role": "reviewer"},
            ]
        return [
            {"provider": "local", "subject": "private@example.test", "verified_at": now},
            {"provider": "https://idp.example/realm", "subject": "private-sub", "verified_at": now},
        ]

    async def found(name, query):
        if name == "organizations":
            return {"org_id": query["org_id"], "name": query["org_id"], "auth_policy": "local_only"}
        if name == "mfa_factors":
            return {"recovery_hashes": ["private-hash"], "encrypted_seed": "private-seed"}
        return {"password_hash": "private-password"}

    local = SimpleNamespace(
        store=SimpleNamespace(list=listed, find=found),
        sessions=SimpleNamespace(authority=AsyncMock()),
        me=AsyncMock(return_value={"user_id": "person"}),
        assurance=lambda _: "mfa",
        policy_engine=None,
    )
    data = await bootstrap(local, row)
    assert [org["org_id"] for org in data["organizations"]] == ["org-a", "org-b"]
    assert calls[0] == (
        "organization_memberships",
        {"user_id": "person", "status": "active", "verified_at": {"$ne": None}},
    )
    assert calls[1] == ("user_identities", {"user_id": "person", "verified_at": {"$ne": None}})
    assert data["security"]["recovery_codes_remaining"] == 1
    assert data["security"]["identities"][1]["kind"] == "oidc"
    assert "private-" not in repr(data) and "private@" not in repr(data)
    assert "password_hash" not in repr(data)


def browser_request(path="/api/v6/auth/oidc/callback", accept="text/html", enabled=True):
    return Request(
        {
            "type": "http",
            "path": path,
            "method": "GET",
            "headers": [(b"accept", accept.encode())],
            "app": SimpleNamespace(
                state=SimpleNamespace(settings=SimpleNamespace(v6_console_enabled=enabled))
            ),
        }
    )


@pytest.mark.parametrize(
    "result,target",
    [
        ({"authenticated": True}, "/console/auth/complete"),
        ({"linked": True}, "/console/sign-in?linked=1"),
        ({"logged_out": True}, "/console/sign-in?logged_out=1"),
    ],
)
def test_browser_callback_is_fixed_local_and_preserves_secure_cookie(result, target):
    response = Response()
    response.set_cookie("__Host-test", "fictional", secure=True, httponly=True)
    redirected = browser_callback(browser_request(), response, result)
    assert redirected.status_code == 303 and redirected.headers["location"] == target
    assert "HttpOnly" in redirected.headers["set-cookie"]
    assert browser_callback(browser_request(accept="application/json"), response, result) == result
    assert browser_callback(browser_request(enabled=False), response, result) == result


async def test_failed_html_callback_does_not_retain_provider_codes_or_state():
    response = await auth_rejected(browser_request(), ValueError("private-provider-detail"))
    assert response.status_code == 303 and response.headers["location"] == "/console/auth/error"
    response = await auth_rejected(
        browser_request(path="/api/v6/auth/local/complete"), ValueError()
    )
    assert response.status_code == 401


async def test_console_is_disabled_until_explicit_enable_and_managed_cutover():
    engine = SimpleNamespace(managed_agents=True, enabled=AsyncMock())
    service = SimpleNamespace(
        policy_engine=engine, store=SimpleNamespace(find=AsyncMock(return_value=None))
    )
    context = SimpleNamespace(service=service)
    with pytest.raises(HTTPException) as disabled:
        await enabled(browser_request(enabled=False), context)
    assert disabled.value.status_code == 404
    with pytest.raises(HTTPException):
        await enabled(browser_request(), context)
    service.store.find.return_value = {"activated": True}
    await enabled(browser_request(), context)
    assert service.store.find.call_args.args == (
        "_managed_agents_control",
        {"_id": "installation", "schema_version": 42, "activated": True},
    )


@pytest.mark.parametrize("handler", [auth_busy, private_validation_error])
async def test_html_callback_errors_scrub_query_but_json_statuses_stay_compatible(handler):
    response = await handler(browser_request(), ValueError())
    assert response.status_code == 303 and response.headers["location"] == "/console/auth/error"
    response = await handler(browser_request(accept="application/json"), ValueError())
    assert response.status_code in {422, 429}
