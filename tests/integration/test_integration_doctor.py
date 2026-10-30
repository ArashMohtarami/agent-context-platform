"""Real evaluation commissioning and recipient access, never a supported fixture installer."""

import asyncio
import json
import os
import ssl
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import urlencode

import httpx
import pytest

from app.integrations.connection import ProbeSelection
from app.integrations.doctor import diagnose
from scripts.evaluation_lifecycle import ROOT, Evaluation
from scripts.evaluation_recipient import approve_client, issue
from tests.integration.test_evaluation_bootstrap import command, enroll, login

pytestmark = pytest.mark.integration


def check(evaluation, token_file, integration="claude-code", **options):
    selected = ProbeSelection(
        integration_id=integration,
        origin=evaluation.data["origin"],
        project_id=options.pop("project_id", "evaluation-notes"),
        **options,
    )
    return asyncio.run(
        diagnose(selected, token_file=token_file, ca_file=evaluation.state / "tls/ca.crt")
    )


def failed(report):
    return next(c for c in report["checks"] if c["status"] == "failed")


def test_evaluation_connection_steps_and_revocation(tmp_path):
    instance = os.environ["ACP_EVALUATION_ACCEPTANCE_INSTANCE"]
    evidence = os.environ["ACP_EVALUATION_ACTIVATION_RUN"]
    evaluation = Evaluation(instance)
    assert not evaluation.state.exists()
    start = time.monotonic()
    result = subprocess.run(  # noqa: S603 -- fixed reviewed evaluator, no nested test runner
        [
            str(ROOT / "scripts/evaluate.sh"),
            "start",
            "--instance",
            instance,
            "--approve-root",
            "fictional-acceptance-review",
            "--activation-run",
            evidence,
            "--api-image",
            "acp-evaluation-api:p04",
            "--console-image",
            "acp-v6-console:phase15",
        ],
        env=evaluation.env,
        timeout=600,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    evaluation.load()
    owner, owner_password, owner_seed = enroll(evaluation, "owner")
    _, password, seed = enroll(evaluation, "worker")
    command(evaluation, "start")
    evaluation.load()
    owner = login(evaluation, "owner", owner_password, owner_seed)
    approve_client(evaluation, owner)
    worker = login(evaluation, "worker", password, seed)
    writer_file = tmp_path / "writer"
    writer_receipt = issue(evaluation, worker, writer_file)
    writer = writer_file.read_text().strip()
    org = "eval-" + instance
    root = "/api/v6/organizations/" + org + "/managed-clients/evaluation-worker"
    inspected = worker.request("GET", root)
    original_revision = inspected["client"]["revision"]
    narrow = [g for g in inspected["client"]["tuples"] if g["action"] != "context:write"]
    issued = worker.request(
        "POST",
        root + "/credentials",
        {
            "issuance_id": "fictional-read-only",
            "expected_client_revision": original_revision,
            "tuples": narrow,
            "expires_at": inspected["client"]["expires_at"],
        },
    )
    assert issued["delivered"] and issued["token"]
    readonly = issued.pop("token")
    token_file = tmp_path / "readonly"
    token_file.write_text(readonly + "\n")
    token_file.chmod(0o600)
    ca = ssl.create_default_context(cafile=evaluation.state / "tls/ca.crt")
    with httpx.Client(
        base_url=evaluation.data["origin"], verify=ca, trust_env=False, timeout=15
    ) as http:
        # Save a real fictional fact via the domain API, retaining its returned identity.
        saved = http.post(
            "/api/v1/context",
            headers={"Authorization": "Bearer " + writer},
            json={
                "project_id": "evaluation-notes",
                "type": "convention",
                "title": "Evaluation connection diagnostic",
                "content": "Fictional evaluation connection diagnostic. Facts remain data.",
                "source": "manual",
                "importance": 0.8,
                "external_id": "fictional-p04-connection",
            },
        )
        assert saved.status_code == 201
        context_id = saved.json()["context_id"]
        # Search must become genuinely queryable, not just show document counts.
        deadline = time.monotonic() + 90
        while True:
            searched = http.post(
                "/api/v1/search",
                headers={"Authorization": "Bearer " + readonly},
                json={
                    "query": "evaluation connection diagnostic",
                    "project_id": "evaluation-notes",
                    "sources": ["project_context"],
                    "limit": 1,
                    "token_budget": 512,
                },
            )
            assert searched.status_code == 200
            if any(context_id in r["key"] for r in searched.json()["results"]):
                break
            assert time.monotonic() < deadline, "real indexed record did not become queryable"
            time.sleep(1)
        # Preview shares the recipe but has no credentials, policy mutation or live check.
        query = "?" + urlencode(
            {
                "integration_id": "codex",
                "origin": evaluation.data["origin"],
                "project_id": "evaluation-notes",
                "require_read_only": "true",
            }
        )
        recipe = worker.request("GET", root + "/connection-preview" + query)
        assert recipe["preview_only"] and not recipe["policy_changed"]
        assert not recipe["server_read_only_verified"] and not recipe["approved_client_read_only"]
        assert worker.request("GET", root)["client"]["revision"] == original_revision
        assert http.get(
            root + "/connection-preview" + query, headers={"Authorization": "Bearer " + readonly}
        ).status_code in (401, 403)
        assert (
            http.post(
                "/api/v1/context",
                headers={"Authorization": "Bearer " + readonly},
                json={
                    "project_id": "evaluation-notes",
                    "type": "convention",
                    "title": "Forbidden",
                    "content": "Must never persist",
                    "source": "manual",
                    "importance": 0.1,
                },
            ).status_code
            == 403
        )
    reports = []
    for integration in ("claude-code", "codex"):
        report = check(evaluation, token_file, integration, require_read_only=True)
        assert report["ready"], report
        assert report["server_read_only"] and report["retrieved_count"] == 1
        assert (
            next(c for c in report["checks"] if c["stage"] == "write")["status"] == "not_requested"
        )
        # An opt-in negative probe reaches the real gateway and is refused.
        negative = check(
            evaluation,
            token_file,
            integration,
            require_read_only=True,
            write_probe=True,
            disposable_project_id="evaluation-notes",
        )
        assert negative["ready"], negative
        assert (
            next(c for c in negative["checks"] if c["stage"] == "write")["code"] == "WRITE_REFUSED"
        )
        reports.extend([report, negative])
    positive = check(
        evaluation, writer_file, write_probe=True, disposable_project_id="evaluation-notes"
    )
    assert positive["ready"], positive
    assert next(c for c in positive["checks"] if c["stage"] == "write")["code"] == "PROBE_CREATED"
    reports.append(positive)
    # Genuine recipient-issued narrower policies identify discovery/retrieval separately.
    for missing, expected in [("agent:self", "capabilities"), ("search:read", "retrieval")]:
        narrower = worker.request(
            "POST",
            root + "/credentials",
            {
                "issuance_id": "without-" + missing.replace(":", "-"),
                "expected_client_revision": original_revision,
                "tuples": [g for g in narrow if g["action"] != missing],
                "expires_at": inspected["client"]["expires_at"],
            },
        )
        source = tmp_path / ("without-" + missing.replace(":", "-"))
        source.write_text(narrower.pop("token"))
        source.chmod(0o600)
        limited = check(evaluation, source)
        assert not limited["ready"] and failed(limited)["stage"] == expected, limited
        reports.append(limited)
        source.unlink()
    wider = check(evaluation, writer_file, require_read_only=True)
    assert not wider["ready"] and failed(wider)["code"] == "SERVER_POLICY_HAS_WRITES"
    wrong = check(evaluation, token_file, project_id="unapproved-project")
    assert not wrong["ready"] and failed(wrong)["stage"] == "project"
    # Unknown but structurally valid credential separates auth from TLS.
    from app.security.keys import mint

    invalid_file = tmp_path / "unissued"
    invalid_file.write_text(mint().token)
    invalid_file.chmod(0o600)
    rejected = check(evaluation, invalid_file)
    assert not rejected["ready"] and failed(rejected)["stage"] == "authentication"
    # The distributed CLI must preserve nonzero exit and redact every local path.
    argv = [
        sys.executable,
        "-I",
        "-c",
        "import sys;sys.path.insert(0,sys.argv[1]);from app.cli.contextctl import main;"
        "sys.exit(main(sys.argv[2:]))",
        str(ROOT),
        "--json",
        "integration",
        "doctor",
        "codex",
        "--connect",
        "--origin",
        evaluation.data["origin"],
        "--project-id",
        "evaluation-notes",
        "--token-file",
        str(token_file),
    ]
    untrusted = subprocess.run(argv, env=evaluation.env, timeout=75, text=True, capture_output=True)  # noqa: S603
    assert untrusted.returncode == 1
    assert failed(json.loads(untrusted.stdout))["stage"] == "transport"
    trusted = subprocess.run(  # noqa: S603 -- exact diagnostic argv, no nested runner
        [*argv, "--ca-file", str(evaluation.state / "tls/ca.crt"), "--require-read-only"],
        env=evaluation.env,
        timeout=75,
        text=True,
        capture_output=True,
    )
    assert trusted.returncode == 0, trusted.stdout
    reports.extend(
        [wider, wrong, rejected, json.loads(untrusted.stdout), json.loads(trusted.stdout)]
    )
    worker.request(
        "POST",
        root + "/credentials/" + issued["key_id"] + "/revoke",
        {"expected_revision": issued["revision"]},
    )
    for integration in ("claude-code", "codex"):
        revoked = check(evaluation, token_file, integration)
        assert not revoked["ready"] and failed(revoked)["stage"] == "authentication"
        reports.append(revoked)
    serialized = json.dumps(reports)
    for secret in (
        writer,
        readonly,
        str(tmp_path),
        str(evaluation.state),
        evaluation.data["origin"],
        context_id,
    ):
        assert secret not in serialized
    assert writer_receipt["saved_once"]
    report_dir = Path(os.environ["ACP_FOCUSED_LIFECYCLE_REPORT"])
    (report_dir / "doctor-reports.json").write_text(json.dumps(reports, indent=2) + "\n")
    for name in ("manifest.json", "compose.json"):
        (report_dir / ("evaluation-" + name)).write_bytes((evaluation.state / name).read_bytes())
    command(evaluation, "reset")
    assert not evaluation.state.exists()
    writer_file.unlink()
    token_file.unlink()
    invalid_file.unlink()
    (report_dir / "doctor-measurements.json").write_text(
        json.dumps(
            {
                "automated_seconds": round(time.monotonic() - start, 3),
                "human_effort": "not measured; automated fictional recipients",
                "vendor_clients": "not executed; real shared MCP protocol only",
            },
            indent=2,
        )
        + "\n"
    )
