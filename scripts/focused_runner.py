"""Small manifest-driven focused runner. Planning collects named files, never fixtures."""

import argparse
import hashlib
import json
import os
import re
import signal
import sys
import time
from pathlib import Path

from scripts.focused_process import Child, FocusedError, run
from scripts.focused_stack import OwnedStack

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "tests/focused/v7-phases.json"
V8_MANIFEST = ROOT / "tests/focused/v8-phases.json"
V8_PHASES = frozenset(
    {"v8-p01-fixtures", "v8-p01-graders", "v8-p01-mutants", "v8-p01-runner", "v8-p01-export"}
)
EVIDENCE = ROOT / ".private/v7-competitive-planning/phase-1/runs"
PYTHON = str(ROOT / ".venv/bin/python")
BROWSER_HARNESS = "tests/integration/test_v6_dashboard_browser.py::test_real_dashboard_browser"


def evidence_folder(phase):
    if phase in V8_PHASES:
        return ROOT / ".private/v8-agent-value-planning/phase-1/runs" / phase
    if phase == "p07" or phase.startswith("p07-"):
        return EVIDENCE.parents[1] / "phase-7" / "runs" / phase
    if phase == "p06" or phase.startswith("p06-"):
        return EVIDENCE.parents[1] / "phase-6" / "runs" / phase
    if phase == "p05" or phase.startswith("p05-"):
        return EVIDENCE.parents[1] / "phase-5" / "runs" / phase
    if phase == "p04" or phase.startswith("p04-"):
        return EVIDENCE.parents[1] / "phase-4" / "runs" / phase
    if phase == "p03" or phase.startswith("p03-"):
        return EVIDENCE.parents[1] / "phase-3" / "runs" / phase
    if phase == "p02" or phase.startswith("p02-"):
        return EVIDENCE.parents[1] / "phase-2" / "runs" / phase
    return EVIDENCE / phase


def clean_environment(values):
    forbidden = [
        k
        for k, v in values.items()
        if v
        and (
            k.startswith(("V6_", "PYTEST_", "COMPOSE_", "ACP_FOCUSED_"))
            or k
            in {
                "PYTHONPATH",
                "PYTHONHOME",
                "NODE_OPTIONS",
                "NODE_PATH",
                "DOCKER_HOST",
                "DOCKER_CONTEXT",
            }
        )
        and k not in {"ACP_FOCUSED_NODE", "ACP_FOCUSED_CHROMIUM", "ACP_FOCUSED_MODEL_CACHE"}
    ]
    if forbidden:
        raise FocusedError(
            "inherited runner/fixture flags refused: " + ", ".join(sorted(forbidden))
        )
    env = {k: values[k] for k in ("PATH", "LANG", "LC_ALL") if k in values}
    env.update(
        PYTHONPATH=str(ROOT) + os.pathsep + str(ROOT / "tests"),
        PYTEST_DISABLE_PLUGIN_AUTOLOAD="1",
        HF_HUB_OFFLINE="1",
        HF_HUB_DISABLE_TELEMETRY="1",
        EMBEDDING_ALLOW_DOWNLOADS="false",
        RUN_EMBEDDING_MODEL_TESTS="0",
        TOKENIZERS_PARALLELISM="false",
        OMP_NUM_THREADS="2",
    )
    if values.get("ACP_FOCUSED_MODEL_CACHE"):
        env["ACP_FOCUSED_MODEL_CACHE"] = values["ACP_FOCUSED_MODEL_CACHE"]
    return env


def validate_selector(selector, root=ROOT):
    # Literal paths and exact function/parameter nodes only; no arbitrary pytest options.
    if not isinstance(selector, str) or not re.fullmatch(
        r"tests/(?:unit|integration|documentation)/test_[a-z0-9_]+\.py(?:::[A-Za-z_][A-Za-z0-9_]*(?:\[[A-Za-z0-9_.=-]+\])?)?",
        selector,
    ):
        raise FocusedError("not an explicit safe test selector")
    relative = selector.split("::", 1)[0]
    path = root / relative
    if not path.is_file() or path.resolve() != path.absolute():
        raise FocusedError("missing or symlinked test selector")
    return selector


