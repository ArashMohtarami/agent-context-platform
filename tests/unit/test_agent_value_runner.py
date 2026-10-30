"""Portable control contracts; installed-CLI/OS proofs live in integration tests."""

import copy
import importlib
import json
import subprocess

import pytest

from scripts.focused_process import FocusedError

driver = importlib.import_module("benchmarks.agent-value-runner.driver")


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
