"""Owned, resumable local evaluation. Never reads deployment dotenv or client config."""

import argparse
import base64
import contextlib
import fcntl
import hashlib
import json
import os
import re
import secrets
import shutil
import signal
import socket
import ssl
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# The shell uses isolated Python; only this reviewed checkout supplies imports.
sys.path.insert(0, str(ROOT))

import yaml  # noqa: E402

from scripts.focused_process import FocusedError, run  # noqa: E402

STATE_ROOT = ROOT / ".private/evaluations"
LABEL = "org.agent-context.evaluation"
SERVICES = ("mongodb", "mongodb-init", "mongot", "operator", "context-api", "console")
INPUTS = ROOT / "deploy/evaluation/inputs.json"


def clean_env():
    # Fixed binaries resolve via system PATH, never inherited PATH/scripts.
    return {
        "PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
        "LANG": "C.UTF-8",
    }


def atomic(path, data):
    temporary = path.with_suffix(".pending")
    if path.is_symlink() or temporary.is_symlink():
        raise FocusedError("symlinked checkpoint refused")
    with open(temporary, "w", opener=lambda p, f: os.open(p, f | os.O_NOFOLLOW, 0o600)) as output:
        json.dump(data, output, indent=2)
        output.write("\n")
        output.flush()
        os.fsync(output.fileno())
    temporary.replace(path)