def load_profile(phase, manifest=None):
    if manifest is None:
        manifest = V8_MANIFEST if phase in V8_PHASES else MANIFEST
    value = json.loads(manifest.read_text())
    if set(value) != {"version", "jobs", "profiles"} or value["version"] != 1:
        raise FocusedError("unknown manifest contract")
    ids = value["profiles"].get(phase)
    if not isinstance(ids, list) or not ids or len(ids) != len(set(ids)):
        raise FocusedError("unknown or empty profile")
    jobs = []
    for name in ids:
        if not re.fullmatch(r"[a-z][a-z0-9-]{0,39}", name):
            raise FocusedError("invalid job identity")
        job = value["jobs"].get(name)
        if not isinstance(job, dict) or set(job) != {
            "reason",
            "kind",
            "selectors",
            "fixture",
            "timeout",
            "expected_count",
            "requires",
        }:
            raise FocusedError("unknown job options")
        if (
            not job["reason"]
            or job["kind"] not in {"pytest", "browser"}
            or job["fixture"]
            not in {"none", "self", "mongo", "browser", "hosting", "browser-hosted", "evaluation"}
        ):
            raise FocusedError("unsupported or unexplained job")
        if type(job["timeout"]) is not int or not 1 <= job["timeout"] <= 1800:
            raise FocusedError("unbounded job timeout")
        if type(job["expected_count"]) is not int or job["expected_count"] < 1:
            raise FocusedError("nonempty expected selection required")
        selectors = job["selectors"]
        if not isinstance(selectors, list) or not selectors:
            raise FocusedError("empty job")
        if job["kind"] == "pytest":
            for selector in selectors:
                validate_selector(selector)
            if any(s.split("::")[0] == BROWSER_HARNESS.split("::")[0] for s in selectors):
                raise FocusedError("browser serving harness must use the bounded browser graph")
            if job["fixture"] in {"browser", "browser-hosted"}:
                raise FocusedError("browser fixture requires browser job")
        else:
            if job["fixture"] not in {"browser", "browser-hosted"} or len(selectors) != 1:
                raise FocusedError("one explicit browser title required")
            target = selectors[0]
            if not isinstance(target, dict) or set(target) != {"file", "title"}:
                raise FocusedError("browser selector requires file and title")
            if (
                not re.fullmatch(r"e2e/[a-z0-9-]+\.spec\.ts", target["file"])
                or not target["title"].strip()
            ):
                raise FocusedError("unsafe browser selection")
            path = ROOT / "dashboard" / target["file"]
            if not path.is_file() or path.resolve() != path.absolute():
                raise FocusedError("missing browser spec")
        if not isinstance(job["requires"], list) or any(
            dep not in ids[: ids.index(name)] for dep in job["requires"]
        ):
            raise FocusedError("dependencies must precede the job in the explicit profile")
        jobs.append({"id": name, **job})
    return jobs


def identity():
    from scripts.prepare_open_source_release import BENCHMARK_FILES

    paths = [
        *ROOT.joinpath("app").rglob("*.py"),
        *ROOT.joinpath("tests").rglob("*.py"),
        *ROOT.joinpath("tests/infrastructure").glob("v6-*"),
        *ROOT.joinpath("scripts").glob("focused*.py"),
        *ROOT.joinpath("scripts").glob("evaluation_*.py"),
        *ROOT.joinpath("deploy/evaluation").glob("*"),
        *ROOT.joinpath("dashboard/e2e").glob("*.ts"),
        *[ROOT / p for p in BENCHMARK_FILES],
        ROOT / "scripts/prepare_open_source_release.py",
        ROOT / "MANIFEST.in",
    ]
    paths += [
        ROOT / p
        for p in (
            "scripts/test-focused.py",
            "scripts/evaluate.sh",
            "tests/focused/v7-phases.json",
            "pyproject.toml",
            "requirements.lock",
            "requirements-dev.lock",
            "docker-compose.yml",
            ".env.example",
            "dashboard/package.json",
            "dashboard/package-lock.json",
            "dashboard/.node-version",
            "dashboard/playwright.config.ts",
            "dashboard/playwright.focused.config.ts",
            "scripts/mongodb-entrypoint.sh",
            "scripts/mongot-entrypoint.sh",
            "scripts/init-mongodb.sh",
            "scripts/init-replica-set.js",
            "scripts/create-application-user.js",
            "scripts/create-mongot-user.js",
            "scripts/mongodb-healthcheck.sh",
            "scripts/mongot-healthcheck.sh",
            "docker/mongot/mongot.yml.template",
            "Dockerfile",
            "deploy/console/compose.yml",
        )
    ]
    paths += [
        *ROOT.joinpath("examples/agent-handoff").glob("*.py"),
        *ROOT.joinpath("examples/agent-handoff").glob("*.json"),
        ROOT / "examples/starter-notes-api/notes_app.py",
        ROOT / "examples/starter-notes-api/test_app.py",
        ROOT / "docs/guide/content.md",
    ]
    digest = hashlib.sha256()
    for path in sorted(set(paths)):
        digest.update(str(path.relative_to(ROOT)).encode() + b"\0" + path.read_bytes())
    return digest.hexdigest()


