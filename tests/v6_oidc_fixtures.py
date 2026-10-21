"""Real HTTPS Keycloak and replica set only; all people and factors synthetic."""

import asyncio
import json
import os
import ssl
import traceback
from html.parser import HTMLParser
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urljoin, urlsplit
from uuid import uuid4

import httpx
import pyotp
import pytest
import pytest_asyncio

from app.db.initializer import initialize_database
from app.identity.browser import SESSION_COOKIE
from app.identity.crypto import AuthRejected, SeedVault
from app.identity.http_errors import auth_rejected
from app.identity.mail import LocalMailSink
from app.identity.providers import Registry
from app.identity.repository import IdentityStore
from app.identity.service import LocalIdentity
from app.identity.sso import SSOIdentity
from app.main import create_app
from app.models.common import utc_now

ORIGIN = os.environ.get("V6_BROWSER_TEST_ORIGIN", "https://identity.example")
PASSWORD = "synthetic-local-password-for-phase-four"


class Form(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.action, self.fields = "", {}
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        row = dict(attrs)
        if tag == "form":
            self.action = row.get("action", "")
        if tag == "input" and row.get("name") and row.get("type") != "checkbox":
            self.fields[row["name"]] = row.get("value", "")


class SSOHarness:
    def __init__(self, database, local, sso, app, sink, provider):
        self.db, self.local, self.sso, self.sink = database, local, sso, sink
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=ORIGIN)
        self.provider = provider
        self.csrf = ""
        self.codes = []
        self.user_id = ""
        self.last_provider_otp = ""

    async def public(self):
        self.client.cookies.clear()
        response = await self.client.get("/api/v6/auth/csrf")
        assert response.status_code == 200
        self.csrf = response.json()["csrf_token"]

    async def post(self, path, data=None, expected=200, **headers):
        response = await self.client.post(
            "/api/v6/" + path,
            json=data or {},
            headers={
                "Origin": ORIGIN,
                "X-CSRF-Token": self.csrf,
                **headers,
            },
        )
        assert response.status_code == expected, (path, response.status_code, response.text[:200])
        if expected == 200 and "csrf_token" in response.json():
            self.csrf = response.json()["csrf_token"]
        return response

    def message(self, purpose):
        messages = [
            json.loads(path.read_text())
            for path in sorted(self.sink.glob("*.json"), key=lambda p: p.stat().st_mtime_ns)
        ]
        return next(m for m in reversed(messages) if m["purpose"] == purpose)["token"]

    async def setup_user(self):
        await self.local.operator_invite(
            "org-a", "alice@example.test", "Alice", approval_reference="synthetic-operator-approval"
        )
        await self.public()
        await self.post(
            "auth/invitations/accept", {"token": self.message("invite"), "password": PASSWORD}, 202
        )
        await self.post("auth/local/verify", {"token": self.message("verify")}, 204)
        await self.local_login(mfa=False)
        seed = (await self.post("auth/mfa/enroll")).json()["seed"]
        result = await self.post("auth/mfa/confirm", {"code": pyotp.TOTP(seed).now()})
        self.codes = result.json()["recovery_codes"]
        self.user_id = (await self.row())["user_id"]
        # Explicit test operator assigns distinct roles; no provider role mapping.
        await self.db.organization_memberships.update_one(
            {"user_id": self.user_id}, {"$set": {"role": "org_owner"}}
        )
        await self.db.organization_memberships.insert_one(
            {
                "_id": uuid4().hex,
                "user_id": self.user_id,
                "org_id": "org-b",
                "display_name": "Alice",
                "role": "contributor",
                "status": "active",
                "verified_at": utc_now(),
                "revision": 1,
                "fence": 0,
                "bootstrap_owner": False,
                "bootstrap_intent": None,
                "created_at": utc_now(),
            }
        )

    async def local_login(self, org="org-a", mfa=True, expected=200):
        await self.public()
        response = await self.post(
            "auth/local/login",
            {
                "email": "alice@example.test",
                "password": PASSWORD,
                "org_id": org,
            },
            expected,
        )
        if expected != 200:
            return response
        code = self.codes.pop() if mfa else ""
        return await self.post(
            "auth/local/complete",
            {"token": response.json()["challenge"], "code": code, "recovery": mfa},
        )

    async def row(self):
        return await self.local.sessions.authenticate(self.client.cookies.get(SESSION_COOKIE))

    async def authorize(self, url, username="alice", *, password="disposable-provider-password"):  # noqa: S107 - synthetic fixture
        """Follow real provider forms/redirects, stop before the application callback."""
        response = await self.provider.get(url)
        submitted_otp = False
        for _ in range(12):
            if response.is_redirect:
                location = urljoin(str(response.url), response.headers["location"])
                if location.startswith(ORIGIN + "/"):
                    return location
                response = await self.provider.get(location)
                continue
            assert response.status_code == 200, "provider did not accept test browser flow"
            form = Form(response.text)
            assert form.action, "provider returned no expected form"
            data = form.fields
            if "username" in data:
                data["username"] = username
            if "password" in data:
                data["password"] = password
            if "otp" in data:
                assert not submitted_otp, (
                    "provider rejected the synthetic OTP; check fixture encoding"
                )
                submitted_otp = True
                seed = (Path(os.environ["V6_OIDC_REGISTRATIONS_FILE"]).parent / "totp").read_text()
                code = pyotp.TOTP(seed).now()
                # Exercise Keycloak's actual non-reusable OTP policy. Two fresh
                # provider logins must wait for different time steps.
                for _ in range(125):
                    if code != self.last_provider_otp:
                        break
                    await asyncio.sleep(0.25)
                    code = pyotp.TOTP(seed).now()
                assert code != self.last_provider_otp
                self.last_provider_otp = code
                data["otp"] = code
            response = await self.provider.post(urljoin(str(response.url), form.action), data=data)
        raise AssertionError("provider browser flow exceeded bounded form steps")

    async def callback(self, url, expected=200):
        response = await self.client.get(url)
        assert response.status_code == expected, (response.status_code, response.text[:200])
        if expected == 200 and "csrf_token" in response.json():
            self.csrf = response.json()["csrf_token"]
        return response

    async def link(self, org="org-a", username="alice", expected=200):
        start = await self.post("auth/identities/link", {"org_id": org})
        callback = await self.authorize(start.json()["authorization_url"], username)
        return await self.callback(callback, expected)

    async def oidc_login(self, org="org-a", username="alice", expected=200):
        await self.public()
        start = await self.post("auth/oidc/start", {"org_id": org})
        callback = await self.authorize(start.json()["authorization_url"], username)
        return await self.callback(callback, expected)

    async def configure(self, org="org-a", policy="sso_preferred"):
        await self.sso.configure(org, "provider-" + org[-1], policy, "synthetic-reviewed-provider")

    async def admin(self, method, path, data=None):
        reg = self.sso.registry.get("provider-a")
        base = reg.issuer.split("/realms/")[0]
        secret = (Path(os.environ["V6_OIDC_REGISTRATIONS_FILE"]).parent / "admin").read_text()
        token = await self.provider.post(
            base + "/realms/master/protocol/openid-connect/token",
            data={
                "grant_type": "password",
                "client_id": "admin-cli",
                "username": "v6-test-operator",
                "password": secret,
            },
        )
        assert token.status_code == 200
        return await self.provider.request(
            method,
            base + "/admin/realms/" + path,
            json=data,
            headers={"Authorization": "Bearer " + token.json()["access_token"]},
        )