def file_hash(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def source_identity():
    from scripts.focused_runner import identity

    digest = hashlib.sha256(identity().encode())
    paths = [
        ROOT / "scripts/evaluate.sh",
        *ROOT.joinpath("scripts").glob("evaluation_*.py"),
        *ROOT.joinpath("deploy/evaluation").glob("*"),
    ]
    for path in sorted(p for p in paths if p.is_file()):
        digest.update(str(path.relative_to(ROOT)).encode() + b"\0" + path.read_bytes())
    return digest.hexdigest()


def validate_instance(value):
    if not isinstance(value, str) or not re.fullmatch(r"[a-f0-9]{32}", value):
        raise FocusedError("exact 32-character evaluation instance required")
    return value


def private_path(path, *, directory=True):
    if path.is_symlink() or path.resolve() != path.absolute():
        raise FocusedError("symlink/noncanonical state refused")
    if path.exists():
        info = path.stat()
        if info.st_uid != os.getuid() or info.st_mode & 0o077 or (directory and not path.is_dir()):
            raise FocusedError("state must be private and owned by the current user")


def validate_tree(path):
    private_path(path)
    for item in path.rglob("*"):
        if item.is_symlink() or (not item.is_dir() and not item.is_file()):
            raise FocusedError("symlink or special file in evaluation state refused")


def resource_names(instance):
    prefix = "acp-eval-" + validate_instance(instance)
    return {
        "container": [prefix + "-" + service for service in SERVICES],
        "network": [prefix + "-" + key for key in ("data", "ingress", "public")],
        "volume": [prefix + "-" + key for key in ("mongodb", "mongot")],
    }


def verify_resource(kind, value, name, instance):
    labels = (
        value.get("Config", {}).get("Labels", {})
        if kind == "container"
        else value.get("Labels", {})
    )
    if (
        value.get("Name", "").lstrip("/") != name
        or labels.get(LABEL) != instance
        or labels.get("com.docker.compose.project") != "acp-eval-" + instance
    ):
        raise FocusedError("foreign resource; no cleanup or restart performed")


def activation_evidence(folder):
    """Only a reconciled, current, ordered runner execution can be reused."""
    from app.services.tenant_isolation_service import verify_evidence
    from scripts.focused_runner import identity, load_profile, prepare_job, reconcile

    folder = Path(folder).absolute()
    allowed = ROOT / ".private/v7-competitive-planning"
    if (
        not (
            folder.is_relative_to(allowed)
            or (folder.parent.parent == STATE_ROOT and folder.name == "evidence")
        )
        or folder.resolve() != folder
    ):
        raise FocusedError("activation must use private focused-run evidence")
    plan = json.loads((folder / "plan.json").read_text())
    summary_path = folder / "activation-summary.json"
    summary = json.loads(summary_path.read_text())
    if summary["source_sha256"] != plan["source_sha256"]:
        raise FocusedError("activation dependency receipt source mismatch")
    if plan["source_sha256"] != identity() or summary["status"] != "passed":
        raise FocusedError("activation reports are stale/incomplete; plan and run activation again")
    required = ("activation-unit", "activation-real", "activation-cutover")
    configured = {job["id"]: job for job in load_profile("activation")}
    selected = [job for job in plan["jobs"] if job["id"] in required]
    if [job["id"] for job in selected] != list(required):
        raise FocusedError("all activation jobs in their original order are required")
    hashes = {}
    for job in selected:
        name = job["id"]
        expected = prepare_job(
            configured[name],
            job["nodes"],
            plan["run_id"],
            {},
            Path(job["report"]).parents[2],
            plan["node"],
        )
        # The full graph must be genuine; report roots may be phase-specific.
        if expected != job:
            raise FocusedError("activation execution graph does not match current selectors")
        target = folder / name
        if json.loads((target / "expected.json").read_text()) != job["nodes"]:
            raise FocusedError("activation selected-node identity mismatch")
        result = reconcile(json.loads((target / "result.json").read_text()), job["nodes"])
        recorded = next((r for r in summary["jobs"] if r["job"] == name), {})
        if result["status"] != "passed" or any(recorded.get(k) != v for k, v in result.items()):
            raise FocusedError("activation selected results are incomplete")
        for item in ("result.xml", "result.json", "expected.json"):
            path = target / item
            if path.is_symlink():
                raise FocusedError("symlinked evidence refused")
            hashes[name + "/" + item] = file_hash(path)
        if name != "activation-unit":
            path = target / "image-identities.json"
            image_ids = json.loads(path.read_text())
            pins = json.loads(INPUTS.read_text())
            if set(image_ids.values()) != set(_activation_images(pins).values()):
                raise FocusedError("activation service image identities changed")
            hashes[name + "/image-identities.json"] = file_hash(path)
    verify_evidence([folder / name / "result.xml" for name in required])
    hashes.update(
        {name: file_hash(folder / name) for name in ("plan.json", "activation-summary.json")}
    )
    return folder, hashes


def _activation_images(pins):
    result = {}
    with tempfile.TemporaryDirectory(prefix="acp-evaluation-image-check-") as config:
        for name in ("mongo_image", "search_image"):
            value = subprocess.run(  # noqa: S603 -- fixed Docker command and maintained image pins
                [
                    shutil.which("docker", path=clean_env()["PATH"]),
                    "--host",
                    "unix:///var/run/docker.sock",
                    "--config",
                    config,
                    "image",
                    "inspect",
                    pins[name],
                ],
                capture_output=True,
                text=True,
                check=True,
                timeout=30,
                env=clean_env(),
            )
            result[name] = json.loads(value.stdout)[0]["Id"]
    return result


class Evaluation:
    def __init__(self, instance):
        self.instance = validate_instance(instance)
        self.state = STATE_ROOT / instance
        self.project = "acp-eval-" + instance
        self.names = resource_names(instance)
        self.manifest = self.state / "manifest.json"
        self.env = clean_env()
        self.docker = [
            "docker",
            "--host",
            "unix:///var/run/docker.sock",
            "--config",
            str(self.state / "docker"),
        ]
        self.compose = [
            *self.docker,
            "compose",
            "--env-file",
            "/dev/null",
            "--project-name",
            self.project,
            "-f",
            str(self.state / "compose.json"),
        ]
        self.counter = 0
        self.data = None

    def command(self, argv, *, timeout=60, required=True):
        # Durable unique log names permit resume without overwriting failed work.
        log = self.state / "logs" / (str(time.time_ns()) + ".log")
        code = run(argv, cwd=ROOT, env=self.env, log=log, timeout=timeout)
        if code and required:
            raise FocusedError(f"evaluation command failed ({code}); private log: {log}")
        return code, log.read_text()

    def inspect(self, kind, name):
        code, body = self.command([*self.docker, kind, "inspect", name], required=False)
        if code:
            if "no such" in body.casefold() or "not found" in body.casefold():
                return None
            raise FocusedError("cannot establish Docker ownership")
        return json.loads(body)[0]

    def load(self, *, resume=False, check_files=True):
        private_path(STATE_ROOT)
        validate_tree(self.state)
        private_path(self.manifest, directory=False)
        self.data = json.loads(self.manifest.read_text())
        if self.data.get("version") != 1:
            raise FocusedError("unknown evaluation manifest version")
        if not re.fullmatch(r"https://127\.0\.0\.1:[0-9]{1,5}", self.data["origin"]):
            raise FocusedError("remote/non-HTTPS evaluation origin refused")
        if (
            self.data["instance"] != self.instance
            or self.data["state"] != str(self.state)
            or self.data["resources"] != self.names
            or self.data["project"] != self.project
        ):
            raise FocusedError("manifest belongs to another instance/path")
        for relative, expected in self.data["files"].items() if check_files else ():
            path = self.state / relative
            if (
                path.resolve() != path
                or not path.is_relative_to(self.state)
                or file_hash(path) != expected
            ):
                raise FocusedError("evaluation inputs changed; resume/cleanup refused")
        if resume and self.data["source"] != source_identity():
            raise FocusedError(
                "source changed; resume refused, scoped stop/reset remains available"
            )
        return self.data

    def owned(self):
        # Every resource is checked before even the first stop/removal.
        found = []
        for kind, names in self.names.items():
            for name in names:
                value = self.inspect(kind, name)
                if value:
                    verify_resource(kind, value, name, self.instance)
                    if kind == "container":
                        allowed_volumes = set(self.names["volume"])
                        for mount in value.get("Mounts", []):
                            if (
                                mount["Type"] == "volume"
                                and mount.get("Name") not in allowed_volumes
                            ):
                                raise FocusedError("foreign/live volume mount refused")
                            if mount["Type"] == "bind" and not any(
                                Path(mount["Source"]).is_relative_to(base)
                                for base in (self.state, ROOT / "scripts", ROOT / "docker/mongot")
                            ):
                                raise FocusedError("foreign bind mount refused")
                    found.append((kind, name, value.get("Id", name)))
        return found

    def checkpoint(self, stage, **values):
        self.data.update(stage=stage, **values)
        atomic(self.manifest, self.data)

    def maintenance(self, action):
        _, body = self.command(
            [
                *self.docker,
                "exec",
                self.names["container"][3],
                "python",
                "/operator/evaluation_operator.py",
                action,
            ],
            timeout=240,
        )
        return json.loads(body.strip().splitlines()[-1])

    def up(self, *services):
        self.command(
            [*self.compose, "up", "-d", "--no-deps", "--pull", "never", *services], timeout=120
        )

    def stop_traffic(self):
        found = self.owned()
        for kind, name, identifier in found:
            if kind == "container" and name.endswith(("-console", "-context-api")):
                self.command([*self.docker, "stop", "--time", "30", identifier])

    def ready(self):
        context = ssl.create_default_context(cafile=self.state / "tls/ca.crt")
        deadline = time.monotonic() + 240
        while True:
            try:
                with urllib.request.urlopen(  # noqa: S310 -- validated loopback HTTPS only
                    self.data["origin"] + "/health/ready", context=context, timeout=3
                ) as response:
                    if response.status == 200:
                        return
            except (urllib.error.URLError, TimeoutError):
                if time.monotonic() >= deadline:
                    raise FocusedError("HTTPS API readiness incomplete") from None
            time.sleep(1)

    def serve(self, stage):
        config = json.loads((self.state / "compose.json").read_text())
        env = config["services"]["context-api"]["environment"]
        for version, flag in (
            (38, "V6_LOCAL_IDENTITY"),
            (39, "V6_SSO"),
            (40, "V6_MANAGEMENT_POLICY"),
            (41, "V6_HUMAN_MANAGEMENT"),
            (42, "V6_MANAGED_AGENTS"),
            (42, "V6_CONSOLE_ENABLED"),
        ):
            env[flag] = str(stage >= version).lower()
        atomic(self.state / "compose.json", config)
        self.data["files"]["compose.json"] = file_hash(self.state / "compose.json")
        self.checkpoint("starting-https")
        self.up("context-api", "console")
        self.ready()

    def start(self):
        self.load(resume=True)
        if datetime.fromisoformat(self.data["expires_at"]) <= datetime.now(UTC):
            raise FocusedError(
                "reviewed evaluation authority expired; reset and commission a fresh instance"
            )
        self.owned()
        self.check_images()
        activation_evidence(self.state / "evidence")
        self.up("mongodb", "mongodb-init")
        self.command([*self.docker, "wait", self.names["container"][1]], timeout=240)
        value = self.inspect("container", self.names["container"][1])
        if value["State"]["ExitCode"]:
            raise FocusedError("database initialization failed")
        self.up("mongot", "operator")
        self.stop_traffic()
        from app.services.tenant_isolation_service import source_fingerprint

        _, fingerprint = self.command(
            [
                *self.docker,
                "exec",
                self.names["container"][3],
                "python",
                "-c",
                "from app.services.tenant_isolation_service import source_fingerprint; "
                "print(source_fingerprint())",
            ]
        )
        if fingerprint.strip() != source_fingerprint():
            raise FocusedError("API image does not contain the reviewed runtime")
        self.checkpoint("checking-search")
        self.maintenance("probe")
        snap = self.maintenance("snapshot")
        if snap["schema"] < 37:
            self.checkpoint("foundation")
            self.maintenance("foundation")
            for path in (self.state / "backup").iterdir():
                if path.is_file():
                    self.data["files"][str(path.relative_to(self.state))] = file_hash(path)
            self.checkpoint("foundation-verified")
        if snap["schema"] < 38:
            self.checkpoint("isolation")
            self.maintenance("isolation")
        if snap["schema"] < 40:
            self.checkpoint("invitations")
            snap = self.maintenance("invitations")
        if set(snap["members"]) == {"owner", "worker"} and all(
            m["active"] and m["mfa"] for m in snap["members"].values()
        ):
            self.checkpoint("management-cutover")
            snap = self.maintenance("management")
            self.serve(42)
            self.checkpoint("commissioned", members=snap["members"])
        else:
            self.serve(39)
            self.checkpoint("awaiting-enrollment", members=snap["members"])

    def check_images(self):
        for reference, expected in self.data["images"].items():
            _, body = self.command([*self.docker, "image", "inspect", reference])
            if json.loads(body)[0]["Id"] != expected:
                raise FocusedError("image identity changed; no resume")

    def stop(self):
        self.load(check_files=False)
        for kind, _, identifier in reversed(self.owned()):
            if kind == "container":
                self.command([*self.docker, "stop", "--time", "30", identifier], timeout=60)
        self.checkpoint("stopped", previous_stage=self.data["stage"])

    def reset(self):
        self.load(check_files=False)
        found = self.owned()
        print(
            json.dumps(
                {"reset_instance": self.instance, "state": str(self.state), "resources": self.names}
            ),
            flush=True,
        )
        self.checkpoint("resetting")
        for kind, _, identifier in found:
            if kind == "container":
                self.command([*self.docker, "rm", "-f", identifier])
        for kind, _, identifier in found:
            if kind in {"network", "volume"}:
                self.command([*self.docker, kind, "rm", identifier])
        validate_tree(self.state)
        shutil.rmtree(self.state)

    def archive_diagnostics(self, target):
        """Keep scoped failure proof before a test parent reclaims its resources."""
        self.load(check_files=False)
        target.mkdir(mode=0o700, parents=True, exist_ok=True)
        for path in [
            self.manifest,
            self.state / "compose.json",
            *(self.state / "logs").glob("*.log"),
        ]:
            if path.is_file() and not path.is_symlink():
                destination = target / path.name
                shutil.copyfile(path, destination)
                destination.chmod(0o600)


def preflight(api_image, console_image, evidence, instance):
    """Only an empty private client scratch directory; no installation mutation."""
    with tempfile.TemporaryDirectory(prefix="acp-evaluation-preflight-") as config:
        return _preflight(api_image, console_image, evidence, instance, config)


def _preflight(api_image, console_image, evidence, instance, docker_config):
    if sys.platform != "linux" or sys.version_info < (3, 12):
        raise FocusedError("Linux and Python 3.12+ required")
    private_path(STATE_ROOT)
    for binary in ("docker", "openssl"):
        if not shutil.which(binary, path=clean_env()["PATH"]):
            raise FocusedError("required executable unavailable: " + binary)
    if shutil.disk_usage(ROOT).free < 4 * 1024**3:
        raise FocusedError("less than 4GiB free disposable disk; setup incomplete")
    env = clean_env()

    def read_command(argv):
        result = subprocess.run(  # noqa: S603 -- fixed executable/argv, no shell
            argv,
            env=env,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=30,
            check=True,
        )
        return result.stdout

    docker = ["docker", "--host", "unix:///var/run/docker.sock", "--config", docker_config]
    if read_command([*docker, "info", "--format", "{{.OSType}}"]).strip() != "linux":
        raise FocusedError("local Linux Docker engine required")
    version = (
        read_command([*docker, "compose", "version", "--short"]).strip().lstrip("v").split("-")[0]
    )
    if tuple(map(int, version.split("."))) < (2, 24, 4):
        raise FocusedError("Compose 2.24.4+ required")
    for kind, names in resource_names(instance).items():
        for name in names:
            result = subprocess.run(  # noqa: S603 -- exact validated resource names, read only
                [*docker, kind, "inspect", name],
                env=env,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                timeout=30,
            )
            if not result.returncode or not (
                "no such" in result.stderr.casefold() or "not found" in result.stderr.casefold()
            ):
                raise FocusedError(
                    "new instance resource is present or ownership unknown; preflight refused"
                )
    inputs = json.loads(INPUTS.read_text())
    images = {}
    image_volumes = {}
    for reference in (inputs["mongo_image"], inputs["search_image"], api_image, console_image):
        if (
            not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9./_:@-]{1,250}", reference)
            or ":latest" in reference
            or (":" not in reference and "@sha256:" not in reference)
        ):
            raise FocusedError("explicit version/digest or reviewed local image required")
        metadata = json.loads(read_command([*docker, "image", "inspect", reference]))[0]
        images[reference] = metadata["Id"]
        image_volumes[metadata["Id"]] = sorted((metadata.get("Config") or {}).get("Volumes") or {})
    # Runtime comparison is read-only: image contents via a removed, private
    # filesystem are checked later before commissioning, never assumed by tag.
    # Choose a vacant ingress subnet from Docker's actual IPAM allocations.
    ids = read_command([*docker, "network", "ls", "-q"]).split()
    occupied = json.loads(read_command([*docker, "network", "inspect", *ids])) if ids else []
    import ipaddress

    subnets = [
        ipaddress.ip_network(c["Subnet"])
        for n in occupied
        for c in (n.get("IPAM") or {}).get("Config") or []
        if "Subnet" in c
    ]
    choices = list(range(1, 250))
    secrets.SystemRandom().shuffle(choices)
    subnet = next(
        (
            f"{prefix}.{n}.0/28"
            for prefix in ("10.203", "10.204", "172.30", "172.29")
            for n in choices
            if all(
                not ipaddress.ip_network(f"{prefix}.{n}.0/24").overlaps(other) for other in subnets
            )
        ),
        None,
    )
    if not subnet:
        raise FocusedError("no free isolated ingress subnet")
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    proposed = {"images": images, "subnet": subnet, "port": port}
    config = compose_config(Evaluation(instance), proposed, api_image, console_image)
    for service in config["services"].values():
        covered = {
            mount.split(":")[-2] if mount.split(":")[-1] in {"ro", "rw"} else mount.split(":")[-1]
            for mount in service.get("volumes", [])
        }
        covered.update(mount.split(":", 1)[0] for mount in service.get("tmpfs", []))
        if set(image_volumes[service["image"]]) - covered:
            raise FocusedError("unreviewed implicit image volume; no instance created")
    # Diagnose cheap platform/network prerequisites before the expensive test
    # evidence dependency. Both still precede any instance/service creation.
    evidence, hashes = activation_evidence(evidence)
    return {
        "images": images,
        "image_volumes": image_volumes,
        "evidence": evidence,
        "evidence_hashes": hashes,
        "subnet": subnet,
        "port": port,
    }


