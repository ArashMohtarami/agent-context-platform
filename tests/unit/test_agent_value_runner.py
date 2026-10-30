"""Owned throwaway state and authored stubs only; never launches Codex/provider."""

import copy
import importlib
import json
import shutil
import subprocess
from collections import Counter
from pathlib import Path

import pytest

from scripts.evaluation_lifecycle import atomic
from scripts.focused_process import FocusedError

driver = importlib.import_module("benchmarks.agent-value-runner.driver")
sandbox = importlib.import_module("benchmarks.agent-value-runner.sandbox")
dataset = importlib.import_module("benchmarks.agent-value.dataset")


def config(scenario="ok"):
    help_record = driver.read(
        driver.ROOT / ".private/v8-agent-value-planning/phase-0/cli-help.json"
    )
    cli = help_record["codex"]["resolved_path"]
    assert Path(cli).is_file(), "installed reviewed CLI binary prerequisite; no substitute or skip"
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
        "cli": cli,
        "stub_scenario": scenario,
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


def authorization(plan):
    return {
        "version": 1,
        "receipt_id": "synthetic-approval-1",
        "approved_by": "synthetic-budget-owner",
        "purpose": plan["configuration"]["mode"],
        "plan_sha256": plan["sha256"],
        "currency": plan["configuration"]["currency"],
        "total_cap": plan["configuration"]["total_cap"],
        "attempt_reservation": plan["configuration"]["attempt_reservation"],
        "attempt_ids": [a["id"] for a in plan["attempts"]],
        "expires_utc": "2100-01-01T00:00:00+00:00",
    }


def skill_folder(tmp_path):
    path = tmp_path / "common-skills"
    shutil.copytree(driver.SOURCE / "skills", path)
    return path


