"""Real production API/static images over TLS in local-only and external OIDC modes."""

import asyncio
import json
import os
import re
import ssl
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
import pytest_asyncio

from app.identity.providers import Registry
from app.identity.sso import SSOIdentity
from app.models.common import utc_now
from tests.v6_deployment_fixtures import docker, hosted_stack

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]
PASSWORD = "fictional-hosting-password-phase-fifteen"


@pytest_asyncio.fixture(params=["local-only", "external-oidc"])
async def hosting(managed_agents_db, request):
    if os.environ.get("V6_DEPLOYMENT_TESTS") != "1":
        pytest.skip("run scripts/test-v6-deployment.sh for real image/TLS/provider evidence")
    h = managed_agents_db
    origin = os.environ["V6_BROWSER_TEST_ORIGIN"]
    tls = Path(os.environ["V6_OIDC_REGISTRATIONS_FILE"]).parent
    h.local.sso = SSOIdentity(
        h.local, Registry.from_file(tls / "registrations.json", origin), h.local.mfa.vault
    )
    local_only = request.param == "local-only"
    await h.local.sso.configure(
        "org-a",
        "provider-a",
        "local_only" if local_only else "sso_preferred",
        "fictional-hosting-review",
    )
    await h.db.local_credentials.insert_one(
        {
            "_id": "member",
            "user_id": "member",
            "password_hash": await h.local.passwords.hash(PASSWORD),
            "updated_at": utc_now(),
        }
    )
    async with h.store.transaction() as tx:
        await h.local.mfa.begin("member", tx)
        recovery = await h.local.mfa.recovery_codes("member", tx, activate=True)
    root = Path(os.environ["V6_TEST_DIR"]) / ("hosting-" + uuid4().hex[:12])
    async with hosted_stack(h, origin, tls, root, local_only=local_only) as (env, compose):
        async with httpx.AsyncClient(
            base_url=origin,
            verify=ssl.create_default_context(cafile=tls / "server.crt"),
            timeout=15,
        ) as client:
            yield h, client, env, compose, local_only, recovery