def root_grants(instance):
    """Explicit reviewed evaluation ceiling, including only own-org MCP discovery."""
    return [
        {
            "action": action,
            "resource_kind": "project_directory" if action == "projects:read" else "project",
            "selector": "all_in_org",
            "ids": [],
            "conditions": {},
        }
        for action in ("projects:read", "context:read", "context:write", "search:read")
    ] + [
        {
            "action": "agent:self",
            "resource_kind": "organization_knowledge",
            "selector": "include",
            "ids": ["eval-" + instance],
            "conditions": {},
        }
    ]


def compose_config(evaluation, inputs, api_image, console_image):
    # Safe-load the maintained base only. No Compose interpolation or implicit
    # .env/default volumes/secrets ever enter the generated execution graph.
    config = yaml.safe_load((ROOT / "docker-compose.yml").read_text())
    state, instance = evaluation.state, evaluation.instance
    dbname = "evaluation_" + instance
    label = {LABEL: instance}
    names = evaluation.names
    secret_names = {
        "mongodb_root_password": "root",
        "mongodb_app_password": "app",
        "mongodb_keyfile": "keyfile",
        "mongot_sync_password": "sync",
    }
    services = {
        name: config["services"][name] for name in SERVICES if name not in {"operator", "console"}
    }
    # Replace EVERY inherited setting/mount; keep maintained hardened commands.
    for name, service in services.items():
        service.pop("build", None)
        service.pop("profiles", None)
        service.pop("ports", None)
        service["restart"] = "no"
        service["container_name"] = evaluation.project + "-" + name
        service["labels"] = label
        service["pull_policy"] = "never"
        service["networks"] = {"data": {"aliases": [name + ".agent_context", name]}}
    root_env = {"MONGODB_DATABASE": dbname, "MONGODB_REPLICA_SET": "rs0"}
    services["mongodb"]["environment"] = {
        **root_env,
        "MONGO_INITDB_ROOT_USERNAME": "eval-root-" + instance,
        "MONGO_INITDB_ROOT_PASSWORD_FILE": "/run/secrets/mongodb-root-password",
        "MONGO_INITDB_DATABASE": "admin",
    }
    services["mongodb"]["command"] = [
        "rs0" if v.startswith("${MONGODB_REPLICA_SET") else v
        for v in services["mongodb"]["command"]
    ]
    services["mongodb"]["volumes"] = [
        "mongodb_data:/data/db",
        f"{ROOT}/scripts/mongodb-entrypoint.sh:/usr/local/bin/mongodb-entrypoint.sh:ro",
        f"{ROOT}/scripts/mongodb-healthcheck.sh:/usr/local/bin/mongodb-healthcheck.sh:ro",
    ]
    services["mongodb"]["mem_limit"] = "2g"
    services["mongodb"]["tmpfs"] = ["/data/configdb:size=67108864"]
    services["mongodb"]["mem_reservation"] = "512m"
    services["mongodb"]["cpus"] = 2
    services["mongodb-init"]["mem_limit"] = "256m"
    services["mongodb-init"]["tmpfs"] = ["/data/db:size=67108864", "/data/configdb:size=67108864"]
    services["mongodb-init"]["environment"] = {
        **root_env,
        "MONGODB_HOST": "mongodb",
        "MONGODB_PORT": "27017",
        "MONGODB_ROOT_USERNAME": "eval-root-" + instance,
        "MONGODB_APP_USERNAME": "eval-app-" + instance,
        "MONGOT_SYNC_USERNAME": "eval-sync-" + instance,
        "MONGODB_ROOT_PASSWORD_FILE": "/run/secrets/mongodb-root-password",
        "MONGODB_APP_PASSWORD_FILE": "/run/secrets/mongodb-app-password",
        "MONGOT_SYNC_PASSWORD_FILE": "/run/secrets/mongot-sync-password",
    }
    services["mongodb-init"]["volumes"] = [
        f"{ROOT}/scripts/{name}:/scripts/{name}:ro"
        for name in (
            "init-mongodb.sh",
            "init-replica-set.js",
            "create-application-user.js",
            "create-mongot-user.js",
        )
    ]
    services["mongot"]["environment"] = {
        "MONGOT_SYNC_USERNAME": "eval-sync-" + instance,
        "MONGOT_LOG_LEVEL": "WARN",
    }
    services["mongot"]["mem_limit"] = "2g"
    services["mongot"]["mem_reservation"] = "1g"
    services["mongot"]["cpus"] = 2
    services["mongot"]["volumes"] = [
        "mongot_data:/data/mongot",
        f"{ROOT}/docker/mongot/mongot.yml.template:/etc/mongot/mongot.yml.template:ro",
        f"{ROOT}/scripts/mongot-entrypoint.sh:/usr/local/bin/mongot-entrypoint.sh:ro",
        f"{ROOT}/scripts/mongot-healthcheck.sh:/usr/local/bin/mongot-healthcheck.sh:ro",
    ]
    prefix = inputs["subnet"].rsplit(".", 1)[0]
    environment = {
        **root_env,
        "APP_NAME": "Disposable fictional evaluation",
        "APP_ENV": "production",
        "API_AUTH_ENABLED": "true",
        "MONGODB_URI": "mongodb://mongodb:27017",
        "MONGODB_USERNAME": "eval-app-" + instance,
        "MONGODB_PASSWORD_FILE": "/run/secrets/mongodb-app-password",
        "V6_TENANT_FOUNDATION": "true",
        "V6_TENANT_ISOLATION": "true",
        "V6_LOCAL_IDENTITY": "true",
        "V6_SSO": "true",
        "V6_MANAGEMENT_POLICY": "false",
        "V6_HUMAN_MANAGEMENT": "false",
        "V6_MANAGED_AGENTS": "false",
        "V6_CONSOLE_ENABLED": "false",
        "V6_BROWSER_ORIGIN": f"https://127.0.0.1:{inputs['port']}",
        "V6_MFA_KEYRING_FILE": "/run/secrets/mfa",
        "V6_OIDC_REGISTRATIONS_FILE": "/run/secrets/oidc",
        "V6_MAIL_SINK_DIRECTORY": "/handoff",
        "BACKUP_DIRECTORY": "/backup",
        "API_PROXY_HEADERS": "true",
        "API_FORWARDED_ALLOW_IPS": prefix + ".2",
        "MCP_REMOTE_ALLOW_LOOPBACK_HTTP": "false",
        "EMBEDDING_ENABLED": "false",
        "EMBEDDING_ALLOW_DOWNLOADS": "false",
        "HF_HUB_OFFLINE": "1",
        "MONGODB_MIN_POOL_SIZE": "0",
        "SKILLS_DIRECTORY": "/app/skills",
    }
    api = services["context-api"]
    api.update(
        image=inputs["images"][api_image],
        environment=environment,
        user=f"{os.getuid()}:{os.getgid()}",
        mem_limit="2g",
        mem_reservation="512m",
        cpus=2,
    )
    api["secrets"] += [{"source": name, "target": name} for name in ("mfa", "oidc")]
    api["volumes"] = [f"{state}/handoff:/handoff", f"{state}/backup:/backup"]
    api["networks"] = {"data": {}, "ingress": {"ipv4_address": prefix + ".3"}}
    # Dedicated maintenance process, never reachable from ingress. DB credentials
    # stay in this instance, while humans use only HTTPS sessions.
    operator = json.loads(json.dumps(api))
    operator["environment"]["PYTHONPATH"] = "/app"
    operator.update(
        container_name=evaluation.project + "-operator",
        command=["python", "-c", "import time; time.sleep(86400)"],
        networks={"data": {}},
        depends_on={},
        healthcheck={"disable": True},
    )
    operator["volumes"] += [
        f"{ROOT}/scripts/evaluation_operator.py:/operator/evaluation_operator.py:ro",
        f"{state}/intent:/intent:ro",
        f"{state}/evidence:/evidence:ro",
    ]
    services["operator"] = operator
    services["console"] = {
        "image": inputs["images"][console_image],
        "container_name": evaluation.project + "-console",
        "labels": label,
        "pull_policy": "never",
        "restart": "no",
        "environment": {"CONSOLE_SERVER_NAME": "127.0.0.1", "CONSOLE_UPSTREAM": "context-api:8000"},
        "ports": [f"127.0.0.1:{inputs['port']}:8443"],
        "read_only": True,
        "tmpfs": ["/tmp:rw,noexec,nosuid,size=32m"],  # noqa: S108 -- container-only tmpfs
        "cap_drop": ["ALL"],
        "security_opt": ["no-new-privileges:true"],
        "pids_limit": 128,
        "mem_limit": "128m",
        "secrets": [
            {"source": "console_cert", "target": "console-cert"},
            {"source": "console_key", "target": "console-key"},
        ],
        "networks": {"ingress": {"ipv4_address": prefix + ".2"}, "public": {}},
    }
    for name in ("mongodb", "mongodb-init", "mongot"):
        services[name]["image"] = inputs["images"][
            json.loads(INPUTS.read_text())["search_image" if name == "mongot" else "mongo_image"]
        ]
    subnet_offsets = {"data": 16, "ingress": 0, "public": 32}
    cfg = {
        "name": evaluation.project,
        "services": services,
        "volumes": {
            key: {"name": name, "labels": label}
            for key, name in zip(("mongodb_data", "mongot_data"), names["volume"], strict=True)
        },
        "networks": {
            key: {
                "name": name,
                "labels": label,
                **({"internal": True} if key != "public" else {}),
                **({"ipam": {"config": [{"subnet": f"{prefix}.{subnet_offsets[key]}/28"}]}}),
            }
            for key, name in zip(("data", "ingress", "public"), names["network"], strict=True)
        },
        "secrets": {
            key: {"file": str(state / "secrets" / value)} for key, value in secret_names.items()
        },
    }
    cfg["secrets"].update({key: {"file": str(state / "secrets" / key)} for key in ("mfa", "oidc")})
    cfg["secrets"].update(
        {
            "console_cert": {"file": str(state / "tls/server.crt")},
            "console_key": {"file": str(state / "tls/server.key")},
        }
    )
    if "${" in json.dumps(cfg):
        raise FocusedError("unresolved inherited deployment input")
    return cfg