def test_plan_is_narrow_sealed_and_launches_nothing(tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("plan launched a subprocess/service/provider")

    monkeypatch.setattr(subprocess, "Popen", forbidden)
    configuration = config()
    plan = driver.make_plan(configuration)
    assert len(plan["attempts"]) == 3 and plan["concurrency"] == 1
    assert plan["execution_authorized"] is False and plan["paid_launch_blockers"]
    assert "--ignore-user-config" in plan["codex_argv"] and "--no-daemon" in plan["codex_argv"]
    assert set(plan["environment"]) == {"HOME", "CODEX_HOME", "PATH"}
    assert (
        len(
            {
                json.dumps(a["knowledge"], sort_keys=True)
                for a in plan["attempts"]
                if a["arm"] != "repository"
            }
        )
        == 1
    )
    held = copy.deepcopy(configuration)
    held.update(
        mode="codex-plan",
        study="heldout",
        repeats=3,
        model="unselected-review-model",
        stub_scenario=None,
    )
    held["task_ids"] = sorted(
        t["task_id"] for t in dataset.load()["tasks"] if t["split"] == "heldout"
    )
    sealed = driver.make_plan(held)
    assert len(sealed["attempts"]) == 216
    triples = [tuple(a["arm"] for a in sealed["attempts"][i : i + 3]) for i in range(0, 216, 3)]
    assert set(Counter(triples).values()) == {12}
    run_id = driver.create(sealed, tmp_path / "state")
    with pytest.raises(FocusedError, match="PAID_EXECUTION_BLOCKED"):
        driver.run(run_id, authorization(sealed), tmp_path / "state")
    assert driver.status(run_id, tmp_path / "state")["accounting"]["spent"] == 0
    for changed in (
        {**configuration, "task_ids": []},
        {**configuration, "extra_args": ["--search"]},
        {**configuration, "model": "$(unsafe)"},
        {**configuration, "repeats": 0},
        {**configuration, "bounds": {**configuration["bounds"], "turns": 1000}},
        {**configuration, "total_cap": True},
        {**configuration, "OPENAI_API_KEY": "not-accepted"},
    ):
        with pytest.raises((FocusedError, ValueError)):
            driver.make_plan(changed)
    tampered = copy.deepcopy(plan)
    tampered["codex_argv"].append("--search")
    tampered["sha256"] = driver.digest({k: v for k, v in tampered.items() if k != "sha256"})
    with pytest.raises(FocusedError, match="STALE_OR_WIDENED"):
        driver.validate_plan(tampered)
    monkeypatch.setattr(driver, "common_skills", lambda: {"changed": "c" * 64})
    with pytest.raises(FocusedError, match="STALE_OR_WIDENED"):
        driver.validate_plan(plan)


def test_budget_reservation_and_unknown_resume_never_double_charge(tmp_path):
    base = tmp_path / "unknown"
    plan = driver.make_plan(config("interrupt"))
    run_id = driver.create(plan, base)
    with pytest.raises(FocusedError, match="AUTHORIZATION"):
        driver.run(run_id, {}, base)
    with pytest.raises(FocusedError, match="AUTHORIZATION"):
        driver.run(run_id, {**authorization(plan), "version": True}, base)
    result = driver.run(run_id, authorization(plan), base)
    assert result["states"] == {"scheduled": 2, "unknown": 1}
    assert result["accounting"] == {"spent": 0, "reserved": 3, "remaining": 9}
    with pytest.raises(FocusedError, match="RECONCILE_UNKNOWN"):
        driver.run(run_id, authorization(plan), base)
    with pytest.raises(FocusedError, match="RECEIPT_RECONCILIATION"):
        driver.resume(run_id, {}, base)
    reconciliation = {
        "version": 1,
        "plan_sha256": plan["sha256"],
        "reviewer": "synthetic-reconciler",
        "receipts": [
            {
                "id": plan["attempts"][0]["id"],
                "receipt_id": "synthetic-charge-1",
                "charged": 2,
                "coverage": "complete",
                "purpose": "synthetic-test-receipt",
            }
        ],
    }
    result = driver.resume(run_id, reconciliation, base)
    assert result["states"] == {"reconciled": 1, "scheduled": 2}
    assert result["accounting"] == {"spent": 2, "reserved": 0, "remaining": 10}
    with pytest.raises(FocusedError, match="MISSING_EXTRA_OR_DUPLICATE"):
        driver.resume(run_id, reconciliation, base)
    assert driver.status(run_id, base)["accounting"]["spent"] == 2
    with driver.locked(base):
        assert driver.status(run_id, base)["accounting"]["spent"] == 2
        with pytest.raises(FocusedError, match="ONE_WORKER"):
            driver.run(run_id, authorization(plan), base)
    result = driver.run(run_id, authorization(plan), base)
    assert result["states"] == {"reconciled": 1, "scheduled": 1, "unknown": 1}
    reused = copy.deepcopy(reconciliation)
    reused["receipts"][0]["id"] = plan["attempts"][1]["id"]
    with pytest.raises(FocusedError, match="SETTLEMENT_RECEIPT_ALREADY_USED"):
        driver.resume(run_id, reused, base)
    assert driver.status(run_id, base)["accounting"] == {"spent": 2, "reserved": 3, "remaining": 7}
    duplicate = driver.create(plan, base)
    with pytest.raises(FocusedError, match="AUTHORIZATION_RECEIPT_ALREADY_BOUND"):
        driver.run(duplicate, authorization(plan), base)
    assert driver.status(duplicate, base)["states"] == {"scheduled": 3}
    budget = config()
    budget["total_cap"] = 4
    limited = driver.make_plan(budget)
    second = driver.create(limited, tmp_path / "limited")
    result = driver.run(second, authorization(limited), tmp_path / "limited")
    assert result["states"] == {"budget_exhausted": 1, "completed": 2}
    assert result["accounting"] == {"spent": 2, "reserved": 0, "remaining": 2}
    assert result["scheduled"] == 3 and result["graded"] == result["vendor_agent_attempts"] == 0
    folder, _, ledger = driver.owned(second, tmp_path / "limited")
    ledger["attempts"].pop()
    atomic(folder / "ledger.json", ledger)
    with pytest.raises(FocusedError, match="ALL_ATTEMPT"):
        driver.status(second, tmp_path / "limited")


def test_timeout_signal_and_cleanup_refuse_foreign_resources(tmp_path, monkeypatch):
    inputs = tmp_path / "input"
    dataset.materialize("folio-development-lookup", inputs, "repository")
    skills = skill_folder(tmp_path)
    bounds = config()["bounds"]
    for scenario, expected in (
        ("timeout", "WALL_LIMIT"),
        ("interrupt", "CHILD_FAILED"),
        ("overflow", "OUTPUT_LIMIT"),
    ):
        result = sandbox.execute(inputs, skills, bounds, scenario, [])
        assert result["code"] == expected, result
        assert result["duration_seconds"] < 8
    with monkeypatch.context() as patch:

        def interrupt(self, event):
            raise KeyboardInterrupt

        patch.setattr(sandbox.Stream, "consume", interrupt)
        interrupted_plan = driver.make_plan(config())
        interrupted = driver.create(interrupted_plan, tmp_path / "interrupted")
        with pytest.raises(KeyboardInterrupt):
            driver.run(interrupted, authorization(interrupted_plan), tmp_path / "interrupted")
        result = driver.status(interrupted, tmp_path / "interrupted")
        assert result["states"] == {"scheduled": 2, "unknown": 1}
        assert result["accounting"]["reserved"] == 3
        with pytest.raises(FocusedError, match="UNRESOLVED_LIABILITY"):
            driver.cleanup(interrupted, tmp_path / "interrupted")
    base = tmp_path / "state"
    plan = driver.make_plan(config())
    run_id = driver.create(plan, base)
    folder = base / run_id
    foreign = tmp_path / "foreign"
    foreign.mkdir()
    (foreign / "keep").write_text("foreign state")
    owner = driver.read(folder / "owner.json")
    atomic(folder / "owner.json", {**owner, "uid": owner["uid"] + 1})
    with pytest.raises(FocusedError, match="FOREIGN_RUN"):
        driver.cleanup(run_id, base)
    atomic(folder / "owner.json", owner)
    (folder / "foreign").symlink_to(foreign, target_is_directory=True)
    with pytest.raises(FocusedError, match="LINKED_CLEANUP"):
        driver.cleanup(run_id, base)
    (folder / "foreign").unlink()
    (folder / "foreign").mkdir()
    with pytest.raises(FocusedError, match="FOREIGN_CLEANUP_DIRECTORY"):
        driver.cleanup(run_id, base)
    (folder / "foreign").rmdir()
    assert driver.cleanup(run_id, base)["processes_signaled"] == 0
    assert (foreign / "keep").read_text() == "foreign state" and not folder.exists()
    with pytest.raises(FocusedError, match="INVALID_RUN_ID"):
        driver.cleanup("../foreign", base)


def test_task_tools_cannot_read_control_secrets_or_grader_inputs(tmp_path, monkeypatch):
    inputs = tmp_path / "input"
    dataset.materialize("folio-development-lookup", inputs, "repository")
    skills = skill_folder(tmp_path)
    secret = tmp_path / "control-key"
    secret.write_text("fictional-provider-key-never-mount")
    sibling = tmp_path / "sibling"
    sibling.mkdir()
    (sibling / "answer").write_text("fictional sibling")
    history = tmp_path / ".git"
    history.mkdir()
    (history / "answers").write_text("fictional prior reference")
    monkeypatch.setenv("OPENAI_API_KEY", secret.read_text())
    probes = [
        str(secret),
        str(sibling / "answer"),
        str(history / "answers"),
        str(driver.HIDDEN / "folio-heldout-close/checks.json"),
        "/inputs/../control-key",
    ]
    result = sandbox.execute(inputs, skills, config()["bounds"], "probe", probes)
    assert result["code"] == "COMPLETED", result
    assert result["probe_denied"] == [True] * (len(probes) + 5)
    assert secret.read_text() not in json.dumps(result)
    assert result["usage"]["billed_spend"] is None and not result["usage"]["spend_supported"]
    assert len(driver.common_skills()) == 2
    assert driver.common_skills() == {
        str(p.relative_to(skills)): dataset.sha(p.read_bytes()) for p in skills.rglob("SKILL.md")
    }
