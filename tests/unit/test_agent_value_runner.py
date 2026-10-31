"""Portable control contracts; installed-CLI/OS proofs live in integration tests."""

import ast
import copy
import importlib
import json
import subprocess

import pytest

from scripts.focused_process import FocusedError

driver = importlib.import_module("benchmarks.agent-value-runner.driver")


def test_native_plus_guard_refuses_unknown_credit_routes_and_configuration(tmp_path):
    native = importlib.import_module("benchmarks.agent-value-runner.native")
    account = {"account": {"type": "chatgpt", "planType": "plus", "email": "fictional-identity"}}
    limits = {
        "rateLimitsByLimitId": {
            "codex": {
                "credits": {"hasCredits": False, "unlimited": False, "balance": "0"},
                "primary": {"usedPercent": 10},
                "secondary": {"usedPercent": 20},
                "rateLimitReachedType": None,
            }
        }
    }
    result = native.account_gate(account, limits)
    assert result["additional_cash_authorized"] == "0.00"
    assert result["quota_upper_bound"] is None and not result["purchased_credits_authorized"]
    assert "fictional-identity" not in json.dumps(result)
    for change in (
        {"hasCredits": True},
        {"balance": "1"},
        {"balance": None},
        {"balance": "NaN"},
        {"unlimited": True},
    ):
        bad = copy.deepcopy(limits)
        bad["rateLimitsByLimitId"]["codex"]["credits"].update(change)
        with pytest.raises(FocusedError):
            native.account_gate(account, bad)
    for bad in ({}, {"rateLimitsByLimitId": {}}, {"rateLimitsByLimitId": {"codex": {}}}):
        with pytest.raises(FocusedError):
            native.account_gate(account, bad)
    with pytest.raises(FocusedError):
        native.account_gate({"account": {"type": "apiKey"}}, limits)
    bad = copy.deepcopy(limits)
    bad["rateLimitsByLimitId"]["codex"]["primary"]["usedPercent"] = 80
    with pytest.raises(FocusedError):
        native.account_gate(account, bad)
    config = native.configuration(tmp_path / "codex", tmp_path / "work", tmp_path / "home")
    assert config["features.multi_agent_v2"]["disable_native_tools"] is True
    assert config["permissions.pilot.network.enabled"] is False
    assert config["permissions.pilot.filesystem"][str(tmp_path / "work/test_visible.py")] == "read"
    assert config["model_providers.benchmark-plus"]["request_max_retries"] == 0
    assert config["shell_environment_policy.inherit"] == "none"
    assert "OPENAI_API_KEY" not in str(config)