def create(instance, approval, inputs, api_image, console_image):
    evaluation = Evaluation(instance)
    private_path(STATE_ROOT)
    STATE_ROOT.mkdir(parents=True, exist_ok=True, mode=0o700)
    evaluation.state.mkdir(mode=0o700)  # exclusive: never reuse another installation
    (evaluation.state / "lock").touch(mode=0o600)
    evaluation.data = {
        "version": 1,
        "instance": instance,
        "state": str(evaluation.state),
        "project": evaluation.project,
        "resources": evaluation.names,
        "files": {},
        "source": source_identity(),
        "images": inputs["images"],
        "origin": f"https://127.0.0.1:{inputs['port']}",
        "stage": "preparing",
        "production_approval": False,
    }
    atomic(evaluation.manifest, evaluation.data)
    print(json.dumps({"evaluation_instance": instance, "state": str(evaluation.state)}), flush=True)
    for name in ("docker", "logs", "secrets", "tls", "handoff", "backup", "intent", "evidence"):
        (evaluation.state / name).mkdir(mode=0o700)
    for key in ("root", "app", "sync", "keyfile"):
        payload = (
            base64.b64encode(secrets.token_bytes(756)).decode()
            if key == "keyfile"
            else secrets.token_urlsafe(36)
        )
        path = evaluation.state / "secrets" / key
        path.write_text(payload)
        path.chmod(0o444)  # private 0700 parent; fresh container mounts
    atomic(
        evaluation.state / "secrets/mfa",
        {
            "active": "eval-1",
            "keys": {"eval-1": base64.urlsafe_b64encode(secrets.token_bytes(32)).decode()},
        },
    )
    (evaluation.state / "secrets/mfa").chmod(0o444)
    atomic(evaluation.state / "secrets/oidc", [])
    (evaluation.state / "secrets/oidc").chmod(0o444)
    # A per-instance CA and SAN leaf; no host trust modification or TLS bypass.
    openssl = ["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "3"]
    tls = evaluation.state / "tls"
    evaluation.command(
        [
            *openssl,
            "-subj",
            "/CN=ACP fictional evaluation CA " + instance,
            "-keyout",
            str(tls / "ca.key"),
            "-out",
            str(tls / "ca.crt"),
        ]
    )
    evaluation.command(
        [
            "openssl",
            "req",
            "-new",
            "-newkey",
            "rsa:2048",
            "-nodes",
            "-subj",
            "/CN=127.0.0.1",
            "-keyout",
            str(tls / "server.key"),
            "-out",
            str(tls / "server.csr"),
        ]
    )
    (tls / "extensions").write_text(
        "subjectAltName=IP:127.0.0.1\nbasicConstraints=CA:FALSE\nextendedKeyUsage=serverAuth\n"
    )
    evaluation.command(
        [
            "openssl",
            "x509",
            "-req",
            "-in",
            str(tls / "server.csr"),
            "-CA",
            str(tls / "ca.crt"),
            "-CAkey",
            str(tls / "ca.key"),
            "-CAcreateserial",
            "-days",
            "2",
            "-extfile",
            str(tls / "extensions"),
            "-out",
            str(tls / "server.crt"),
        ]
    )
    for path in tls.iterdir():
        path.chmod(0o444 if path.name in {"server.key", "server.crt", "ca.crt"} else 0o600)
    for relative in inputs["evidence_hashes"]:
        destination = evaluation.state / "evidence" / relative
        destination.parent.mkdir(exist_ok=True, mode=0o700)
        shutil.copyfile(inputs["evidence"] / relative, destination)
        destination.chmod(0o600)
    expiry = (datetime.now(UTC) + timedelta(hours=24)).isoformat()
    atomic(
        evaluation.state / "intent/intent.json",
        {
            "instance": instance,
            "org_id": "eval-" + instance,
            "adoption_id": "adoption-" + instance,
            "approval": approval,
            "expires_at": expiry,
        },
    )
    atomic(
        evaluation.state / "intent/root.json",
        root_grants(instance),
    )
    atomic(
        evaluation.state / "compose.json",
        compose_config(evaluation, inputs, api_image, console_image),
    )
    files = {
        str(p.relative_to(evaluation.state)): file_hash(p)
        for key in ("secrets", "tls", "intent", "evidence")
        for p in (evaluation.state / key).rglob("*")
        if p.is_file()
    }
    files["compose.json"] = file_hash(evaluation.state / "compose.json")
    evaluation.data = {
        "version": 1,
        "instance": instance,
        "state": str(evaluation.state),
        "project": evaluation.project,
        "resources": evaluation.names,
        "source": source_identity(),
        "images": inputs["images"],
        "files": files,
        "origin": f"https://127.0.0.1:{inputs['port']}",
        "expires_at": expiry,
        "stage": "prepared",
        "production_approval": False,
        "embedding_mode": json.loads(INPUTS.read_text())["mode"],
    }
    atomic(evaluation.manifest, evaluation.data)
    return evaluation


@contextlib.contextmanager
def locked(evaluation):
    private_path(evaluation.state)
    lock = evaluation.state / "lock"
    if lock.is_symlink():
        raise FocusedError("symlinked lock refused")
    private_path(lock, directory=False)
    with open(lock, "r+", opener=lambda p, f: os.open(p, f | os.O_NOFOLLOW)) as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise FocusedError("another operation owns this evaluation") from None
        yield


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["start", "status", "doctor", "stop", "reset"])
    parser.add_argument("--instance")
    parser.add_argument("--approve-root", metavar="REVIEW_REFERENCE")
    parser.add_argument("--activation-run", type=Path)
    parser.add_argument("--enroll", choices=["owner", "worker"])
    parser.add_argument("--approve-client", action="store_true")
    parser.add_argument("--token-output", type=Path)
    parser.add_argument("--api-image", default=json.loads(INPUTS.read_text())["api_image"])
    parser.add_argument("--console-image", default=json.loads(INPUTS.read_text())["console_image"])
    args = parser.parse_args(argv)
    evaluation = None

    def interrupted(signum, frame):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, interrupted)
    try:
        if args.command == "start" and (
            not args.instance
            or (
                args.approve_root
                and args.activation_run
                and not (STATE_ROOT / validate_instance(args.instance)).exists()
            )
        ):
            if not args.approve_root or not args.activation_run:
                raise FocusedError(
                    "review docs/evaluation.md, then supply --approve-root REFERENCE "
                    "and --activation-run RUN; no resources created"
                )
            if not 1 <= len(args.approve_root.strip()) <= 200:
                raise FocusedError("bounded explicit root review reference required")
            instance = args.instance or secrets.token_hex(16)
            inputs = preflight(args.api_image, args.console_image, args.activation_run, instance)
            evaluation = create(
                instance,
                args.approve_root,
                inputs,
                args.api_image,
                args.console_image,
            )
        else:
            if not args.instance:
                raise FocusedError(
                    "select --instance EXACT_ID (no implicit current/live installation)"
                )
            evaluation = Evaluation(args.instance)
        with locked(evaluation):
            evaluation.load(check_files=args.command in {"start", "doctor"})
            if evaluation.data["stage"] == "preparing" and args.command == "start":
                raise FocusedError(
                    "input preparation was interrupted; scoped reset then fresh start required"
                )
            if args.enroll or args.approve_client or args.token_output:
                if (
                    args.command != "start"
                    or sum(
                        bool(value)
                        for value in (args.enroll, args.approve_client, args.token_output)
                    )
                    != 1
                ):
                    raise FocusedError("select exactly one recipient action under start")
                evaluation.load(resume=True)
                evaluation.owned()
                from scripts.evaluation_recipient import approve_client, enroll, issue, session

                if args.enroll:
                    enroll(evaluation, args.enroll)
                elif evaluation.data["stage"] != "commissioned":
                    raise FocusedError(
                        "finish verified commissioning before client approval/issuance"
                    )
                elif args.approve_client:
                    print(json.dumps(approve_client(evaluation, session(evaluation, "owner"))))
                else:
                    print(
                        json.dumps(
                            issue(evaluation, session(evaluation, "worker"), args.token_output)
                        )
                    )
            elif args.command == "start":
                evaluation.start()
            elif args.command == "stop":
                evaluation.stop()
            elif args.command == "reset":
                evaluation.reset()
                return 0
            elif args.command == "doctor":
                evaluation.load(resume=True)
                evaluation.owned()
                evaluation.check_images()
                evaluation.ready()
            print(
                json.dumps(
                    {
                        k: evaluation.data[k]
                        for k in (
                            "instance",
                            "state",
                            "origin",
                            "stage",
                            "embedding_mode",
                            "production_approval",
                        )
                    },
                    indent=2,
                )
            )
        return 0
    except (
        FocusedError,
        OSError,
        ValueError,
        KeyError,
        subprocess.SubprocessError,
        KeyboardInterrupt,
    ) as error:
        if (
            evaluation
            and evaluation.manifest.is_file()
            and args.command in {"start", "stop", "reset"}
            and not (args.enroll or args.approve_client or args.token_output)
        ):
            try:
                evaluation.load(check_files=False)
                previous = evaluation.data["stage"]
                evaluation.stop()
                evaluation.checkpoint(
                    "interrupted" if isinstance(error, KeyboardInterrupt) else "failed",
                    previous_stage=previous,
                    error_type=type(error).__name__,
                )
            except (FocusedError, OSError, ValueError):
                pass  # refuse foreign state; retain original failure and manifest
        print(f"Evaluation incomplete: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
