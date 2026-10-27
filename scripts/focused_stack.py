"""Real owned MongoDB/mongot fixtures, independent of any test-suite wrapper.

No deployment dotenv, existing volumes, provider credentials or model downloads.
The existing V6 Compose inputs supply pinned images and disposable mount contracts.
"""

import base64
import json
import os
import re
import secrets
import shutil
import socket
import ssl
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import quote

import yaml
from pymongo import MongoClient
from pymongo.errors import PyMongoError
from pymongo.operations import SearchIndexModel

from scripts.focused_process import FocusedError, run

ROOT = Path(__file__).resolve().parents[1]
LABEL = "org.agent-context.focused-run"


def free_port():
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


def assert_owned(kind: str, value: dict, *, name: str, run_id: str, project: str):
    labels = (
        value.get("Config", {}).get("Labels", {})
        if kind == "container"
        else value.get("Labels", {})
    )
    if (
        value.get("Name", "").lstrip("/") != name
        or labels.get(LABEL) != run_id
        or labels.get("com.docker.compose.project") != project
    ):
        raise FocusedError("foreign resource: cleanup refused before any removal")


class OwnedStack:
    def __init__(
        self, run_id: str, env: dict[str, str], report: Path, *, oidc=False, hosting=False
    ):
        if not re.fullmatch(r"[a-f0-9]{32}-[a-z0-9-]{1,40}", run_id):
            raise FocusedError("invalid fixture identity")
        self.run_id, self.env, self.report, self.oidc = run_id, dict(env), report, oidc
        self.hosting = hosting
        if hosting and not oidc:
            raise FocusedError("hosted fixture requires its real TLS provider fixture")
        self.state = Path(tempfile.gettempdir()) / ("acp-v6-phase2." + run_id)
        self.project = "acp-focused-" + run_id
        self.manifest = self.state / "compose.json"
        self.containers = ["acp-v6-phase2-" + x + "-" + run_id for x in ("mongodb", "mongot")]
        if oidc:
            self.containers.append("acp-v6-oidc-keycloak-" + run_id)
        self.network = "acp-v6-phase2-isolated-" + run_id
        self.hosting_project = "acp-v6-hosting-" + run_id
        self.hosting_containers = (
            ["acp-v6-hosting-" + name + "-" + run_id for name in ("context-api", "console")]
            if hosting
            else []
        )
        self.containers += self.hosting_containers
        self.hosting_networks = (
            ["acp-v6-hosting-" + name + "-" + run_id for name in ("ingress", "public")]
            if hosting
            else []
        )
        self.networks = [self.network, *self.hosting_networks]
        self.created = False
        self.owner = secrets.token_hex(32)
        self.env.update(DOCKER_CONFIG=str(self.state / "docker"))
        self.docker = [
            "docker",
            "--host",
            "unix:///var/run/docker.sock",
            "--config",
            self.env["DOCKER_CONFIG"],
        ]
        self.compose = [
            *self.docker,
            "compose",
            "--env-file",
            "/dev/null",
            "--project-name",
            self.project,
            "-f",
            str(self.manifest),
        ]
        self.counter = 0

    def graph(self):
        sources = [ROOT / "tests/infrastructure/v6-isolation.compose.yml"]
        if self.oidc:
            sources.append(ROOT / "tests/infrastructure/v6-oidc.compose.yml")
        images = sorted(
            {
                service["image"]
                for path in sources
                for service in yaml.safe_load(path.read_text())["services"].values()
            }
        )
        if self.hosting:
            images += ["acp-v6-api:phase15", "acp-v6-console:phase15"]
        return {
            "state": str(self.state),
            "project": self.project,
            "resources": {
                "containers": self.containers,
                "network": self.network,
                "networks": self.networks,
                "volumes": [],
            },
            "compose_sources": [str(path.relative_to(ROOT)) for path in sources],
            "prepare": "exclusive 0700 state, fresh credentials, reviewed Compose inputs, no pulls",
            "commands": [
                *[[*self.docker, "container", "inspect", name] for name in self.containers],
                *[[*self.docker, "network", "inspect", name] for name in self.networks],
                *[[*self.docker, "image", "inspect", image] for image in images],
                *(
                    [[str(ROOT / ".venv/bin/python"), "tests/infrastructure/v6_oidc_prepare.py"]]
                    if self.oidc
                    else []
                ),
                [*self.compose, "up", "-d", "--pull", "never", "mongodb"],
                [
                    *self.docker,
                    "exec",
                    self.containers[0],
                    "mongosh",
                    "--quiet",
                    "--file",
                    "/bootstrap.js",
                ],
                [
                    *self.docker,
                    "exec",
                    self.containers[0],
                    "mongosh",
                    "--quiet",
                    "--file",
                    "/sync.js",
                ],
                [*self.compose, "port", "mongodb", "27017"],
                [*self.compose, "up", "-d", "--pull", "never", "mongot"],
            ]
            + (
                [
                    [*self.compose, "up", "-d", "--pull", "never", "keycloak"],
                ]
                if self.oidc
                else []
            ),
            "readiness": [
                "replica primary <=120s",
                "real lexical/vector index + queries <=120s",
                "TLS OIDC <=180s when selected",
            ],
            "cleanup": [
                "validate all exact names/labels before removal",
                "remove owned IDs then owned network; no prune/down",
            ],
            "cleanup_commands": [
                *[[*self.docker, "container", "inspect", name] for name in self.containers],
                *[[*self.docker, "network", "inspect", name] for name in self.networks],
                *[[*self.docker, "logs", name] for name in self.containers],
                [*self.docker, "rm", "-f", "<each ownership-verified container Id>"],
                [*self.docker, "network", "rm", "<ownership-verified network Id>"],
            ],
            "command_timeout_seconds": 30,
            "up_timeout_seconds": 60,
            "replica_poll_command_timeout_seconds": 10,
            "runtime_bindings": ["fresh password URI (never printed)", "ephemeral loopback ports"],
            "hosting": {
                "enabled": self.hosting,
                "source": "tests/v6_deployment_fixtures.py::hosted_stack",
                "project": self.hosting_project,
                "images": ["acp-v6-api:phase15", "acp-v6-console:phase15"] if self.hosting else [],
                "commands": [
                    [
                        *self.docker,
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
                    ],
                    [
                        *self.docker,
                        "compose",
                        "-f",
                        "<owned fixture root>/compose.json",
                        "up",
                        "-d",
                        "--pull",
                        "never",
                    ],
                    [
                        *self.docker,
                        "compose",
                        "-f",
                        "<owned fixture root>/compose.json",
                        "exec",
                        "-T",
                        "context-api",
                        "python",
                        "-c",
                        "from app.services.tenant_isolation_service import source_fingerprint; "
                        "print(source_fingerprint())",
                    ],
                ]
                if self.hosting
                else [],
                "readiness_seconds": 90,
                "cleanup": "exact labels/names/project verified by shared owned cleanup, "
                "including parent fallback",
            },
            "limits": {
                "mongodb_memory": "2g",
                "mongot_memory": "2g",
                "keycloak_memory": "1g" if self.oidc else None,
                "api_memory": "2g" if self.hosting else None,
                "console_memory": "128m" if self.hosting else None,
            },
        }

    def command(self, argv, *, required=True, timeout=30):
        self.counter += 1
        log = self.report / f"infrastructure-{self.counter}.log"
        code = run(argv, cwd=ROOT, env=self.env, log=log, timeout=timeout)
        if required and code:
            raise FocusedError(f"infrastructure command failed ({code}); inspect private log")
        return code, log.read_text()

    def inspect(self, kind, name):
        code, body = self.command([*self.docker, kind, "inspect", name], required=False)
        if code:
            if "No such" in body or "not found" in body:
                return None
            raise FocusedError("cannot establish resource ownership; cleanup refused")
        return json.loads(body)[0]

    def prepare(self):
        if self.state.is_symlink() or self.state.exists():
            raise FocusedError("fixture state already exists; refusing reuse")
        self.state.mkdir(mode=0o700)
        self.created = True
        (self.state / "owner").write_text(self.owner)
        (self.state / "owner").chmod(0o600)
        (self.state / "docker").mkdir(mode=0o700)
        for name in ("root", "sync", "keyfile"):
            data = (
                base64.b64encode(secrets.token_bytes(756)).decode()
                if name == "keyfile"
                else secrets.token_urlsafe(32)
            )
            (self.state / name).write_text(data)
            (self.state / name).chmod(0o444)  # parent is 0700, container reads only its fresh mount
        self.env.update(
            V6_TEST_RUN=self.run_id,
            V6_TEST_DIR=str(self.state),
            V6_REPOSITORY_ROOT=str(ROOT),
            V6_TEST_MONGODB_MEMORY_LIMIT="2g",
            V6_TEST_MONGODB_CONTAINER=self.containers[0],
            V6_REPORT_DIR=str(self.report),
        )
        if self.oidc:
            self.env.update(
                V6_OIDC_PORT=str(free_port()),
                V6_BROWSER_TEST_ORIGIN=f"https://127.0.0.1:{free_port()}",
            )
        if self.hosting:
            self.env.update(
                ACP_FOCUSED_RUN_ID=self.run_id,
                ACP_FOCUSED_STATE=str(self.state),
                ACP_FOCUSED_OWNER=self.owner,
                V6_DEPLOYMENT_TESTS="1",
            )
        text = (ROOT / "tests/infrastructure/v6-isolation.compose.yml").read_text()
        text = re.sub(r"\$\{([A-Z_]+)(?::[-?][^}]*)?\}", lambda m: self.env[m[1]], text)
        config = yaml.safe_load(text)
        config["name"] = self.project
        if self.oidc:
            text = (ROOT / "tests/infrastructure/v6-oidc.compose.yml").read_text()
            text = re.sub(r"\$\{([A-Z_]+)(?::[-?][^}]*)?\}", lambda m: self.env[m[1]], text)
            provider = yaml.safe_load(text)["services"]["keycloak"]
            provider["networks"] = ["isolated"]
            config["services"]["keycloak"] = provider
        for service in config["services"].values():
            service["labels"] = {LABEL: self.run_id}
            service["pull_policy"] = "never"
        config["networks"]["isolated"]["labels"] = {LABEL: self.run_id}
        self.manifest.write_text(json.dumps(config))
        self.manifest.chmod(0o600)
        for name in self.containers:
            if self.inspect("container", name):
                raise FocusedError("existing container refused")
        for name in self.networks:
            if self.inspect("network", name):
                raise FocusedError("existing network refused")
        images = sorted({s["image"] for s in config["services"].values()})
        if self.hosting:
            images += ["acp-v6-api:phase15", "acp-v6-console:phase15"]
        for image in images:
            self.command([*self.docker, "image", "inspect", image])
        if self.oidc:
            self.command(
                [str(ROOT / ".venv/bin/python"), "tests/infrastructure/v6_oidc_prepare.py"]
            )

    def __enter__(self):
        try:
            self.prepare()
            self.command([*self.compose, "up", "-d", "--pull", "never", "mongodb"], timeout=60)
            deadline = time.monotonic() + 120
            while True:
                good = all(
                    self.command(
                        [
                            *self.docker,
                            "exec",
                            self.containers[0],
                            "mongosh",
                            "--quiet",
                            "--file",
                            script,
                        ],
                        required=False,
                        timeout=10,
                    )[0]
                    == 0
                    for script in ("/bootstrap.js", "/sync.js")
                )
                if good:
                    break
                if time.monotonic() >= deadline:
                    raise FocusedError("disposable replica set did not become ready")
                time.sleep(1)
            _, endpoint = self.command([*self.compose, "port", "mongodb", "27017"])
            endpoint = endpoint.strip()
            if not re.fullmatch(r"127\.0\.0\.1:[0-9]+", endpoint):
                raise FocusedError("non-loopback database refused")
            password = quote((self.state / "root").read_text(), safe="")
            self.env["V6_TEST_MONGODB_URI"] = (
                f"mongodb://v6-test-root:{password}@{endpoint}/?authSource=admin&replicaSet=acp_v6_phase2&directConnection=true"
            )
            self.command([*self.compose, "up", "-d", "--pull", "never", "mongot"], timeout=60)
            self.search_ready()
            if self.oidc:
                self.command([*self.compose, "up", "-d", "--pull", "never", "keycloak"], timeout=60)
                self.env["V6_OIDC_REGISTRATIONS_FILE"] = str(self.state / "oidc/registrations.json")
                context = ssl.create_default_context(cafile=self.state / "oidc/server.crt")
                url = f"https://127.0.0.1:{self.env['V6_OIDC_PORT']}/realms/v6-org-a/.well-known/openid-configuration"
                deadline = time.monotonic() + 180
                while True:
                    try:
                        with urllib.request.urlopen(url, context=context, timeout=2) as response:  # noqa: S310 -- fixed loopback HTTPS
                            if response.status == 200:
                                break
                    except (urllib.error.URLError, TimeoutError):
                        if time.monotonic() >= deadline:
                            raise FocusedError("disposable TLS provider not ready") from None
                    time.sleep(1)
            return self
        except BaseException:
            self.cleanup()
            raise

    def search_ready(self):
        with MongoClient(
            self.env["V6_TEST_MONGODB_URI"], serverSelectionTimeoutMS=5000, socketTimeoutMS=5000
        ) as client:
            if client.admin.command("hello").get("setName") != "acp_v6_phase2":
                raise FocusedError("unexpected replica identity")
            db = client["acp_focused_readiness_" + self.run_id[:32]]
            try:
                db.probe.insert_one({"content": "focusedreadiness", "embedding": [1.0, 0.0, 0.0]})
                deadline = time.monotonic() + 120
                created = False
                while True:
                    try:
                        if not created:
                            db.probe.create_search_indexes(
                                [
                                    SearchIndexModel(
                                        name="focused_text",
                                        definition={"mappings": {"dynamic": True}},
                                    ),
                                    SearchIndexModel(
                                        name="focused_vector",
                                        type="vectorSearch",
                                        definition={
                                            "fields": [
                                                {
                                                    "type": "vector",
                                                    "path": "embedding",
                                                    "numDimensions": 3,
                                                    "similarity": "cosine",
                                                }
                                            ]
                                        },
                                    ),
                                ]
                            )
                            created = True
                        indexes = list(db.probe.list_search_indexes())
                        if len(indexes) == 2 and all(
                            i.get("status") == "READY" and i.get("queryable") for i in indexes
                        ):
                            lexical = list(
                                db.probe.aggregate(
                                    [
                                        {
                                            "$search": {
                                                "index": "focused_text",
                                                "text": {
                                                    "path": "content",
                                                    "query": "focusedreadiness",
                                                },
                                            }
                                        }
                                    ]
                                )
                            )
                            vector = list(
                                db.probe.aggregate(
                                    [
                                        {
                                            "$vectorSearch": {
                                                "index": "focused_vector",
                                                "path": "embedding",
                                                "queryVector": [1.0, 0.0, 0.0],
                                                "numCandidates": 10,
                                                "limit": 1,
                                            }
                                        }
                                    ]
                                )
                            )
                            if len(lexical) == len(vector) == 1:
                                return
                    except PyMongoError:
                        pass
                    if time.monotonic() >= deadline:
                        raise FocusedError("real lexical/vector readiness timed out")
                    time.sleep(1)
            finally:
                client.drop_database(db.name)

    def check_state(self):
        if (
            self.state.is_symlink()
            or self.state.resolve() != self.state
            or self.state.stat().st_uid != os.getuid()
            or (self.state / "owner").is_symlink()
            or (self.state / "owner").read_text() != self.owner
        ):
            raise FocusedError("foreign fixture state; cleanup refused")

    def remove_resources(self, containers, networks):
        owned = []
        for name in containers:
            value = self.inspect("container", name)
            if value:
                assert_owned(
                    "container",
                    value,
                    name=name,
                    run_id=self.run_id,
                    project=self.hosting_project
                    if name in self.hosting_containers
                    else self.project,
                )
                owned.append((name, value["Id"]))
        owned_networks = []
        for name in networks:
            network = self.inspect("network", name)
            if network:
                assert_owned(
                    "network",
                    network,
                    name=name,
                    run_id=self.run_id,
                    project=self.hosting_project if name in self.hosting_networks else self.project,
                )
                owned_networks.append(network["Id"])
        # Validate every resource before making the first destructive call.
        for name, identifier in owned:
            self.command([*self.docker, "logs", name], required=False)
            self.command([*self.docker, "rm", "-f", identifier])
        for identifier in owned_networks:
            self.command([*self.docker, "network", "rm", identifier])

    def cleanup_hosting(self):
        self.check_state()
        self.remove_resources(self.hosting_containers, self.hosting_networks)

    def cleanup(self):
        if not self.created:
            return
        self.check_state()
        self.remove_resources(self.containers, self.networks)
        shutil.rmtree(self.state)
        self.created = False

    def __exit__(self, *_):
        self.cleanup()
