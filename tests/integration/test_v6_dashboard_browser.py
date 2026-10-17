"""Actual built SPA + HTTPS API + replica set + HTTPS Keycloak; fictional people only.

A private filesystem control channel prepares/inspects fixture state. No fixture
endpoint, master key or bypass login is installed in the application or bundle.
"""

import asyncio
import json
import os
from datetime import timedelta
from pathlib import Path
from urllib.parse import urlsplit

import pyotp
import pytest
import uvicorn
from starlette.responses import FileResponse

from app.identity.providers import Registry
from app.identity.sso import SSOIdentity
from app.management.invitations import Invitations
from app.management.models import InviteCreate, ReviewedPolicy
from app.models.common import utc_now

pytestmark = pytest.mark.integration
PASSWORD = "fictional-browser-password-phase-nine"


async def test_real_dashboard_browser(managed_agents_db):
    h = managed_agents_db
    registration_file = os.environ.get("V6_OIDC_REGISTRATIONS_FILE")
    descriptor_file = os.environ.get("V6_BROWSER_DESCRIPTOR")
    if not registration_file or not descriptor_file:
        pytest.skip("run dashboard test:e2e for the isolated HTTPS API/provider browser gate")
    origin = os.environ["V6_BROWSER_TEST_ORIGIN"]
    assert urlsplit(origin).hostname == "127.0.0.1"
    descriptor = Path(descriptor_file)
    root = Path(os.environ["V6_TEST_DIR"]) / "browser"
    root.mkdir(mode=0o700)
    requests = root / "requests"
    requests.mkdir(mode=0o700)
    registry = Registry.from_file(Path(registration_file), origin)
    h.local.sso = SSOIdentity(h.local, registry, h.local.mfa.vault)
    await h.local.sso.configure("org-a", "provider-a", "sso_preferred", "fictional-browser-review")
    await h.local.sso.configure("org-b", "provider-b", "sso_required", "fictional-browser-review")
    h.app.state.settings = h.app.state.settings.model_copy(
        update={"v6_console_enabled": True, "v6_browser_origin": origin}
    )
    await h.db.organizations.update_one({"org_id": "org-a"}, {"$set": {"name": "Fictional Alpha"}})
    await h.db.organizations.update_one({"org_id": "org-b"}, {"$set": {"name": "Fictional Beta"}})
    encoded = await h.local.passwords.hash(PASSWORD)
    factors = {}
    recoveries = {}
    for user in ("owner", "member", "lead", "other", "reviewer"):
        now = utc_now()
        await h.db.local_credentials.insert_one(
            {"_id": user, "user_id": user, "password_hash": encoded, "updated_at": now}
        )
        async with h.store.transaction() as tx:
            seed, _ = await h.local.mfa.begin(user, tx)
            factors[user] = seed
            recoveries[user] = await h.local.mfa.recovery_codes(user, tx, activate=True)
    await h.db.organization_memberships.insert_one(
        {
            "_id": "browser-member-b",
            "org_id": "org-b",
            "user_id": "member",
            "display_name": "member",
            "role": "reviewer",
            "status": "active",
            "verified_at": utc_now(),
            "revision": 1,
            "fence": 0,
            "bootstrap_owner": False,
            "bootstrap_intent": None,
            "created_at": utc_now(),
        }
    )
    for suffix in ("a", "b"):
        await h.db.user_identities.insert_one(
            {
                "_id": "browser-oidc-" + suffix,
                "provider": registry.get("provider-" + suffix).issuer,
                "subject": "oidc-alice-" + suffix,
                "user_id": "member",
                "verified_at": utc_now(),
                "created_at": utc_now(),
            }
        )
    await Invitations(h.local).create(
        "org-a",
        "owner",
        InviteCreate(
            email="new@example.test",
            display_name="Fictional New Member",
            policy=ReviewedPolicy(
                parent_id=h.root,
                expected_parent_generation=h.root_generation,
                expires_at=utc_now() + timedelta(days=1),
            ),
            expires_at=utc_now() + timedelta(hours=1),
        ),
    )
    if os.environ.get("V6_PHASE10_BROWSER") == "1":
        from app.security.policy.models import Issue
        from tests.v6_managed_agent_fixtures import g
        from tests.v6_management_fixtures import team_grants

        await h.governance.change("org-a", "owner", h.lead["_id"], 1, revoke=True)
        own = tuple(
            g(a, "member", ("member",))
            for a in (
                "credentials:issue:own",
                "credentials:revoke:own",
                "clients:read:own",
                "clients:manage:own",
            )
        )
        h.lead = await h.governance.issue(
            "org-a",
            "owner",
            Issue(
                subject_id="lead",
                role="team_lead",
                parent_id=h.root,
                expected_parent_generation=3,
                tuples=(
                    *team_grants(),
                    *(
                        g(a, "team", ("alpha",))
                        for a in (
                            "clients:manage:team",
                            "clients:read:team",
                            "credentials:revoke:team",
                        )
                    ),
                ),
                ceiling=h.domain + own,
                remaining_depth=1,
                allowed_roles=("contributor",),
                expires_at=utc_now() + timedelta(days=4),
                team_id="alpha",
                expected_team_generation=1,
            ),
        )
    # Snapshot all persisted fixture collections, including private identity rows,
    # to give each browser journey a fresh real database without a test HTTP API.
    names = await h.db.list_collection_names()
    snapshots = {name: await h.db[name].find({}).to_list() for name in names}
    mail_root = h.local.mail.directory
    mail_snapshot = {p.name: p.read_bytes() for p in mail_root.glob("*.json")}
    dist = Path(__file__).resolve().parents[2] / "dashboard/dist"
    assert (dist / "index.html").is_file(), "build the actual frontend first"

    @h.app.get("/console/{asset:path}", include_in_schema=False)
    async def console_assets(asset: str):
        selected = (dist / asset).resolve()
        if not selected.is_relative_to(dist) or not selected.is_file():
            selected = dist / "index.html"
        return FileResponse(
            selected,
            headers={
                "Cache-Control": "no-store",
                "Content-Security-Policy": (
                    "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
                    "img-src 'self'; connect-src 'self'; font-src 'self'; object-src 'none'; "
                    "base-uri 'none'; frame-ancestors 'none'; form-action 'self'"
                ),
            },
        )

    tls = Path(registration_file).parent
    config = uvicorn.Config(
        h.app,
        host="127.0.0.1",
        port=urlsplit(origin).port,
        ssl_keyfile=str(tls / "server.key"),
        ssl_certfile=str(tls / "server.crt"),
        lifespan="off",
        access_log=False,
        log_level="warning",
    )
    server = uvicorn.Server(config)
    task = asyncio.create_task(server.serve())
    for _ in range(200):
        if server.started:
            break
        if task.done():
            await task
        await asyncio.sleep(0.05)
    assert server.started
    payload = {
        "fixture": "acp-phase9-disposable",
        "origin": origin,
        "requests": str(requests),
        "password": PASSWORD,
        "provider_password": "disposable-provider-password",
    }
    temporary = descriptor.with_suffix(".pending")
    temporary.write_text(json.dumps(payload))
    temporary.chmod(0o600)
    temporary.replace(descriptor)
    child = None
    if os.environ.get("V6_BROWSER_SERVE_ONLY") != "1":
        child = await asyncio.create_subprocess_exec(
            "npm",
            "--prefix",
            "dashboard",
            "run",
            "test:e2e",
            "--",
            "e2e/auth.spec.ts",
            "e2e/org-switch.spec.ts",
            "e2e/my-security.spec.ts",
        )
    last_provider_code = ""
    try:
        while not descriptor.with_suffix(".stop").exists() and not (
            child and child.returncode is not None
        ):
            for request in sorted(requests.glob("*.request")):
                command = json.loads(request.read_text())
                try:
                    action = command["action"]
                    result = None
                    if action == "reset":
                        for name in names:
                            await h.db[name].delete_many({})
                            if snapshots[name]:
                                await h.db[name].insert_many(snapshots[name])
                        for path in mail_root.glob("*.json"):
                            path.unlink()
                        for name, contents in mail_snapshot.items():
                            (mail_root / name).write_bytes(contents)
                    elif action == "knowledge":
                        from tests.v6_knowledge_fixtures import prepare_knowledge

                        await prepare_knowledge(h)
                    elif action == "knowledge-conflict":
                        await h.db.project_context.update_many(
                            {"org_id": "org-a", "project_id": "payment-service"},
                            {
                                "$set": {"content": "Concurrent saved revision"},
                                "$inc": {"revision": 1},
                            },
                        )
                    elif action == "mail":
                        messages = [
                            json.loads(p.read_text())
                            for p in sorted(
                                mail_root.glob("*.json"), key=lambda p: p.stat().st_mtime_ns
                            )
                        ]
                        result = next(
                            m["token"]
                            for m in reversed(messages)
                            if m["purpose"] == command["purpose"]
                            and m["recipient"] == command["email"]
                        )
                    elif action == "recovery":
                        result = recoveries[command.get("user", "member")][command.get("index", 0)]
                    elif action == "totp":
                        result = pyotp.TOTP(
                            command.get("seed") or factors[command.get("user", "member")]
                        ).now()
                    elif action == "provider-totp":
                        seed = (tls / "totp").read_text()
                        result = pyotp.TOTP(seed).now()
                        for _ in range(125):
                            if result != last_provider_code:
                                break
                            await asyncio.sleep(0.25)
                            result = pyotp.TOTP(seed).now()
                        assert result != last_provider_code
                        last_provider_code = result
                    elif action == "stale-mfa":
                        await h.db.browser_sessions.update_many(
                            {"user_id": command.get("user", "member")},
                            {"$set": {"mfa_at": utc_now() - timedelta(minutes=11)}},
                        )
                    elif action == "expire":
                        await h.db.browser_sessions.update_many(
                            {"user_id": command.get("user", "member")},
                            {"$set": {"expires_at": utc_now() - timedelta(seconds=1)}},
                        )
                    elif action == "prefer-b":
                        await h.local.sso.configure(
                            "org-b",
                            "provider-b",
                            "sso_preferred",
                            "fictional-browser-switch-review",
                        )
                    elif action == "local-only":
                        await h.local.sso.configure(
                            "org-a",
                            "provider-a",
                            "local_only",
                            "fictional-browser-local-only-review",
                        )
                    elif action == "identity-count":
                        result = await h.db.user_identities.count_documents(
                            {"user_id": command.get("user", "lead")}
                        )
                    elif action == "factor-active":
                        result = bool(
                            await h.db.mfa_factors.find_one(
                                {"_id": command.get("user", "member"), "status": "active"}
                            )
                        )
                    else:
                        raise AssertionError("unknown bounded fixture operation")
                    answer = {"result": result}
                except Exception as error:
                    answer = {"error": type(error).__name__}
                request.with_suffix(".reply").write_text(json.dumps(answer))
                request.unlink()
            await asyncio.sleep(0.02)
        if child:
            assert await child.wait() == 0, (
                "real browser acceptance failed; inspect private Playwright artifacts"
            )
    finally:
        server.should_exit = True
        await task
        descriptor.unlink(missing_ok=True)
        descriptor.with_suffix(".stop").unlink(missing_ok=True)
