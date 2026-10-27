"""Actual distributed empty-store commissioning, never privileged fixture rows."""

import json
import os
import secrets
import ssl
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path

import pyotp
import pytest

from scripts.evaluation_lifecycle import ROOT, Evaluation
from scripts.evaluation_recipient import Browser, approve_client, handoff, issue
from scripts.focused_process import FocusedError

pytestmark = pytest.mark.integration


def command(evaluation, *args, timeout=600):
    result = subprocess.run(  # noqa: S603 -- exact maintained command, no nested test runner
        [str(ROOT / "scripts/evaluate.sh"), *args, "--instance", evaluation.instance],
        env=evaluation.env,
        timeout=timeout,
        text=True,
        capture_output=True,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout


def enroll(evaluation, recipient):
    browser = Browser(evaluation.data["origin"], evaluation.state / "tls/ca.crt")
    email = recipient + "@example.test"
    password = secrets.token_urlsafe(30)
    browser.request(
        "POST",
        "/api/v6/auth/invitations/accept",
        {"token": handoff(evaluation.state, email, "invite"), "password": password},
    )
    browser.request(
        "POST", "/api/v6/auth/local/verify", {"token": handoff(evaluation.state, email, "verify")}
    )
    browser.login("eval-" + evaluation.instance, email, password)
    factor = browser.request("POST", "/api/v6/auth/mfa/enroll", {})
    browser.request("POST", "/api/v6/auth/mfa/confirm", {"code": pyotp.TOTP(factor["seed"]).now()})
    return browser, password, factor["seed"]


def login(evaluation, recipient, password, seed):
    # Real wall clock: never modify MFA replay/expiry state to fit a test.
    current = time.time() % 30
    time.sleep(30 - current + 0.2)
    browser = Browser(evaluation.data["origin"], evaluation.state / "tls/ca.crt")
    browser.login(
        "eval-" + evaluation.instance, recipient + "@example.test", password, pyotp.TOTP(seed).now()
    )
    return browser


def test_distributed_empty_store_resume_enroll_issue_stop_reset(tmp_path):
    instance = os.environ["ACP_EVALUATION_ACCEPTANCE_INSTANCE"]
    evidence = os.environ["ACP_EVALUATION_ACTIVATION_RUN"]
    evaluation = Evaluation(instance)
    # The runner supplies an exact reserved ID and genuine earlier reports. It
    # also owns fallback cleanup on child failure/signal/timeout.
    assert not evaluation.state.exists()
    started = time.monotonic()
    result = subprocess.run(  # noqa: S603 -- maintained distributed command, explicit argv
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
            "acp-v6-api:phase15",
            "--console-image",
            "acp-v6-console:phase15",
        ],
        env={
            **evaluation.env,
            "COMPOSE_FILE": "/nonexistent/foreign-compose.yml",
            "MONGODB_URI": "mongodb://foreign.invalid",
            "DOCKER_HOST": "tcp://foreign.invalid:2375",
        },
        capture_output=True,
        text=True,
        timeout=600,
    )
    assert result.returncode == 0, result.stderr
    evaluation.load()
    warm_start_seconds = time.monotonic() - started
    assert evaluation.data["stage"] == "awaiting-enrollment"
    manifest_before = evaluation.manifest.read_bytes()
    # Untrusted CA fails; explicitly scoped CA succeeds, with no global trust edits.
    with pytest.raises(urllib.error.URLError) as untrusted:
        urllib.request.urlopen(evaluation.data["origin"] + "/api/v6/auth/csrf", timeout=5)  # noqa: S310 -- owned loopback HTTPS
    assert isinstance(untrusted.value.reason, ssl.SSLCertVerificationError)
    assert ssl.create_default_context(cafile=evaluation.state / "tls/ca.crt")
    assert evaluation.manifest.read_bytes() == manifest_before
    command(evaluation, "status")
    # Interrupt an ACTUAL distributed resume, not a pretend checkpoint fixture.
    # The instance lock/manifest exposes the genuine bounded search stage.
    interrupted = subprocess.Popen(  # noqa: S603 -- exact owned distributed command
        [str(ROOT / "scripts/evaluate.sh"), "start", "--instance", instance],
        env=evaluation.env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        deadline = time.monotonic() + 180
        while True:
            stage = json.loads(evaluation.manifest.read_text())["stage"]
            if stage == "checking-search":
                interrupted.terminate()
                break
            assert interrupted.poll() is None and time.monotonic() < deadline
            time.sleep(0.1)
        assert interrupted.wait(timeout=240) != 0
    finally:
        if interrupted.poll() is None:
            interrupted.kill()
            interrupted.wait(timeout=10)
    evaluation.load()
    assert evaluation.data["stage"] == "interrupted"
    assert evaluation.state.exists()
    command(evaluation, "start")
    command(evaluation, "start")  # repeats neither invitations nor authority
    owner, owner_password, owner_seed = enroll(evaluation, "owner")
    _, worker_password, worker_seed = enroll(evaluation, "worker")
    command(evaluation, "start")
    evaluation.load()
    assert evaluation.data["stage"] == "commissioned"
    assert evaluation.maintenance("snapshot")["schema"] == 42
    owner = login(evaluation, "owner", owner_password, owner_seed)
    assert approve_client(evaluation, owner)["independent_approval"]
    root = "/api/v6/organizations/eval-" + instance + "/managed-clients/evaluation-worker"
    with pytest.raises(FocusedError, match="HTTP 403"):
        owner.request(
            "POST",
            root + "/credentials",
            {
                "issuance_id": "owner-cannot-claim",
                "expected_client_revision": 1,
                "tuples": [],
                "expires_at": evaluation.data["expires_at"],
            },
        )
    worker = login(evaluation, "worker", worker_password, worker_seed)
    destination = tmp_path / "recipient-token"
    receipt = issue(evaluation, worker, destination)
    assert receipt["saved_once"] and destination.stat().st_mode & 0o077 == 0
    token = destination.read_text().strip()
    assert token.startswith("acp_")
    assert token not in evaluation.manifest.read_text()
    request = urllib.request.Request(  # noqa: S310 -- owned loopback origin
        evaluation.data["origin"] + "/api/v1/projects", headers={"Authorization": "Bearer " + token}
    )
    with urllib.request.urlopen(  # noqa: S310 -- owned loopback HTTPS and explicit CA
        request,
        context=ssl.create_default_context(cafile=evaluation.state / "tls/ca.crt"),
        timeout=10,
    ) as response:
        assert response.status == 200
        assert "evaluation-notes" in response.read().decode()
    command(evaluation, "doctor")
    root_generation = owner.request(
        "GET",
        "/api/v6/organizations/eval-"
        + instance
        + "/assignments?subject_id="
        + evaluation.data["members"]["owner"]["user_id"],
    )["assignments"][0]["generation"]
    command(evaluation, "stop")
    assert destination.exists() and evaluation.state.exists()
    command(evaluation, "start")
    evaluation.load()
    owner = login(evaluation, "owner", owner_password, owner_seed)
    assert (
        owner.request(
            "GET",
            "/api/v6/organizations/eval-"
            + instance
            + "/assignments?subject_id="
            + evaluation.data["members"]["owner"]["user_id"],
        )["assignments"][0]["generation"]
        == root_generation
    )
    before_reset = evaluation.names
    report_directory = Path(os.environ["ACP_FOCUSED_LIFECYCLE_REPORT"])
    # Reset deliberately deletes runtime state. Retain only the redacted
    # identity/configuration evidence first, never secret or handoff contents.
    for name in ("manifest.json", "compose.json"):
        proof = report_directory / ("evaluation-" + name)
        proof.write_bytes((evaluation.state / name).read_bytes())
        proof.chmod(0o600)
    command(evaluation, "reset")
    assert not evaluation.state.exists()
    # Read-only Docker absence checks use a temporary report directory outside
    # deleted state. Recipient token is explicitly outside evaluator ownership.
    evaluation.state = tmp_path
    for kind, names in before_reset.items():
        assert all(evaluation.inspect(kind, name) is None for name in names)
    assert destination.exists()
    destination.unlink()
    measurements = {
        "warm_initial_start_seconds": round(warm_start_seconds, 3),
        "warm_automated_seconds": round(time.monotonic() - started, 3),
        "human_effort": "not measured; automated fictional recipients",
        "cold_download_build": "not measured; cached images, no downloads",
        "instance": instance,
    }
    report = Path(os.environ["ACP_FOCUSED_LIFECYCLE_REPORT"]) / "evaluation-measurements.json"
    report.write_text(json.dumps(measurements, indent=2) + "\n")
    report.chmod(0o600)