def phase_identity(phase):
    """Retain the V7 identity algorithm; bind only reviewed V8 manifest routes."""
    previous = identity()
    if phase not in V8_PHASES:
        return previous
    return hashlib.sha256(previous.encode() + b"\0" + V8_MANIFEST.read_bytes()).hexdigest()


def pytest_argv(selectors, report, *, collect=False, expected=None):
    args = [
        PYTHON,
        "-m",
        "pytest",
        "-o",
        "addopts=",
        "--strict-config",
        "--strict-markers",
        "--import-mode=importlib",
        "-p",
        "pytest_asyncio.plugin",
        "-p",
        "focused_plugin",
        "--focused-report",
        str(report),
        "--basetemp",
        str(report.parent / (report.stem + "-tmp")),
        "-q",
    ]
    if collect:
        args.append("--collect-only")
    else:
        args += ["--junitxml", str(report.with_suffix(".xml"))]
    if expected:
        args += ["--focused-expected", str(expected)]
    return [*args, *selectors]


def browser_argv(job, node, *, collect=False):
    target = job["selectors"][0]
    return [
        node,
        str(ROOT / "dashboard/node_modules/@playwright/test/cli.js"),
        "test",
        "--config",
        "playwright.focused.config.ts",
        target["file"],
        "--grep",
        "^ *" + re.escape(Path(target["file"]).name + " " + target["title"]) + "$",
        "--workers=1",
        "--retries=0",
        *(["--list", "--reporter=json"] if collect else []),
    ]


def browser_cases(data):
    result = []

    def visit(suites):
        for suite in suites:
            for spec in suite.get("specs", []):
                for case in spec["tests"]:
                    relative = str(
                        (Path(data["config"]["rootDir"]) / spec["file"]).relative_to(
                            ROOT / "dashboard"
                        )
                    )
                    result.append(
                        {
                            "file": relative,
                            "title": spec["title"],
                            "project": case["projectName"],
                            "case": case,
                        }
                    )
            visit(suite.get("suites", []))

    visit(data.get("suites", []))
    return result


def selection_matches(selectors, nodes):
    if not nodes or len(nodes) != len(set(nodes)):
        raise FocusedError("empty or duplicate collection")
    for selector in selectors:
        if not any(
            n == selector or n.startswith(selector + "::") or n.startswith(selector + "[")
            for n in nodes
        ):
            raise FocusedError("missing selector in collection")
    if any(
        not any(n == s or n.startswith(s + "::") or n.startswith(s + "[") for s in selectors)
        for n in nodes
    ):
        raise FocusedError("collection widened beyond selected cases")


def reconcile(data, expected):
    counts = dict(
        selected=len(expected),
        collected=len(data.get("nodes", [])),
        passed=0,
        failed=0,
        errors=data.get("collection_errors", 0),
        skipped=0,
        xfailed=0,
    )
    if data.get("nodes") != expected:
        raise FocusedError("planned-versus-collected mismatch")
    events = data.get("events", [])
    if any(e["nodeid"] not in expected for e in events):
        raise FocusedError("unselected result")
    for node in expected:
        reports = [e for e in events if e["nodeid"] == node]
        phases = [e["when"] for e in reports]
        if any(e.get("xfail") for e in reports):
            counts["xfailed"] += 1
        elif any(e["outcome"] == "skipped" for e in reports):
            counts["skipped"] += 1
        elif any(e["outcome"] == "failed" for e in reports):
            counts["failed"] += 1
        elif phases == ["setup", "call", "teardown"] and all(
            e["outcome"] == "passed" for e in reports
        ):
            counts["passed"] += 1
        else:
            counts["errors"] += 1
    ok = (
        bool(expected)
        and data.get("exitstatus") == 0
        and counts["passed"] == len(expected)
        and counts["errors"] == 0
    )
    return {"status": "passed" if ok else "failed", **counts}


def write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_suffix(".pending")
    temporary.write_text(json.dumps(data, indent=2) + "\n")
    temporary.chmod(0o600)
    temporary.replace(path)