def test_native_preflight_refuses_foreign_cleanup_and_missing_login(tmp_path, monkeypatch):
    native = importlib.import_module("benchmarks.agent-value-runner.native")
    preflight = importlib.import_module("benchmarks.agent-value-runner.preflight")
    runtime = importlib.import_module("benchmarks.agent-value-runner.runtime")
    import os

    credentials = tmp_path / "purpose-cache"
    credentials.mkdir(mode=0o700)
    monkeypatch.setattr(native, "CREDENTIAL_HOME", credentials)
    binary = tmp_path / "unit-binary"
    binary.write_bytes(b"fictional non-executable unit input")
    monkeypatch.setattr(native, "BINARY", binary)
    adapter = driver.read(driver.SOURCE / "adapter.json")
    monkeypatch.setattr(
        driver,
        "read",
        lambda path: {**adapter, "binary_sha256": driver.dataset.sha(binary.read_bytes())},
    )
    monkeypatch.setattr(
        subprocess, "Popen", lambda *a, **k: pytest.fail("missing login launched a child")
    )
    with pytest.raises(FileNotFoundError):
        preflight.prepare(with_benchmark_login=True)
    home = tmp_path / "fresh"
    home.mkdir(mode=0o700)
    (credentials / "auth.json").write_bytes(b"fictional-test-cache")
    (credentials / "auth.json").chmod(0o600)
    native.stage_credentials(home)
    assert (home / "auth.json").read_bytes() == b"fictional-test-cache"
    with pytest.raises(FocusedError, match="FRESH_NATIVE_HOME"):
        native.stage_credentials(home)
    identity = "a" * 32
    folder = tmp_path / ("acp-v8-native-" + identity)
    folder.mkdir(mode=0o700)
    owner = {"uid": os.getuid(), "identity": identity, "path": str(folder)}
    (folder / "owner.json").write_text(json.dumps(owner))
    (folder / "owner.json").chmod(0o600)
    foreign = folder / "foreign"
    foreign.mkdir()
    with pytest.raises(FocusedError, match="FOREIGN_NATIVE_CLEANUP_ENTRY"):
        preflight.cleanup(folder, owner)
    assert foreign.is_dir()
    foreign.rmdir()
    with pytest.raises(FocusedError, match="FOREIGN_NATIVE_CLEANUP_REFUSED"):
        preflight.cleanup(folder, {**owner, "uid": -1})
    preflight.cleanup(folder, owner)
    assert not folder.exists()
    argv = runtime.argv(
        binary, tmp_path / "inputs", tmp_path / "control", tmp_path / "skills", identity=identity
    )
    assert "--unshare-all" in argv and "--share-net" not in argv
    assert "--property=MemoryMax=2147483648" in argv
    assert "--property=TasksMax=128" in argv
    assert not any("docker.sock" in v or "OPENAI_API_KEY" in v for v in argv)
    with pytest.raises(FocusedError):
        runtime.argv(binary, home, home, home, identity="../foreign")


def test_native_gateway_rejects_undeclared_routes_and_redacts_numeric_receipts(
    tmp_path, monkeypatch
):
    """Synthetic numbers through real owned local TLS; no external provider."""
    import http.client
    import ssl
    from urllib.parse import urlsplit

    gateway = importlib.import_module("benchmarks.agent-value-runner.gateway")
    tools = [{"type": "namespace", "name": "fictional-unit-tools", "tools": []}]
    body = {
        "client_metadata": {},
        "include": [],
        "input": [{"type": "additional_tools", "tools": tools}],
        "model": "gpt-6.1-sol",
        "parallel_tool_calls": False,
        "prompt_cache_key": "synthetic",
        "reasoning": {"effort": "high", "context": "all_turns"},
        "store": False,
        "stream": True,
        "text": {},
        "tool_choice": "auto",
    }
    encoded = json.dumps(body).encode()
    path = "/backend-api/codex/responses"
    with pytest.raises(FocusedError, match="MODEL_FREE_PREFLIGHT_ONLY"):
        gateway.Policy().request("chatgpt.com", "POST", path, encoded)
    persisted = []
    policy = gateway.Policy(
        model_requests=True, tools_sha256=gateway.digest(tools), persist=persisted.append
    )
    for host, method, url in (
        ("api.openai.com", "POST", path),
        ("chatgpt.com", "POST", "/purchase"),
        ("auth.openai.com", "GET", "/arbitrary"),
        ("chatgpt.com", "POST", "https://other.invalid"),
    ):
        with pytest.raises(FocusedError):
            policy.request(host, method, url, encoded)
    with pytest.raises(FocusedError, match="CATALOG_CHANGED"):
        policy.request("chatgpt.com", "POST", path, json.dumps({**body, "input": []}).encode())
    secret = "fictional-secret-never-retained"
    response = {
        "id": "resp_synthetic_unit",
        "model": "gpt-6.1-sol",
        "status": "completed",
        "service_tier": "default",
        "usage": {
            "input_tokens": 100,
            "input_tokens_details": {"cached_tokens": 40},
            "output_tokens": 20,
            "output_tokens_details": {"reasoning_tokens": 5},
            "total_tokens": 120,
        },
        "output": [{"reasoning": secret}],
    }
    wire = (
        "data: " + json.dumps({"type": "response.completed", "response": response}) + "\n\n"
    ).encode()

    class Reply:
        status = 200

        def __init__(self):
            self.pending = wire

        def getheaders(self):
            return [("Content-Type", "text/event-stream")]

        def read1(self, size):
            data, self.pending = self.pending[:size], self.pending[size:]
            return data

    class Upstream:
        sock = None

        def __init__(self, host, **kwargs):
            assert host == "chatgpt.com"

        def connect(self):
            pass

        def request(self, method, url, data, headers):
            assert headers["Authorization"] == "Bearer " + secret
            assert json.loads(data) == body  # no guessed API token-limit fields

        def getresponse(self):
            return Reply()

        def close(self):
            pass

    monkeypatch.setattr(gateway.http.client, "HTTPSConnection", Upstream)
    tmp_path.chmod(0o700)
    route = gateway.Route(tmp_path, policy)
    route.start()
    try:
        port = urlsplit(route.url).port
        client = http.client.HTTPConnection("127.0.0.1", port, timeout=3)
        client.set_tunnel("chatgpt.com", 443)
        client.connect()
        context = ssl.create_default_context(cafile=str(tmp_path / "ca.pem"))
        client.sock = context.wrap_socket(client.sock, server_hostname="chatgpt.com")
        client.request("POST", path, encoded, {"Authorization": "Bearer " + secret})
        result = client.getresponse()
        assert result.status == 200 and result.read() == wire
        client.close()
    finally:
        route.close()
    receipt = policy.receipts.report()
    assert receipt["normalized_total"] == 120
    assert receipt["native_session_totals_added"] is False
    assert secret not in json.dumps([receipt, persisted])
    assert persisted[0]["coverage"] == "incomplete"
    assert persisted[-1]["coverage"] == "complete-observed-route"
    assert receipt["billed_spend"] is None and not receipt["cash_savings_claim_supported"]
    partial = gateway.Events()
    partial.consume(b'data: {"type":"response.completed"')
    with pytest.raises(FocusedError, match="TRUNCATED_PROVIDER_EVENT"):
        partial.end()
    number = policy.request("chatgpt.com", "POST", path, encoded)[1]
    with pytest.raises(FocusedError, match="UNRECONCILED"):
        policy.request("chatgpt.com", "POST", path, encoded)
    policy.finish(number, 502, None)
    with pytest.raises(FocusedError, match="UNRECONCILED"):
        policy.request("chatgpt.com", "POST", path, encoded)


