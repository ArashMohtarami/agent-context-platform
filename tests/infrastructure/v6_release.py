"""Whole-suite runner inside the already verified disposable MongoDB/mongot/IdP stack."""

import ast
import json
import os
import secrets
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET
from hashlib import sha256
from pathlib import Path
from urllib.parse import urlsplit

from pymongo import MongoClient

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from v6_acceptance import ROOT, matrix, reconcile, validate_inventory


def source_seal() -> dict[str, str | int]:
    from scripts.prepare_open_source_release import audited_files

    files = audited_files(ROOT)
    digest = sha256()
    for name, contents in sorted(files.items()):
        digest.update(str(name).encode() + b"\0")
        digest.update(contents)
    return {"public_source_sha256": digest.hexdigest(), "files": len(files)}


def finish(report: Path, *, development: bool, selected: list[str]) -> None:
    if not development:
        sealed = json.loads((report / "source-seal.json").read_text())
        if sealed != source_seal():
            raise ValueError("public source changed during the acceptance run")
    if selected and not development:
        raise ValueError("a release gate cannot narrow its module inventory")
    for name in selected:
        path = Path(name)
        assert (
            path.parent == Path("tests/integration")
            and path.is_file()
            and path.name.startswith("test_")
        )
    combined = ET.Element("testsuites")
    pieces = sorted((report / "modules").glob("*/integration.xml"))
    if not pieces:
        raise ValueError("no integration reports")
    for path in pieces:
        # Only this runner's locally generated XML, never an uploaded document.
        root = ET.parse(path).getroot()  # noqa: S314
        for suite in root.findall("testsuite"):
            combined.append(suite)
    ET.ElementTree(combined).write(
        report / "integration.xml", encoding="utf-8", xml_declaration=True
    )
    required = set() if development else validate_inventory(matrix())
    for path in (ROOT / "tests/integration").glob("test_*.py"):
        if selected and str(path.relative_to(ROOT)) not in selected:
            continue
        if development and path.name.startswith("test_v6_"):
            continue
        required.update(
            str(path.relative_to(ROOT)) + "::" + node.name
            for node in ast.parse(path.read_text()).body
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
            and node.name.startswith("test_")
        )
    fingerprints = None
    if not development:
        from app.services.tenant_isolation_service import source_fingerprint, suite_fingerprint

        fingerprints = {
            "v6_source_sha256": source_fingerprint(),
            "v6_tests_sha256": suite_fingerprint(),
        }
    counts = {
        "integration": reconcile(report / "integration.xml", required, fingerprints=fingerprints)
    }
    if not development:
        counts["unit"] = reconcile(report / "unit.xml", set(), fingerprints=fingerprints)
    if selected:
        counts["selected_modules"] = selected
    name = (
        "focused-development.json"
        if selected
        else "legacy-development.json"
        if development
        else "complete.json"
    )
    (report / name).write_text(json.dumps(counts, indent=2) + "\n")