def collect(job, env, folder, node):
    report = folder / (job["id"] + "-collect.json")
    argv = (
        pytest_argv(job["selectors"], report, collect=True)
        if job["kind"] == "pytest"
        else browser_argv(job, node, collect=True)
    )
    log = report.with_suffix(".log")
    report.unlink(missing_ok=True)
    collection_env = dict(env)
    if job["kind"] == "browser":
        collection_env["ACP_FOCUSED_PLAN_ONLY"] = "1"
    code = run(
        argv,
        cwd=ROOT if job["kind"] == "pytest" else ROOT / "dashboard",
        env=collection_env,
        log=log,
        timeout=60,
    )
    if code:
        raise FocusedError("focused collection failed; inspect private log")
    if job["kind"] == "pytest":
        nodes = json.loads(report.read_text())["nodes"]
        selection_matches(job["selectors"], nodes)
    else:
        data = json.loads(log.read_text())
        cases = browser_cases(data)
        nodes = [c["file"] + "::" + c["title"] + "::" + c["project"] for c in cases]
        target = job["selectors"][0]
        if data.get("errors") or any(
            c["file"] != target["file"] or c["title"] != target["title"] for c in cases
        ):
            raise FocusedError("browser collection widened")
    if len(nodes) != job["expected_count"]:
        raise FocusedError(
            f"{job['id']}: expected {job['expected_count']} cases, collected {len(nodes)}"
        )
    return nodes


def tools_for(jobs, values, env, folder):
    node = values.get("ACP_FOCUSED_NODE", "node")
    chrome = values.get("ACP_FOCUSED_CHROMIUM", "/opt/google/chrome/chrome")
    if any(j["kind"] == "browser" for j in jobs):
        if not Path(chrome).is_file():
            raise FocusedError("explicit installed Chromium required")
        log = folder / "node-version.log"
        if run([node, "--version"], cwd=ROOT, env=env, log=log, timeout=10):
            raise FocusedError("Node unavailable")
        if log.read_text().strip() != "v" + (ROOT / "dashboard/.node-version").read_text().strip():
            raise FocusedError(
                "Node does not match dashboard/.node-version; choose ACP_FOCUSED_NODE"
            )
    return node, chrome