async def test_tls_console_only_fallback_errors_headers_and_proxy_spoof(hosting):
    h, client, env, compose, local_only, _ = hosting
    index = await client.get("/console/sign-in")
    assert index.status_code == 200 and "<html" in index.text
    for path, expected in (
        ("/console", 308),
        ("/api/v6/unknown", 404),
        ("/api/v1/unknown", 404),
        ("/console/assets/missing.js", 404),
        ("/console/licenses/missing-LICENSE", 404),
        ("/console/missing.svg", 404),
        ("/unknown", 404),
        ("/backups/raw.archive", 404),
        ("/metrics", 404),
    ):
        response = await client.get(path)
        assert response.status_code == expected, (path, response.status_code)
        assert response.headers["cache-control"] == "no-store"
        if expected != 308:
            assert response.text != index.text
    asset = re.search(r'src="([^"]+\.js)"', index.text)[1]
    js = await client.get(asset)
    assert js.status_code == 200 and "immutable" in js.headers["cache-control"]
    assert PASSWORD not in js.text and "__Host-acp-session=" not in js.text
    assert "frame-ancestors" in index.headers["content-security-policy"]
    assert index.headers["x-frame-options"] == "DENY"
    assert "max-age=" in index.headers["strict-transport-security"]
    assert (await client.get("/console/", headers={"Host": "foreign.example"})).status_code == 421
    csrf = await client.get(
        "/api/v6/auth/csrf", headers={"X-Forwarded-Proto": "http", "X-Forwarded-For": "spoofed"}
    )
    assert csrf.status_code == 200 and csrf.headers["cache-control"] == "no-store"
    cookie = csrf.headers["set-cookie"]
    assert "Secure" in cookie and "HttpOnly" in cookie and "Domain=" not in cookie
    # Loopback inside the API container is not the trusted gateway IP.
    code = (
        "import urllib.request,urllib.error; "
        "r=urllib.request.Request('http://127.0.0.1:8000/api/v6/auth/csrf',"
        "headers={'X-Forwarded-Proto':'https'});\n"
        "try:\n urllib.request.urlopen(r)\n"
        "except urllib.error.HTTPError as e:\n print(e.code)"
    )
    assert (
        await docker(*compose, "exec", "-T", "context-api", "python", "-c", code, env=env)
    ).strip() == "401"
    start = await client.post(
        "/api/v6/auth/oidc/start",
        json={"org_id": "org-a"},
        headers={
            "Origin": str(client.base_url).rstrip("/"),
            "X-CSRF-Token": csrf.json()["csrf_token"],
        },
    )
    if local_only:
        assert start.status_code in (401, 403)
    else:
        assert start.status_code == 200 and start.json()["authorization_url"].startswith(
            "https://127.0.0.1:"
        )

    marker = "phase15-synthetic-callback-must-not-be-logged"
    await client.get("/api/v6/auth/oidc/callback", params={"state": marker, "code": marker})
    log = await docker(*compose, "logs", "--no-color", env=env)
    assert marker not in log

    # A distinct machine credential works through the same TLS edge; no browser cookies.
    from tests.v6_managed_agent_fixtures import provision

    _, _, delivered = await provision(h)
    client.cookies.clear()
    bearer = {"Authorization": "Bearer " + delivered["token"]}
    assert (await client.get("/api/v1/projects/payment-service", headers=bearer)).status_code == 200
    async with (
        asyncio.timeout(15),
        client.stream(
            "POST",
            "/mcp",
            headers={**bearer, "Accept": "application/json, text/event-stream"},
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {},
                    "clientInfo": {"name": "phase15-isolated", "version": "1"},
                },
            },
        ) as initialized,
    ):
        assert initialized.status_code == 200
        assert initialized.headers["mcp-session-id"]
        assert initialized.headers["cache-control"] == "no-store"
        if initialized.headers["content-type"].startswith("application/json"):
            assert "result" in json.loads(await initialized.aread())
        else:
            async for line in initialized.aiter_lines():
                if line.startswith("data:"):
                    assert "result" in json.loads(line.removeprefix("data:"))
                    break
            else:
                raise AssertionError("MCP did not return an initialized result")
    assert (await client.get("/api/v6/me", headers=bearer)).status_code == 401


async def test_real_local_mfa_cookie_csrf_bearer_mcp_and_backup_boundary(hosting):
    _h, client, _env, _compose, _local_only, recovery = hosting
    origin = str(client.base_url).rstrip("/")
    pre = await client.get("/api/v6/auth/csrf")
    headers = {"Origin": origin, "X-CSRF-Token": pre.json()["csrf_token"]}
    login = await client.post(
        "/api/v6/auth/local/login",
        headers=headers,
        json={"org_id": "org-a", "email": "member@example.test", "password": PASSWORD},
    )
    assert login.status_code == 200, login.status_code
    session = await client.post(
        "/api/v6/auth/local/complete",
        headers=headers,
        json={"token": login.json()["challenge"], "code": recovery[0], "recovery": True},
    )
    assert session.status_code == 200, session.status_code
    assert (await client.get("/api/v6/me")).status_code == 200
    for path in ("/api/v1/projects", "/mcp"):
        assert (await client.get(path)).status_code == 401
    for path in ("/api/v6/organizations/org-a/backups", "/api/v6/backups", "/backups/raw.archive"):
        assert (await client.get(path)).status_code == 404
    assert (
        await client.post(
            "/api/v6/auth/logout",
            headers={
                "Origin": "https://foreign.example",
                "X-CSRF-Token": session.json()["csrf_token"],
            },
            json={},
        )
    ).status_code == 403
    assert (
        await client.post("/api/v6/auth/logout", headers={"Origin": origin}, json={})
    ).status_code == 403
    assert (
        await client.post(
            "/api/v6/auth/logout",
            headers={"Origin": origin, "X-CSRF-Token": session.json()["csrf_token"]},
            json={},
        )
    ).status_code == 204
    assert (await client.get("/api/v6/me")).status_code == 401