@pytest_asyncio.fixture
async def sso(isolated_database, tmp_path):
    path = os.environ.get("V6_OIDC_REGISTRATIONS_FILE")
    if not path:
        pytest.skip("explicit disposable HTTPS Keycloak required; Phase 4 gate incomplete")
    registry = Registry.from_file(Path(path), ORIGIN)
    assert all(urlsplit(r.issuer).hostname == "127.0.0.1" for r in registry.registrations.values())
    await initialize_database(
        isolated_database,
        tenant_foundation=True,
        tenant_isolation=True,
        local_identity=True,
        sso=True,
    )
    sink = tmp_path / "mail"
    vault = SeedVault("fixture", {"fixture": b"s" * 32})
    local = LocalIdentity(IdentityStore(isolated_database), vault, LocalMailSink(sink))
    service = SSOIdentity(local, registry, vault)
    local.sso = service
    app = create_app()
    app.state.local_identity = local
    app.state.settings = SimpleNamespace(v6_browser_origin=ORIGIN)
    app.state.tenant_control_database = isolated_database

    async def refusal(request, error):
        # Test failure diagnostics contain code locations/types, never token bodies.
        for exception in (error, error.__context__):
            if exception:
                print(
                    type(exception).__name__,
                    [
                        (Path(f.filename).name, f.lineno, f.name)
                        for f in traceback.extract_tb(exception.__traceback__)
                    ],
                )
        return await auth_rejected(request, error)

    app.add_exception_handler(AuthRejected, refusal)
    context = ssl.create_default_context(cafile=registry.get("provider-a").ca_file)
    async with httpx.AsyncClient(verify=context, trust_env=False, timeout=15) as provider:
        harness = SSOHarness(isolated_database, local, service, app, sink, provider)
        try:
            yield harness
        finally:
            await harness.client.aclose()
            local.passwords.close()