def prepare_job(job, nodes, run_id, env, folder, node):
    target = folder / run_id / job["id"]
    prepared = {
        **job,
        "nodes": nodes,
        "report": str(target / "result.json"),
        "argv": pytest_argv(nodes, target / "result.json", expected=target / "expected.json")
        if job["kind"] == "pytest"
        else browser_argv(job, node),
    }
    if job["id"] in {"v8-p01-fixtures", "v8-p01-graders", "v8-p01-mutants"}:
        from importlib import import_module

        sealing = import_module("benchmarks.agent-value.seal")
        grading = import_module("benchmarks.agent-value.grading")
        source = ROOT / ".private/v8-agent-value-planning/phase-1"
        receipt = source / "candidate-seal.json"
        value = json.loads(receipt.read_text())
        sealing.verify(value, source / "hidden")
        prepared["fixture_inputs"] = {
            "private_seal_sha256": hashlib.sha256(receipt.read_bytes()).hexdigest(),
            "public_sha256": value["public_sha256"],
            "private_sha256": value["private_sha256"],
            "sandbox": "bubblewrap0.9.0+libseccomp; wall5s/CPU3s/256MiB/output1MiB",
            "children": "one fixed isolated Python worker per coding variant; "
            "no test/provider child",
            "bwrap_sha256": hashlib.sha256(Path("/usr/bin/bwrap").read_bytes()).hexdigest(),
            "python_sha256": hashlib.sha256(Path("/usr/bin/python3").read_bytes()).hexdigest(),
        }
        prepared["fixture_children"] = [
            {
                "source": "benchmarks/agent-value/grading.py::sandbox",
                "argv": grading.sandbox_argv(
                    Path("<fresh copied implementation only>"),
                    ROOT / "benchmarks/agent-value/worker.py",
                    "<native seccomp filter fd>",
                ),
                "limits": "wall5s/CPU3s/256MiB/output1MiB/64fds/core0; job180s",
                "selection": "private seal-bound case IDs for only the selected task; "
                "no labels/reference in child; no provider or nested test runner",
                "maximum_children": 30 if job["id"] == "v8-p01-mutants" else 150,
            }
        ]
    if job["id"] == "p06-real":
        from benchmarks.retrieval.model import model_identity

        try:
            prepared["model_input"] = model_identity(env.get("ACP_FOCUSED_MODEL_CACHE", ""))
        except (OSError, ValueError) as error:
            raise FocusedError(
                "p06-real requires the pinned explicit offline model cache"
            ) from error
    if job["fixture"] in {"mongo", "browser", "hosting", "browser-hosted"}:
        stack = OwnedStack(
            run_id + "-" + job["id"],
            env,
            target,
            oidc=job["fixture"] != "mongo",
            hosting=job["fixture"] in {"hosting", "browser-hosted"},
        )
        prepared["fixture_graph"] = stack.graph()
    if job["fixture"] == "mongo":
        prepared["fixture_children"] = [
            {
                "source": "tests/v6_isolation_fixtures.py::archive",
                "argv": [
                    "docker",
                    "exec",
                    stack.containers[0],
                    "sh",
                    "-c",
                    "exec mongodump --quiet --username v6-test-root "
                    '--password "$(cat /run/secrets/root)" --authenticationDatabase admin '
                    '--archive --db "$1"',
                    "v6-disposable-backup",
                    "<fresh fixture database.name>",
                ],
                "bounds": "one per isolated_database fixture; same job deadline/process group; "
                "owned container removed on timeout",
            }
        ]
        if any(
            "::test_actual_stdio_mcp_" in selector
            or "::test_actual_stdio_connected_session_" in selector
            for selector in job["selectors"]
        ):
            connected_stdio = any(
                "::test_actual_stdio_connected_session_" in selector
                for selector in job["selectors"]
            )
            prepared["fixture_children"].append(
                {
                    "source": (
                        "tests/integration/test_v6_managed_client_revocation.py::"
                        "test_actual_stdio_connected_session_rechecks_revocation"
                        if connected_stdio
                        else "tests/integration/test_v6_mcp_isolation.py::"
                        "test_actual_stdio_mcp_uses_one_org_and_rechecks_revocation"
                    ),
                    "argv": [
                        PYTHON,
                        "-c",
                        "import asyncio; from app.mcp.server import run_stdio; "
                        "asyncio.run(run_stdio())",
                    ],
                    "bounds": (
                        "three selected sponsor/client/key variants; one issued client each; "
                        "private temporary cwd; loopback API; "
                        "request timeout 15s; same job deadline"
                        if connected_stdio
                        else "two sequential fictional credentials; private temporary cwd; "
                        "loopback API; request timeout 10s; same job deadline"
                    ),
                }
            )
        if job["id"] == "p05-smoke":
            prepared["fixture_children"].extend(
                [
                    {
                        "source": (
                            "tests/integration/test_agent_handoff.py::"
                            "test_real_protocol_handoff_and_boundaries"
                        ),
                        "argv": [
                            PYTHON,
                            "-I",
                            str(ROOT / "examples/agent-handoff/smoke.py"),
                            "--origin",
                            "<fresh loopback HTTPS origin>",
                            "--project-id",
                            "payment-service",
                            "--negative-project-id",
                            "payments-api",
                            "--run-id",
                            "fictional-focused",
                            "--client-a-token-file",
                            "<private tmp_path/a>",
                            "--client-b-token-file",
                            "<private tmp_path/b>",
                            "--ca-file",
                            "<fresh CA>",
                            "--revocation-signal",
                            "<private tmp_path/revoked>",
                            "--allow-disposable-writes",
                            "--confirm-verified-notes",
                        ],
                        "bounds": (
                            "300s demo / 330s fixture child / 600s job; fixed stdio bridge "
                            "child uses Phase 4 transport; no other runner or provider"
                        ),
                    },
                    {
                        "source": (
                            "tests/integration/test_agent_handoff.py::"
                            "test_real_protocol_handoff_and_boundaries"
                        ),
                        "argv": [
                            "/usr/bin/openssl",
                            "req",
                            "-x509",
                            "-newkey",
                            "rsa:2048",
                            "-nodes",
                            "-days",
                            "1",
                            "-subj",
                            "/CN=fictional-handoff",
                            "-addext",
                            "subjectAltName=IP:127.0.0.1",
                            "-addext",
                            "basicConstraints=critical,CA:TRUE",
                            "-keyout",
                            "<private tmp_path/server.key>",
                            "-out",
                            "<private tmp_path/ca.crt>",
                        ],
                        "bounds": "30s; fresh fixture key/CA only; no OS trust edits",
                    },
                ]
            )
    if job["fixture"] == "self":
        prepared["fixture_graph"] = {
            "owner": "selected lifecycle test",
            "graph": OwnedStack(run_id + "-lifecycle", env, target).graph(),
        }
    if job["fixture"] == "evaluation":
        from scripts.evaluation_lifecycle import Evaluation

        evaluation = Evaluation(run_id)
        prepared["fixture_graph"] = {
            "owner": "distributed evaluate.sh command selected by bootstrap test",
            "instance": run_id,
            "state": str(evaluation.state),
            "resources": evaluation.names,
            "commands": [
                [
                    "scripts/evaluate.sh",
                    "start",
                    "--instance",
                    run_id,
                    "--approve-root",
                    "fictional-acceptance-review",
                    "--activation-run",
                    str(folder / run_id),
                    "--api-image",
                    "acp-evaluation-api:p04" if job["id"] == "p04-real" else "acp-v6-api:phase15",
                    "--console-image",
                    "acp-v6-console:phase15",
                ],
                ["scripts/evaluate.sh", "stop", "--instance", run_id],
                ["scripts/evaluate.sh", "start", "--instance", run_id],
                ["scripts/evaluate.sh", "doctor", "--instance", run_id],
                ["scripts/evaluate.sh", "reset", "--instance", run_id],
            ],
            "recipient_operations": "real HTTPS invitation/verification/password/MFA; "
            "independent owner approval then worker one-time issuance; no fixture row writes",
            "children": "bounded exact Docker argv, no pull/build/npm/pytest children; "
            "maintained staged operator APIs, probe <=120s, setup <=240s",
            "cleanup": "parent fallback loads exact manifest and verifies every owned label/path",
        }
    if job["fixture"] == "evaluation" and job["id"] == "p04-real":
        graph = prepared["fixture_graph"]
        graph["commands"] = [graph["commands"][i] for i in (0, 2, 4)]
        graph["diagnostic_children"] = {
            "entrypoint": [
                PYTHON,
                "-I",
                "-c",
                "fixed contextctl.main import",
                str(ROOT),
                "--json",
                "integration",
                "doctor",
                "codex",
                "--connect",
            ],
            "runtime_inputs": (
                "exact owned HTTPS origin/CA from manifest; recipient temporary token file; "
                "explicit evaluation-notes project"
            ),
            "limits": "75s CLI, 60s doctor, 8s HTTP, 10s stdio request; same named job deadline",
            "stdio": [
                PYTHON,
                "-I",
                "-c",
                "fixed run_stdio(McpSettings(_env_file=None))",
                str(ROOT),
            ],
            "no_other_journeys": True,
        }
    if job["kind"] == "browser":
        prepared["harness_nodes"] = [BROWSER_HARNESS]
        prepared["harness_argv"] = pytest_argv(
            [BROWSER_HARNESS], target / "harness.json", expected=target / "harness-expected.json"
        )
        prepared["browser_environment"] = {
            "V6_BROWSER_SERVE_ONLY": "1",
            "V6_DASHBOARD_HOSTING": "1" if job["fixture"] == "browser-hosted" else "0",
            "ACP_FOCUSED_RUN_ID": run_id + "-" + job["id"],
            "tls": "Node verifies fresh fixture CA; Chromium pins only the generated "
            "loopback certificate SPKI in its temporary profile; ignoreHTTPSErrors=false",
        }
    return prepared