def synthetic_configuration(cli):
    """A rejected synthetic unit input, never an installed vendor identity."""
    return {
        "version": 1,
        "mode": "stub-validation",
        "study": "stub-validation",
        "task_ids": ["folio-development-lookup"],
        "repeats": 1,
        "model": "synthetic-model",
        "effort": "synthetic",
        "service_tier": "synthetic",
        "currency": "TEST",
        "total_cap": 12,
        "attempt_reservation": 3,
        "price_sha256": "a" * 64,
        "tool_schema_sha256": "b" * 64,
        "cli": str(cli),
        "stub_scenario": "ok",
        "bounds": {
            "wall_seconds": 2,
            "cpu_seconds": 2,
            "memory_bytes": 134217728,
            "workspace_bytes": 1048576,
            "output_bytes": 65536,
            "turns": 1,
            "tools": 2,
            "events": 16,
        },
    }


def test_public_planner_refuses_unreviewed_binary_and_widened_inputs(tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("invalid planning input launched a child")

    monkeypatch.setattr(subprocess, "Popen", forbidden)
    binary = tmp_path / "not-a-vendor-cli"
    binary.write_bytes(b"fictional unit input; not executable")
    config = synthetic_configuration(binary)
    with pytest.raises(FocusedError, match="UNREVIEWED_CLI_BINARY"):
        driver.make_plan(config)
    for changed in (
        {**config, "task_ids": []},
        {**config, "task_ids": ["folio-heldout-close"]},
        {**config, "extra_args": ["--search"]},
        {**config, "model": "$(unsafe)"},
        {**config, "total_cap": True},
        {**config, "OPENAI_API_KEY": "fictional-not-accepted"},
    ):
        with pytest.raises(FocusedError):
            driver.make_plan(changed)


def test_authorization_binds_all_attempts_caps_purpose_and_expiry():
    plan = {
        "sha256": "a" * 64,
        "configuration": {
            "mode": "stub-validation",
            "currency": "TEST",
            "total_cap": 12,
            "attempt_reservation": 3,
        },
        "attempts": [{"id": str(i) * 32} for i in (1, 2, 3)],
    }
    receipt = {
        "version": 1,
        "receipt_id": "synthetic-unit-approval",
        "approved_by": "synthetic-unit-budget-owner",
        "purpose": "stub-validation",
        "plan_sha256": plan["sha256"],
        "currency": "TEST",
        "total_cap": 12,
        "attempt_reservation": 3,
        "attempt_ids": [a["id"] for a in plan["attempts"]],
        "expires_utc": "2100-01-01T00:00:00+00:00",
    }
    driver.authorization(receipt, plan)
    for replacement in (
        {"attempt_ids": receipt["attempt_ids"][:-1]},
        {"attempt_ids": receipt["attempt_ids"] * 2},
        {"total_cap": 13},
        {"purpose": "codex-plan"},
        {"plan_sha256": "b" * 64},
        {"expires_utc": "2000-01-01T00:00:00+00:00"},
        {"version": True},
    ):
        changed = copy.deepcopy(receipt)
        changed.update(replacement)
        with pytest.raises(FocusedError):
            driver.authorization(changed, plan)


def test_calibration_lock_uses_only_development_and_invalidates_changed_inputs(
    tmp_path, monkeypatch
):
    calibration = importlib.import_module("benchmarks.agent-value-runner.calibration")
    seed, rows = calibration.schedule("calibration", list(calibration.TASKS), 1)
    assert seed == 20261031
    assert [(t, a, pos) for t, _, a, pos in rows] == [
        ("harbor-development-import", "markdown", 7),
        ("harbor-development-import", "repository", 8),
        ("harbor-development-import", "mcp", 9),
        ("parcel-development-pack", "mcp", 31),
        ("parcel-development-pack", "repository", 32),
        ("parcel-development-pack", "markdown", 33),
        ("parcel-development-tracking", "markdown", 43),
        ("parcel-development-tracking", "repository", 44),
        ("parcel-development-tracking", "mcp", 45),
        ("harbor-development-precision", "markdown", 46),
        ("harbor-development-precision", "mcp", 47),
        ("harbor-development-precision", "repository", 48),
    ]
    binary = tmp_path / "synthetic-unit-binary"
    binary.write_bytes(b"not executable or vendor evidence")
    source = tmp_path / "unit-adapter"
    source.mkdir()
    (source / "adapter.json").write_text(
        json.dumps({"binary_sha256": driver.dataset.sha(binary.read_bytes())})
    )
    monkeypatch.setattr(driver, "SOURCE", source)
    config = synthetic_configuration(binary)
    config.update(mode="codex-plan", study="calibration", task_ids=list(calibration.TASKS))
    config["stub_scenario"] = None
    assert driver.configuration(config) == config
    for tasks, repeats in ((list(calibration.TASKS)[:-1], 1), (list(calibration.TASKS), 3)):
        with pytest.raises(FocusedError, match="CALIBRATION"):
            driver.configuration({**config, "task_ids": tasks, "repeats": repeats})
    plan = {
        "configuration": config,
        "attempts": [{"id": calibration.digest([t, r, a])[:32]} for t, r, a, _ in rows],
        "source_hashes": {"unit-only": "a" * 64},
    }
    plan["sha256"] = calibration.digest(plan)
    empty = calibration.reconcile(plan, [], copy.deepcopy(plan))
    assert empty["scheduled"] == empty["missing"] == 12 and not empty["final_lock"]
    record = {
        "id": plan["attempts"][0]["id"],
        "plan_sha256": plan["sha256"],
        "state": "unknown",
        "kind": "synthetic",
        "success": None,
        "usage_complete": False,
    }
    result = calibration.reconcile(plan, [record], plan)
    assert result["unknown_liability"] == 1 and result["missing"] == 11
    assert not result["final_lock"] and not result["provider_evidence_verified"]
    with pytest.raises(ValueError, match="duplicate"):
        calibration.reconcile(plan, [record, record], plan)
    with pytest.raises(ValueError, match="cannot claim"):
        calibration.reconcile(plan, [{**record, "state": "completed", "success": True}], plan)
    changed = copy.deepcopy(plan)
    changed["source_hashes"]["unit-only"] = "b" * 64
    with pytest.raises(ValueError, match="invalidate"):
        calibration.reconcile(plan, [record], changed)
    with pytest.raises(ValueError, match="exact four"):
        calibration.schedule("calibration", ["folio-heldout-close"], 1)


def test_equal_delivery_rejects_wrong_snapshot_metadata_and_grader_tampering(tmp_path, monkeypatch):
    delivery = importlib.import_module("benchmarks.agent-value-runner.delivery")
    schema = ast.parse((driver.ROOT / "app/mcp/tools/context.py").read_text())
    fields = next(
        n.value
        for n in schema.body
        if isinstance(n, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == "_RECORD_FIELDS" for t in n.targets)
    )
    assert ast.literal_eval(fields) == delivery.FIELDS
    sources = driver.source_fingerprint()
    assert all(
        path in sources
        for path in (
            "app/models/common.py",
            "app/mcp/tools/context.py",
            "app/mcp/formatting.py",
            "requirements.lock",
        )
    )
    original_read = driver.dataset.read

    def changed_validator(root, name):
        content = original_read(root, name)
        return (
            content + b"\n# prospective normalization change\n"
            if (root == driver.ROOT and name == "app/models/common.py")
            else content
        )

    with monkeypatch.context() as patch:
        patch.setattr(driver.dataset, "read", changed_validator)
        changed_sources = driver.source_fingerprint()
    assert changed_sources != sources
    assert changed_sources["app/models/common.py"] != sources["app/models/common.py"]
    task = "harbor-development-precision"
    # Fictional authorized-export-shaped rows test serialization, not a live API.
    rows = [
        {
            **request,
            "context_id": f"{i:024x}",
            "revision": 1,
            "updated_at": "2026-10-31T00:00:00Z",
            "storage_or_actor_metadata": "must not enter an agent input",
        }
        for i, request in enumerate(delivery.import_requests(task, "unit-project"), 1)
    ]
    snapshot = delivery.freeze(task, "unit-project", rows)
    m = delivery.inputs(task, "markdown", snapshot)
    c = delivery.inputs(task, "mcp", snapshot)
    assert m["TASK.md"] == c["TASK.md"]
    assert json.loads(m["knowledge/index.json"]) == snapshot["records"]
    assert all("storage_or_actor_metadata" not in r for r in snapshot["records"])
    for r in snapshot["records"]:
        assert m["knowledge/" + r["external_id"] + ".md"] == r["content"].encode()
        assert not r["content"].endswith("\n")
    for changed in ([*rows, rows[0]], rows[:-1], [{**rows[0], "project_id": "other"}, *rows[1:]]):
        with pytest.raises(ValueError):
            delivery.freeze(task, "unit-project", changed)
    changed = copy.deepcopy(snapshot)
    changed["records"][0]["content"] += "forbidden change"
    with pytest.raises(ValueError):
        delivery.inputs(task, "markdown", changed)
    workspace = tmp_path / "candidate"
    workspace.mkdir()
    for name, content in m.items():
        path = workspace / name
        path.parent.mkdir(exist_ok=True)
        path.write_bytes(content)
    calls = []

    def capture(task_id, target, private_root, *, seal, arm):
        calls.append(arm)
        assert set(p.name for p in target.iterdir()) == delivery.dataset.INPUTS | {"TASK.md"}
        assert (target / "domain.py").read_bytes() == b"# unit submitted implementation\n"
        return {"status": "unit-capture-only", "success": None}

    monkeypatch.setattr(delivery.grading, "grade", capture)
    (workspace / "domain.py").write_bytes(b"# unit submitted implementation\n")
    result = delivery.grade(task, "markdown", workspace, snapshot, tmp_path / "absent", {})
    assert result["success"] is None and calls == ["repository"]
    (workspace / "knowledge/index.json").write_bytes(b"{}")
    assert delivery.grade(task, "markdown", workspace, snapshot, tmp_path, {})["code"] == (
        "PROTECTED_INPUT_CHANGED"
    )
    assert calls == ["repository"]
    (workspace / "knowledge/index.json").write_bytes(m["knowledge/index.json"])
    sibling = tmp_path / "sibling"
    sibling.mkdir()
    (workspace / "foreign").symlink_to(sibling, target_is_directory=True)
    assert delivery.grade(task, "markdown", workspace, snapshot, tmp_path, {})["code"] == (
        "INPUT_ALLOWLIST_CHANGED"
    )
    assert calls == ["repository"]


def test_primary_analysis_matches_independent_tiny_examples():
    analysis = importlib.import_module("benchmarks.agent-value-runner.analysis")
    assert analysis.quantile([0, 10], 0.25) == 2.5
    result = analysis.paired_bootstrap({f"unit-{i}": (0, 0, 1) for i in range(20)})
    assert result["q"] == [0, 0, 1]
    assert result["c_minus_m"] == 1 and result["interval95"] == [1, 1]
    assert result["warnings"] and not result["claim_approved"]
    # Two equiprobable clusters give differences -1,0,+1 with mass .25,.5,.25.
    result = analysis.paired_bootstrap({"unit-a": (0, 1, 0), "unit-b": (0, 0, 1)})
    assert result["c_minus_m"] == 0 and result["interval95"] == [-1, 1]
    assert result == analysis.paired_bootstrap({"unit-b": (0, 0, 1), "unit-a": (0, 1, 0)})
    with pytest.raises(ValueError):
        analysis.paired_bootstrap({"unit-invalid": (0, float("nan"), 1)})
    with pytest.raises(ValueError):
        analysis.quantile([float("nan")], 0.5)
    with pytest.raises(ValueError):
        analysis.indices(2, True, 20261030)
    # Each project is homogeneous. Project resampling must keep its families
    # together, rather than pretending the four families are independent.
    families = {"a1": (0, 1, 0), "a2": (0, 1, 0), "b1": (0, 0, 1), "b2": (0, 0, 1)}
    projects = {"a1": "a", "a2": "a", "b1": "b", "b2": "b"}
    sensitivity = analysis.project_sensitivity(families, projects)
    assert sensitivity["projects"] == 2 and sensitivity["families"] == 4
    assert sensitivity["c_minus_m"] == 0 and sensitivity["interval95"] == [-1, 1]
    with pytest.raises(ValueError):
        analysis.project_sensitivity(families, {"a1": "a"})
    assert analysis.latency([1, 2, 3, 4, 100, None]) == {
        "n": 5,
        "missing": 1,
        "p50": 3,
        "p95": 100,
        "method": "nearest rank; observed successes only",
    }
    assert analysis.latency([None])["p95"] is None
    with pytest.raises(ValueError):
        analysis.latency([-1])
    ratio = analysis.efficiency_interval(
        {"unit-only": {"numerators": [10, 20, 10], "completions": [1, 2, 2]}}
    )
    assert ratio["per_completion"] == [10, 10, 5] and ratio["c_minus_m"] == -5
    assert ratio["interval95"] == [-5, -5] and not ratio["claim_approved"]
    undefined = analysis.efficiency_interval(
        {
            "a": {"numerators": [10, 10, 10], "completions": [1, 1, 0]},
            "b": {"numerators": [10, 10, 10], "completions": [1, 1, 1]},
        }
    )
    assert undefined["c_minus_m"] == 10 and undefined["undefined_draws"] > 0
    assert undefined["interval95"] is None  # undefined draws cannot be discarded
    missing = analysis.efficiency_interval(
        {"a": {"numerators": [10, None, 10], "completions": [1, 1, 1]}}
    )
    assert missing["coverage"] == "missing" and missing["per_completion"] is None