def main() -> None:
    if len(sys.argv) == 3 and sys.argv[1] == "--seal":
        (Path(sys.argv[2]) / "source-seal.json").write_text(
            json.dumps(source_seal(), indent=2) + "\n"
        )
        return
    if len(sys.argv) >= 3 and sys.argv[1] == "--reconcile":
        finish(
            Path(sys.argv[2]).resolve(),
            development=os.environ.get("V6_LEGACY_DEVELOPMENT_ONLY") == "1",
            selected=sys.argv[3:],
        )
        return
    root = Path(os.environ["V6_TEST_DIR"])
    report = Path(sys.argv[1]).resolve()
    uri = urlsplit(os.environ["V6_TEST_MONGODB_URI"])
    container = os.environ["V6_TEST_MONGODB_CONTAINER"]
    assert uri.hostname == "127.0.0.1" and container.startswith("acp-v6-phase2-mongodb-")
    assert root.name.startswith("acp-v6-phase2.")
    tools = root / "tools"
    tools.mkdir(exist_ok=True)
    docker = shutil.which("docker")
    assert docker
    for name in ("mongodump", "mongorestore"):
        subprocess.run(  # noqa: S603 -- fixed executable and validated disposable container
            [docker, "cp", container + ":/usr/bin/" + name, str(tools / name)], check=True
        )
    password = secrets.token_urlsafe(32)
    password_file = root / "legacy-app-password"
    password_file.write_text(password)
    password_file.chmod(0o600)
    database = "acp_v6_release_legacy"
    with MongoClient(os.environ["V6_TEST_MONGODB_URI"]) as client:
        assert client.admin.command("hello")["setName"] == "acp_v6_phase2"
        client[database].command(
            "createUser",
            "release-app",
            pwd=password,
            roles=[
                {"role": "readWrite", "db": database},
                {"role": "dbAdmin", "db": database},
                {"role": "clusterMonitor", "db": "admin"},
            ],
        )
    # Whitelist environment: neither .env nor inherited live configuration can
    # choose a database, API, keyring, mail service or identity provider.
    env = {
        k: v
        for k, v in os.environ.items()
        if k.startswith("V6_")
        or k
        in {
            "PATH",
            "HOME",
            "LANG",
            "LC_ALL",
            "PLAYWRIGHT_CHROMIUM_EXECUTABLE",
            "DOCKER_HOST",
            "DOCKER_CONTEXT",
            "DOCKER_CONFIG",
            "XDG_RUNTIME_DIR",
        }
    }
    env.update(
        {
            "PATH": str(tools) + os.pathsep + env["PATH"],
            "PYTHONPATH": str(ROOT) + os.pathsep + str(ROOT / "tests"),
            "RUN_MONGODB_INTEGRATION": "1",
            "RUN_EMBEDDING_MODEL_TESTS": "1",
            "MONGODB_URI": f"mongodb://127.0.0.1:{uri.port}/?directConnection=true",
            "MONGODB_DATABASE": database,
            "MONGODB_USERNAME": "release-app",
            "MONGODB_PASSWORD_FILE": str(password_file),
            "MONGODB_REPLICA_SET": "acp_v6_phase2",
            "EMBEDDING_MODEL_CACHE_DIRECTORY": str(
                Path(os.environ["V6_ACCEPTANCE_MODEL_CACHE"]).resolve()
            ),
            "EMBEDDING_DEVICE": "cpu",
            "EMBEDDING_ALLOW_DOWNLOADS": "false",
            "HF_HUB_OFFLINE": "1",
            "HF_HUB_DISABLE_TELEMETRY": "1",
            "OMP_NUM_THREADS": "2",
            "TOKENIZERS_PARALLELISM": "false",
        }
    )
    target = Path(sys.argv[2])
    assert (
        target.parent == Path("tests/integration")
        and target.name.startswith("test_")
        and target.suffix == ".py"
        and target.is_file()
    )
    if target.name.startswith("test_v6_"):
        # V6 fixtures explicitly provision their own databases/credentials.
        # The legacy app's password file must not conflict with those Settings.
        env = {key: value for key, value in env.items() if not key.startswith("MONGODB_")}
    command = [
        sys.executable,
        "-m",
        "pytest",
        str(target),
        "-q",
        "--verbose",
        "--maxfail=15",
        "-p",
        "v6_release_plugin",
        "--junitxml=" + str(report / "integration.xml"),
    ]
    development = os.environ.get("V6_LEGACY_DEVELOPMENT_ONLY") == "1"
    if development:
        assert not target.name.startswith("test_v6_")
        env["V6_FULL_ACCEPTANCE"] = "1"
        print("Development legacy selector only; no release gate is claimed.", flush=True)
    print("Disposable integration module:", target.name, flush=True)
    result = subprocess.run(command, cwd=ROOT, env=env)  # noqa: S603 -- validated source target
    if result.returncode:
        raise SystemExit(result.returncode)
    required = {
        str(target) + "::" + node.name
        for node in ast.parse(target.read_text()).body
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
        and node.name.startswith("test_")
    }
    reconcile(report / "integration.xml", required)


if __name__ == "__main__":
    main()