def make_plan(phase, values):
    jobs = load_profile(phase)
    env = clean_environment(values)
    folder = evidence_folder(phase)
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    node, chrome = tools_for(jobs, values, env, folder)
    before = phase_identity(phase)
    import uuid

    run_id = uuid.uuid4().hex
    plan = {
        "phase": phase,
        "source_sha256": before,
        "run_id": run_id,
        "node": node,
        "chromium": chrome,
        "jobs": [],
    }
    for job in jobs:
        nodes = collect(job, env, folder, node)
        prepared = prepare_job(job, nodes, run_id, env, folder, node)
        if job["kind"] == "browser":
            harness = {
                "id": "browser-harness",
                "kind": "pytest",
                "selectors": [BROWSER_HARNESS],
                "expected_count": 1,
            }
            if collect(harness, env, folder, node) != prepared["harness_nodes"]:
                raise FocusedError("browser harness selection changed")
        plan["jobs"].append(prepared)
    if before != phase_identity(phase):
        raise FocusedError("source changed during planning")
    write(folder / "plan.json", plan)
    return plan


def run_browser(job, stack, env, plan, target):
    descriptor = stack.state / "browser.json"
    env.update(
        V6_BROWSER_DESCRIPTOR=str(descriptor),
        V6_BROWSER_SERVE_ONLY="1",
        V6_DASHBOARD_HOSTING=job["browser_environment"]["V6_DASHBOARD_HOSTING"],
        ACP_FOCUSED_RUN_ID=stack.run_id,
        ACP_FOCUSED_STATE=str(stack.state),
        ACP_FOCUSED_BROWSER_REPORT=str(target / "browser.json"),
        PLAYWRIGHT_CHROMIUM_EXECUTABLE=plan["chromium"],
        NODE_EXTRA_CA_CERTS=str(stack.state / "oidc/server.crt"),
        ACP_FOCUSED_BROWSER_OUTPUT=str(target / "browser-output"),
    )
    write(target / "harness-expected.json", job["harness_nodes"])
    child = Child(job["harness_argv"], cwd=ROOT, env=env, log=target / "harness.log")
    try:
        deadline = time.monotonic() + 240
        while not descriptor.is_file():
            if child.process.poll() is not None or time.monotonic() >= deadline:
                raise FocusedError("browser fixture did not become ready")
            time.sleep(0.1)
        data = json.loads(descriptor.read_text())
        data["focused_run_id"] = stack.run_id
        write(descriptor, data)
        code = run(
            job["argv"],
            cwd=ROOT / "dashboard",
            env=env,
            log=target / "browser.log",
            timeout=job["timeout"],
        )
        data = json.loads((target / "browser.json").read_text())
        cases = browser_cases(data)
        nodes = [c["file"] + "::" + c["title"] + "::" + c["project"] for c in cases]
        good = (
            code == 0
            and not data.get("errors")
            and nodes == job["nodes"]
            and all(
                c["case"]["expectedStatus"] == "passed"
                and len(c["case"]["results"]) == 1
                and c["case"]["results"][0]["status"] == "passed"
                for c in cases
            )
        )
        return {
            "status": "passed" if good else "failed",
            "selected": len(job["nodes"]),
            "collected": len(nodes),
            "passed": sum(
                c["case"].get("expectedStatus") == "passed"
                and len(c["case"].get("results", [])) == 1
                and c["case"]["results"][0]["status"] == "passed"
                for c in cases
            ),
            "failed": sum(c["case"].get("status") in {"unexpected", "flaky"} for c in cases),
            "skipped": sum(c["case"].get("status") == "skipped" for c in cases),
            "xfailed": sum(c["case"].get("expectedStatus") == "failed" for c in cases),
            "errors": len(data.get("errors", [])),
        }
    finally:
        descriptor.with_suffix(".stop").write_text("stop")
        try:
            code = child.wait(30)
            outcome = reconcile(
                json.loads((target / "harness.json").read_text()), job["harness_nodes"]
            )
            write(target / "harness-summary.json", outcome)
            if code or outcome["status"] != "passed":
                raise FocusedError("browser serving harness failed or teardown incomplete")
        finally:
            child.stop()


