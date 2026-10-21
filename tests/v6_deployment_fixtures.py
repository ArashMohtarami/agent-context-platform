"""Run real distribution images only against the explicitly disposable replica set."""

import asyncio
import base64
import ipaddress
import json
import os
import secrets
import shutil
import ssl
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlsplit

import httpx


async def docker(*args, env=None, check=True):
    process = await asyncio.create_subprocess_exec(
        "docker", *args, env=env, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    stdout, _stderr = await process.communicate()
    if check and process.returncode:
        report = Path(os.environ["V6_REPORT_DIR"]) / "deployment-commands.log"
        with report.open("a") as handle:
            handle.write(_stderr.decode("utf-8", "replace") + "\n")
        report.chmod(0o600)
    if check:
        assert process.returncode == 0, "disposable deployment command failed (output withheld)"
    return stdout.decode()


@asynccontextmanager
async def hosted_stack(h, origin, tls, root, *, local_only=False):
    endpoint = urlsplit(origin)
    assert endpoint.scheme == "https" and endpoint.hostname == "127.0.0.1" and endpoint.port
    run = os.environ["V6_TEST_RUN"]
    assert os.environ["V6_TEST_MONGODB_CONTAINER"].startswith("acp-v6-phase2-mongodb-")
    network = "acp-v6-phase2-isolated-" + run
    subnet = ipaddress.ip_network(f"10.240.{secrets.randbelow(250)}.0/28")
    proxy_ip, api_ip = str(subnet.network_address + 2), str(subnet.network_address + 3)
    root.mkdir(parents=True, exist_ok=True)
    # Ancestors must be traversable by the image's non-root nginx UID; no secrets in them.
    os.chmod(root, 0o755)  # noqa: S103 - synthetic fixture traversal by non-root nginx.
    for parent in root.parents:
        if parent == Path(os.environ["V6_TEST_DIR"]):
            os.chmod(parent, 0o755)  # noqa: S103 - disposable fixture ancestors only.
            break
        os.chmod(parent, 0o755)  # noqa: S103 - disposable fixture ancestors only.
    for name in ("secrets", "ca", "backups"):
        (root / name).mkdir(mode=0o755 if name != "backups" else 0o700, exist_ok=True)
    for source, target in (("server.crt", "console-cert"), ("server.key", "console-key")):
        shutil.copyfile(tls / source, root / "secrets" / target)
        (root / "secrets" / target).chmod(0o444)
    (root / "secrets" / "v6-mfa-keyring").write_text(
        json.dumps(
            {"active": "fixture", "keys": {"fixture": base64.urlsafe_b64encode(b"p" * 32).decode()}}
        )
    )
    password = "disposable-hosting-database-password"
    (root / "secrets" / "mongodb-app-password").write_text(password)
    users = await h.db.command("usersInfo", "v6-hosting-app")
    if not users["users"]:
        await h.db.command(
            "createUser",
            "v6-hosting-app",
            pwd=password,
            roles=[
                {"role": "readWrite", "db": h.db.name},
                {"role": "dbAdmin", "db": h.db.name},
                {"role": "clusterMonitor", "db": "admin"},
            ],
        )
    registrations = json.loads((tls / "registrations.json").read_text())
    provider = json.loads(await docker("inspect", "acp-v6-oidc-keycloak-" + run))[0]
    provider_ip = provider["NetworkSettings"]["Networks"][network]["IPAddress"]
    for registration in registrations:
        registration["connect_ip"] = provider_ip
        registration["ca_file"] = "/run/provider-ca/server.crt"
    (root / "secrets" / "v6-oidc-registrations").write_text(
        json.dumps([] if local_only else registrations)
    )
    shutil.copyfile(tls / "server.crt", root / "ca/server.crt")
    # Private host directory and non-root UID mapping, same read-only image as production.
    values = dict(
        APP_ENV="production",
        MONGODB_URI="mongodb://mongodb:27017",
        MONGODB_DATABASE=h.db.name,
        MONGODB_USERNAME="v6-hosting-app",
        MONGODB_PASSWORD_FILE="/run/secrets/mongodb-app-password",
        MONGODB_REPLICA_SET="acp_v6_phase2",
        EMBEDDING_ENABLED="false",
        METRICS_ENABLED="false",
        API_AUTH_ENABLED="true",
        API_PROXY_HEADERS="true",
        API_FORWARDED_ALLOW_IPS=proxy_ip,
        MCP_REMOTE_ALLOW_LOOPBACK_HTTP="false",
        V6_BROWSER_ORIGIN=origin,
        V6_MFA_KEYRING_FILE="/run/secrets/v6-mfa-keyring",
        V6_OIDC_REGISTRATIONS_FILE="/run/secrets/v6-oidc-registrations",
        V6_MAIL_SINK_DIRECTORY="/var/lib/acp-mail",
        LOG_LEVEL="WARNING",
        **{
            name: "true"
            for name in (
                "V6_TENANT_FOUNDATION",
                "V6_TENANT_ISOLATION",
                "V6_LOCAL_IDENTITY",
                "V6_SSO",
                "V6_MANAGEMENT_POLICY",
                "V6_HUMAN_MANAGEMENT",
                "V6_MANAGED_AGENTS",
                "V6_CONSOLE_ENABLED",
            )
        },
    )
    (root / "api.env").write_text("".join(f"{key}={value}\n" for key, value in values.items()))
    (root / "api.env").chmod(0o600)
    # Render the SHIPPED deployment, never read a deployment .env or use its volumes.
    env = dict(os.environ, **values)
    env.update(
        MONGODB_ROOT_USERNAME="v6-test-root",
        MONGODB_APP_USERNAME="v6-hosting-app",
        MONGOT_SYNC_USERNAME="v6-test-sync",
        CONSOLE_SERVER_NAME="127.0.0.1",
        CONSOLE_BIND_IP="127.0.0.1",
        CONSOLE_HTTPS_PORT=str(urlsplit(origin).port),
        CONSOLE_PROXY_IP=proxy_ip,
        CONSOLE_API_IP=api_ip,
        CONSOLE_SUBNET=str(subnet),
        CONSOLE_CERT_FILE=str(root / "secrets/console-cert"),
        CONSOLE_KEY_FILE=str(root / "secrets/console-key"),
        V6_MFA_KEYRING_SECRET=str(root / "secrets/v6-mfa-keyring"),
        V6_OIDC_REGISTRATIONS_SECRET=str(root / "secrets/v6-oidc-registrations"),
        V6_PROVIDER_CA_DIRECTORY=str(root / "ca"),
        V6_MAIL_HOST_DIRECTORY=str(h.local.mail.directory),
        MONGODB_ROOT_PASSWORD_SECRET=str(Path(os.environ["V6_TEST_DIR"]) / "root"),
        MONGODB_APP_PASSWORD_SECRET=str(root / "secrets/mongodb-app-password"),
        MONGODB_KEYFILE_SECRET=str(Path(os.environ["V6_TEST_DIR"]) / "keyfile"),
        MONGOT_SYNC_PASSWORD_SECRET=str(Path(os.environ["V6_TEST_DIR"]) / "sync"),
        BACKUP_HOST_DIRECTORY=str(root / "backups"),
    )
    rendered = json.loads(
        await docker(
            "compose",
            "--env-file",
            "/dev/null",
            "-f",
            "docker-compose.yml",
            "-f",
            "deploy/console/compose.yml",
            "config",
            "--format",
            "json",
            env=env,
        )
    )
    api = rendered["services"]["context-api"]
    console = rendered["services"]["console"]
    assert not api.get("ports"), "production overlay must remove every cleartext API port"
    for key in ("API_AUTH_ENABLED", "API_PROXY_HEADERS", "V6_CONSOLE_ENABLED"):
        assert api["environment"][key] == "true"
    assert api["environment"]["MCP_REMOTE_ALLOW_LOOPBACK_HTTP"] == "false"
    assert api["environment"]["API_FORWARDED_ALLOW_IPS"] == proxy_ip
    assert api["environment"]["MONGODB_MIN_POOL_SIZE"] == "50"
    assert console["build"]["target"] == "console"
    assert api["build"]["target"] == "production"
    # Retain shipped security/health/routing settings; replace ONLY test I/O and topology.
    for service, image in ((api, "acp-v6-api:phase15"), (console, "acp-v6-console:phase15")):
        service.pop("build", None)
        service.pop("depends_on", None)
        service.pop("secrets", None)
        service["image"] = image
        service["restart"] = "no"
    api["environment"].update(values)
    api["user"] = f"{os.getuid()}:{os.getgid()}"
    api["volumes"] = [
        str(root / "secrets") + ":/run/secrets:ro",
        str(root / "ca") + ":/run/provider-ca:ro",
        str(h.local.mail.directory) + ":/var/lib/acp-mail",
        str(root / "backups") + ":/backups",
    ]
    api["networks"] = {"fixture": {}, "console_ingress": {"ipv4_address": api_ip}}
    console["networks"] = {"console_ingress": {"ipv4_address": proxy_ip}, "console_public": {}}
    console["volumes"] = [
        str(root / "secrets/console-cert") + ":/run/secrets/console-cert:ro",
        str(root / "secrets/console-key") + ":/run/secrets/console-key:ro",
    ]
    ingress = rendered["networks"]["console_ingress"]
    ingress["name"] = "acp-v6-hosting-ingress-" + run
    selected = {
        "name": "acp-v6-hosting-" + run,
        "services": {"context-api": api, "console": console},
        "networks": {
            "console_ingress": ingress,
            "console_public": {"name": "acp-v6-hosting-public-" + run},
            "fixture": {"external": True, "name": network},
        },
    }
    manifest = root / "compose.json"
    manifest.write_text(json.dumps(selected))
    manifest.chmod(0o600)
    compose = ("compose", "-f", str(manifest))
    try:
        await docker(*compose, "up", "-d", env=env)
        async with (
            asyncio.timeout(90),
            httpx.AsyncClient(
                verify=ssl.create_default_context(cafile=tls / "server.crt"), timeout=2
            ) as client,
        ):
            for _attempt in range(180):
                try:
                    response = await client.get(origin + "/health/ready")
                    if response.status_code == 200:
                        break
                except httpx.HTTPError:
                    pass
                await asyncio.sleep(1)
            else:
                raise AssertionError(
                    "disposable production API/TLS gateway not ready; see private logs"
                )
        from app.services.tenant_isolation_service import source_fingerprint

        observed = (
            await docker(
                *compose,
                "exec",
                "-T",
                "context-api",
                "python",
                "-c",
                (
                    "from app.services.tenant_isolation_service import source_fingerprint; "
                    "print(source_fingerprint())"
                ),
                env=env,
            )
        ).strip()
        expected = source_fingerprint()
        assert observed == expected, (
            "production API image differs from the tested runtime source; rebuild it"
        )
        image_ids = {}
        for service in ("context-api", "console"):
            container_id = (await docker(*compose, "ps", "-q", service, env=env)).strip()
            image_ids[service] = (
                await docker("inspect", "--format", "{{.Image}}", container_id)
            ).strip()
        identity_report = Path(os.environ["V6_REPORT_DIR"]) / (
            "image-identity-" + root.name + ".json"
        )
        identity_report.write_text(
            json.dumps({"runtime_source_sha256": observed, "images": image_ids}, indent=2) + "\n"
        )
        if not local_only:
            for registration in registrations:
                org = await h.db.organizations.find_one({"org_id": registration["org_id"]})
                if org and org["status"] == "active":
                    await docker(
                        *compose,
                        "exec",
                        "-T",
                        "context-api",
                        "python",
                        "-m",
                        "app.cli.contextctl",
                        "--org-id",
                        registration["org_id"],
                        "identity",
                        "configure-sso",
                        "--registration-id",
                        registration["registration_id"],
                        "--policy",
                        org["auth_policy"],
                        "--approval-reference",
                        "fictional-hosted-provider-review",
                        env=env,
                    )
        yield env, compose
    finally:
        report = Path(os.environ["V6_REPORT_DIR"])
        (report / ("hosting-" + root.name + ".log")).write_text(
            await docker(*compose, "logs", "--no-color", env=env, check=False)
        )
        await docker(*compose, "down", "--remove-orphans", env=env, check=False)
