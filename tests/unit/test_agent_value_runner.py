"""Portable control contracts; installed-CLI/OS proofs live in integration tests."""

import copy
import importlib
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