def execute(phase, values):
    jobs = load_profile(phase)
    env = clean_environment(values)
    folder = evidence_folder(phase)
    path = folder / "plan.json"
    if not path.is_file():
        raise FocusedError("inspect --plan before --run")
    plan = json.loads(path.read_text())
    if not re.fullmatch(r"[a-f0-9]{32}", plan["run_id"]):
        raise FocusedError("invalid saved plan identity")
    if plan["phase"] != phase or plan["source_sha256"] != phase_identity(phase):
        raise FocusedError("plan source changed; inspect a new --plan")
    node, chrome = tools_for(jobs, values, env, folder)
    if node != plan["node"] or chrome != plan["chromium"]:
        raise FocusedError("tool selection changed after plan")
    # Recollect only the same named files, before starting infrastructure.
    for job, prepared in zip(jobs, plan["jobs"], strict=True):
        nodes = collect(job, env, folder, node)
        if prepare_job(job, nodes, plan["run_id"], env, folder, node) != prepared:
            raise FocusedError("saved execution graph or collection changed after plan")
    run_folder = folder / plan["run_id"]
    run_folder.mkdir(mode=0o700)  # a plan is single-use; never overwrite previous evidence
    outcomes = []
    active_job = None
    try:
        write(run_folder / "plan.json", plan)
        write(run_folder / "summary.json", {"status": "running", "jobs": outcomes})
        for job in plan["jobs"]:
            active_job = job
            target = Path(job["report"]).parent
            target.mkdir(mode=0o700)
            write(target / "expected.json", job["nodes"])
            current = dict(env)
            current.update(
                ACP_FOCUSED_LIFECYCLE_ID=plan["run_id"] + "-lifecycle",
                ACP_FOCUSED_LIFECYCLE_REPORT=str(target),
            )
            if job["fixture"] == "evaluation":
                current.update(
                    ACP_EVALUATION_ACCEPTANCE_INSTANCE=plan["run_id"],
                    ACP_EVALUATION_ACTIVATION_RUN=str(run_folder),
                )
            if job["requires"]:
                if set(job["requires"]) != {"activation-unit", "activation-real"}:
                    raise FocusedError("unknown evidence dependency")
                current.update(
                    V6_UNIT_EVIDENCE_XML=str(run_folder / "activation-unit/result.xml"),
                    V6_REAL_EVIDENCE_XML=str(run_folder / "activation-real/result.xml"),
                )
            started = time.monotonic()
            if job["id"] == "p05-smoke":
                current["ACP_FOCUSED_HANDOFF_REPORT"] = str(target)
            if job["id"] == "p06-real":
                current["ACP_FOCUSED_RETRIEVAL_REPORT"] = str(target)

            def perform(active, job=job, target=target, current=current):
                if job["kind"] == "browser":
                    return run_browser(job, active, active.env, plan, target)
                code = run(
                    job["argv"],
                    cwd=ROOT,
                    env=active.env if active else current,
                    log=target / "pytest.log",
                    timeout=job["timeout"],
                )
                result = reconcile(json.loads(Path(job["report"]).read_text()), job["nodes"])
                if code:
                    result["status"] = "failed"
                return result

            if job["fixture"] in {"mongo", "browser", "hosting", "browser-hosted"}:
                with OwnedStack(
                    plan["run_id"] + "-" + job["id"],
                    current,
                    target,
                    oidc=job["fixture"] != "mongo",
                    hosting=job["fixture"] in {"hosting", "browser-hosted"},
                ) as stack:
                    outcome = perform(stack)
            elif job["fixture"] == "self":
                owner = OwnedStack(plan["run_id"] + "-lifecycle", current, target)
                current["ACP_FOCUSED_LIFECYCLE_OWNER"] = owner.owner
                try:
                    outcome = perform(None)
                finally:
                    owner.created = owner.state.exists() or owner.state.is_symlink()
                    owner.cleanup()
            elif job["fixture"] == "evaluation":
                from scripts.evaluation_lifecycle import Evaluation

                evaluation = Evaluation(plan["run_id"])
                try:
                    outcome = perform(None)
                finally:
                    if evaluation.manifest.exists():
                        evaluation.archive_diagnostics(target / "evaluation-diagnostics")
                        evaluation.reset()
            else:
                outcome = perform(None)
            outcome.update(job=job["id"], duration_seconds=round(time.monotonic() - started, 3))
            outcomes.append(outcome)
            active_job = None
            write(run_folder / "summary.json", {"status": "running", "jobs": outcomes})
            if job["id"] == "activation-cutover" and outcome["status"] == "passed":
                # A downstream commissioning job may consume this completed
                # dependency while the outer profile is still running. This is
                # a receipt for actual reconciled reports, never release XML.
                dependency = [
                    r
                    for r in outcomes
                    if r["job"] in {"activation-unit", "activation-real", "activation-cutover"}
                ]
                if len(dependency) != 3 or any(r["status"] != "passed" for r in dependency):
                    raise FocusedError("activation dependency did not reconcile")
                write(
                    run_folder / "activation-summary.json",
                    {
                        "status": "passed",
                        "source_sha256": plan["source_sha256"],
                        "jobs": dependency,
                    },
                )
            print(json.dumps(outcome), flush=True)
            if outcome["status"] != "passed":
                raise FocusedError("selected check did not pass")
        if phase_identity(phase) != plan["source_sha256"]:
            raise FocusedError("source changed during execution; evidence invalid")
        write(run_folder / "summary.json", {"status": "passed", "jobs": outcomes})
    except BaseException as error:
        if active_job is not None:
            # A timeout/missing report/fixture failure is incomplete, never zero
            # tests passed. Preserve observed counts where a child wrote them.
            report = Path(active_job["report"])
            try:
                data = json.loads(report.read_text()) if report.is_file() else {}
            except (OSError, ValueError):
                data = {}
            outcome = {
                "job": active_job["id"],
                "status": "incomplete",
                "selected": len(active_job["nodes"]),
                "collected": len(data["nodes"]) if "nodes" in data else None,
                "passed": None,
                "failed": None,
                "skipped": None,
                "xfailed": None,
                "error_type": type(error).__name__,
            }
            if data.get("nodes") == active_job["nodes"]:
                outcome.update(reconcile(data, active_job["nodes"]), status="incomplete")
            outcomes.append(outcome)
        write(
            run_folder / "summary.json",
            {"status": "incomplete", "error_type": type(error).__name__, "jobs": outcomes},
        )
        raise
    return run_folder


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--phase", required=True)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--plan", action="store_true")
    modes.add_argument("--run", action="store_true")
    args = parser.parse_args()
    os.umask(0o077)

    def interrupted(*_):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, interrupted)
    try:
        if args.plan:
            print(json.dumps(make_plan(args.phase, os.environ), indent=2))
        else:
            print("Evidence:", execute(args.phase, os.environ))
    except (FocusedError, OSError, ValueError, KeyError, KeyboardInterrupt) as error:
        # No arbitrary child stdout/environment in console diagnostics.
        print(
            str(error)
            if isinstance(error, FocusedError)
            else "Focused check incomplete: " + type(error).__name__,
            file=sys.stderr,
        )
        return 1
    return 0
