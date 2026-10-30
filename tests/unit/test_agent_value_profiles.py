"""Only the new explicit routing boundary; no services or nested test runners."""

import json
from pathlib import Path

import pytest

from scripts import focused_runner as runner
from scripts.focused_process import FocusedError


def test_v8_profiles_keep_v7_selection_and_phase_identities(tmp_path, monkeypatch):
    old = json.loads(runner.MANIFEST.read_text())
    for phase in old["profiles"]:
        jobs = runner.load_profile(phase)
        assert [j["id"] for j in jobs] == old["profiles"][phase]
        assert all(
            {k: v for k, v in job.items() if k != "id"} == old["jobs"][job["id"]] for job in jobs
        )
    assert runner.evidence_folder("p04-real").parent.parent.name == "phase-4"
    assert (
        runner.evidence_folder("v8-p01-fixtures")
        == runner.ROOT / ".private/v8-agent-value-planning/phase-1/runs/v8-p01-fixtures"
    )
    assert (
        runner.evidence_folder("v8-p02-runner")
        == runner.ROOT / ".private/v8-agent-value-planning/phase-2/runs/v8-p02-runner"
    )
    for phase in runner.V8_PHASES:
        jobs = runner.load_profile(phase)
        if phase == "v8-p03-activation":
            assert [j["id"] for j in jobs] == old["profiles"]["activation"]
            assert all(
                {k: v for k, v in j.items() if k != "id"} == old["jobs"][j["id"]] for j in jobs
            )
        elif phase == "v8-p03-real":
            assert [(j["fixture"], j["expected_count"]) for j in jobs] == [
                ("evaluation", 1),
                ("none", 1),
            ]
            assert runner.evidence_folder(phase).parent.parent.name == "phase-3"
        else:
            assert all(j["fixture"] == "none" and not j["requires"] for j in jobs)
    for phase in ("v8-p03-runner", "v8-../../private", "--manifest"):
        with pytest.raises(FocusedError):
            runner.load_profile(phase)
    monkeypatch.setattr(runner, "identity", lambda: "unchanged-v7-formula")
    assert runner.phase_identity("p01-unit") == "unchanged-v7-formula"
    before = runner.phase_identity("v8-p01-fixtures")
    changed = tmp_path / "reviewed-v8.json"
    changed.write_bytes(runner.V8_MANIFEST.read_bytes() + b"\n")
    monkeypatch.setattr(runner, "V8_MANIFEST", changed)
    assert runner.phase_identity("v8-p01-fixtures") != before
    assert runner.phase_identity("p01-unit") == "unchanged-v7-formula"
    assert "--manifest" not in Path(runner.__file__).read_text()
