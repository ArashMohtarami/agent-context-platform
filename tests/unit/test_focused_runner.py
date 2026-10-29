"""Focused runner contracts; only explicit stubs, never a broad nested test suite."""

import json
import os
import signal
import sys
import tarfile
from pathlib import Path

import pytest

from scripts import focused_runner as runner
from scripts.focused_process import FocusedError, run
from scripts.focused_stack import LABEL, OwnedStack, assert_owned


@pytest.mark.parametrize(
    "selector",
    [
        "tests/unit",
        "tests/integration/",
        "tests/unit/*.py",
        "-k all",
        "--pyargs tests",
        "tests/unit/test_focused_runner.py;echo unsafe",
        "$(echo unsafe)",
        "../tests/unit/test_x.py",
        "/absolute/test_x.py",
        "tests/unit/test_missing_focused_case.py",
        "tests/unit/test_focused_runner.py::test_*",
    ],
    ids=[
        "unit-dir",
        "integration-dir",
        "glob",
        "keyword",
        "option",
        "shell",
        "substitution",
        "traversal",
        "absolute",
        "missing",
        "node-glob",
    ],
)
def test_rejects_broad_missing_or_unsafe_selectors(selector):
    with pytest.raises(FocusedError):
        runner.validate_selector(selector)


@pytest.mark.parametrize(
    "flag",
    [
        "V6_FULL_ACCEPTANCE",
        "V6_POLICY_DEVELOPMENT_ONLY",
        "PYTEST_ADDOPTS",
        "PYTEST_PLUGINS",
        "COMPOSE_FILE",
        "NODE_OPTIONS",
        "DOCKER_CONTEXT",
    ],
)
def test_inherited_flags_cannot_widen_or_redirect_execution(flag):
    with pytest.raises(FocusedError, match="inherited"):
        runner.clean_environment({"PATH": "/usr/bin", flag: "1"})


def test_environment_drops_provider_secrets_and_live_database_settings():
    env = runner.clean_environment(
        {
            "PATH": "/usr/bin",
            "MONGODB_URI": "live",
            "OPENAI_API_KEY": "fictional",
            "ANTHROPIC_API_KEY": "fictional",
        }
    )
    assert (
        "MONGODB_URI" not in env and "OPENAI_API_KEY" not in env and "ANTHROPIC_API_KEY" not in env
    )
    assert env["EMBEDDING_ALLOW_DOWNLOADS"] == "false"


def test_unknown_empty_and_unbounded_profiles_fail_closed(tmp_path):
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps({"version": 1, "jobs": {}, "profiles": {"empty": []}}))
    for phase in ("unknown", "empty", "../../bad"):
        with pytest.raises(FocusedError):
            runner.load_profile(phase, path)


def test_collection_reconciles_every_selector_and_rejects_extras():
    base = "tests/unit/test_focused_runner.py::test_case"
    runner.selection_matches([base], [base + "[one]", base + "[two]"])
    for nodes in ([], [base, base], [base, "tests/unit/test_other.py::test_other"]):
        with pytest.raises(FocusedError):
            runner.selection_matches([base], nodes)
    with pytest.raises(FocusedError, match="missing"):
        runner.selection_matches([base, base + "_missing"], [base])


def results():
    return {
        "nodes": ["selected"],
        "exitstatus": 0,
        "collection_errors": 0,
        "events": [
            {"nodeid": "selected", "when": phase, "outcome": "passed", "xfail": False}
            for phase in ("setup", "call", "teardown")
        ],
    }


@pytest.mark.parametrize(
    "change", ["skip", "xfail", "failure", "teardown", "missing", "exit", "collect"], ids=str
)
def test_required_incomplete_outcomes_never_pass(change):
    data = results()
    if change == "skip":
        data["events"][1]["outcome"] = "skipped"
    elif change == "xfail":
        data["events"][1]["xfail"] = True
    elif change == "failure":
        data["events"][1]["outcome"] = "failed"
    elif change == "teardown":
        data["events"][2]["outcome"] = "failed"
    elif change == "missing":
        data["events"].pop()
    elif change == "exit":
        data["exitstatus"] = 1
    else:
        data["collection_errors"] = 1
    assert runner.reconcile(data, ["selected"])["status"] == "failed"


def test_planned_collection_and_results_must_match():
    assert runner.reconcile(results(), ["selected"])["passed"] == 1
    with pytest.raises(FocusedError, match="mismatch"):
        runner.reconcile(results(), ["different"])
    data = results()
    data["events"].append({"nodeid": "unselected"})
    with pytest.raises(FocusedError, match="unselected"):
        runner.reconcile(data, ["selected"])


def test_commands_have_literal_narrow_arguments_and_no_shell(tmp_path):
    selector = "tests/unit/test_focused_runner.py::test_case"
    argv = runner.pytest_argv([selector], tmp_path / "result.json")
    assert argv[-1] == selector
    assert "tests/unit" not in argv and "tests/integration" not in argv
    out = tmp_path / "stdout.log"
    marker = tmp_path / "not-created"
    literal = f"; touch {marker}"
    assert (
        run(
            [sys.executable, "-c", "import sys; print(sys.argv[1])", literal],
            cwd=tmp_path,
            env={"PATH": os.defpath},
            log=out,
            timeout=5,
        )
        == 0
    )
    assert out.read_text().strip() == literal and not marker.exists()


@pytest.mark.parametrize("detached", [False, True], ids=["same-session", "detached-session"])
def test_timeout_terminates_owned_process_group(tmp_path, detached):
    child = tmp_path / "child.pid"
    code = (
        "import subprocess,sys,time; "
        "p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)'], "
        f"start_new_session={detached!r}); "
        "open(sys.argv[1],'w').write(str(p.pid)); time.sleep(60)"
    )
    with pytest.raises(FocusedError, match="exceeded"):
        run(
            [sys.executable, "-c", code, str(child)],
            cwd=tmp_path,
            env={"PATH": os.defpath},
            log=tmp_path / "timeout.log",
            timeout=0.5,
        )
    pid = int(child.read_text())
    # A reparented zombie can briefly remain until init reaps it; it is not running.
    status = Path(f"/proc/{pid}/stat")
    stopped = not status.exists() or status.read_text().split()[2] == "Z"
    try:
        assert stopped
    finally:
        if not stopped:  # a failing regression must not leak its own stub
            os.kill(pid, signal.SIGKILL)


@pytest.mark.parametrize("kind", ["container", "network"])
def test_foreign_resource_cleanup_is_refused(kind):
    labels = {LABEL: "ours", "com.docker.compose.project": "project"}
    value = {"Name": "owned", "Labels": labels, "Config": {"Labels": labels}}
    assert_owned(kind, value, name="owned", run_id="ours", project="project")
    for changed in ("other-run", ""):
        with pytest.raises(FocusedError, match="foreign"):
            assert_owned(kind, value, name="owned", run_id=changed, project="project")


def test_cleanup_validates_all_resources_before_removing_any(tmp_path, monkeypatch):
    stack = OwnedStack("a" * 32 + "-unit", {"PATH": os.defpath}, tmp_path)
    stack.state = tmp_path / "state"
    stack.state.mkdir()
    (stack.state / "owner").write_text(stack.owner)
    stack.created = True
    calls = []

    def inspect(kind, name):
        return {
            "Name": name,
            "Id": "id",
            "Config": {
                "Labels": {LABEL: stack.run_id, "com.docker.compose.project": stack.project}
            },
            "Labels": {},
        }

    monkeypatch.setattr(stack, "inspect", inspect)
    monkeypatch.setattr(stack, "command", lambda argv, **kwargs: calls.append(argv))
    with pytest.raises(FocusedError, match="foreign"):
        stack.cleanup()
    assert calls == [] and stack.state.exists()


@pytest.mark.parametrize("foreign_kind", ["container", "network"])
def test_hosted_cleanup_refuses_foreign_auxiliary_resources(tmp_path, monkeypatch, foreign_kind):
    stack = OwnedStack("f" * 32 + "-unit", {"PATH": os.defpath}, tmp_path, oidc=True, hosting=True)
    stack.state = tmp_path / "state"
    stack.state.mkdir()
    (stack.state / "owner").write_text(stack.owner)
    stack.created = True
    calls = []

    def inspect(kind, name):
        auxiliary = name in stack.hosting_containers or name in stack.hosting_networks
        project = stack.hosting_project if auxiliary else stack.project
        labels = {LABEL: stack.run_id, "com.docker.compose.project": project}
        if auxiliary and kind == foreign_kind:
            labels[LABEL] = "foreign"
        return {"Name": name, "Id": name, "Labels": labels, "Config": {"Labels": labels}}

    monkeypatch.setattr(stack, "inspect", inspect)
    monkeypatch.setattr(stack, "command", lambda argv, **kwargs: calls.append(argv))
    with pytest.raises(FocusedError, match="foreign"):
        stack.cleanup()
    assert calls == [] and stack.state.exists()


def test_hosted_profile_keeps_original_browser_and_local_hosting_nodes(tmp_path):
    browser = runner.load_profile("p02-browser-directory")[0]
    assert browser["fixture"] == "browser-hosted"
    prepared = runner.prepare_job(browser, ["selected"], "a" * 32, {}, tmp_path, "node")
    assert prepared["browser_environment"]["V6_DASHBOARD_HOSTING"] == "1"
    assert prepared["browser_environment"]["V6_BROWSER_SERVE_ONLY"] == "1"
    assert prepared["harness_argv"][-1] == runner.BROWSER_HARNESS
    assert not any("test-v6-" in arg for arg in prepared["argv"])
    hosting = runner.load_profile("p02-hosting")[0]
    assert len(hosting["selectors"]) == 2
    assert all(node.endswith("[local-only]") for node in hosting["selectors"])


def test_foreign_state_and_symlinks_are_not_removed(tmp_path):
    stack = OwnedStack("b" * 32 + "-unit", {"PATH": os.defpath}, tmp_path)
    real = tmp_path / "foreign"
    real.mkdir()
    (real / "owner").write_text(stack.owner)
    stack.state = tmp_path / "link"
    stack.state.symlink_to(real, target_is_directory=True)
    stack.created = True
    with pytest.raises(FocusedError, match="foreign"):
        stack.cleanup()
    assert real.exists()


def test_activation_dependency_is_explicit_and_cutover_last():
    jobs = runner.load_profile("activation")
    assert [j["id"] for j in jobs] == ["activation-unit", "activation-real", "activation-cutover"]
    assert len(jobs[0]["selectors"]) == 3
    assert len(jobs[1]["selectors"]) == 11
    assert jobs[2]["requires"] == ["activation-unit", "activation-real"]
    assert all(not j["id"].startswith("activation") for j in runner.load_profile("p01-unit"))


def test_browser_plan_never_uses_a_broad_wrapper():
    job = runner.load_profile("p01-browser")[0]
    argv = runner.browser_argv(job, "node")
    assert "--grep" in argv and argv[argv.index("--grep") + 1].startswith("^")
    assert job["selectors"][0]["file"] in argv
    assert not any("test-v6-" in a for a in argv)


def test_phase_two_outputs_do_not_reuse_phase_one_evidence(tmp_path, monkeypatch):
    baseline = tmp_path / "phase-1" / "runs"
    monkeypatch.setattr(runner, "EVIDENCE", baseline)
    assert runner.evidence_folder("p01-unit") == baseline / "p01-unit"
    assert runner.evidence_folder("activation") == baseline / "activation"
    assert runner.evidence_folder("p02-browser-directory") == (
        tmp_path / "phase-2" / "runs" / "p02-browser-directory"
    )
    assert not tmp_path.joinpath("phase-2").exists()


def test_focused_manifest_survives_source_export_without_private_runs(tmp_path):
    from scripts import prepare_open_source_release as release

    root = tmp_path / "source"
    root.mkdir()
    for name in ("README.md", "LICENSE", "pyproject.toml"):
        (root / name).write_text("Fictional public source\n")
    target = root / "tests/focused/v7-phases.json"
    target.parent.mkdir(parents=True)
    target.write_bytes(runner.MANIFEST.read_bytes())
    private = root / ".private/v7-competitive-planning/phase-1/runs/result.json"
    private.parent.mkdir(parents=True)
    private.write_text("private evidence")
    files = release.audited_files(root)
    archive = tmp_path / "source.tar.gz"
    release.write_archive(root, files, archive)
    with tarfile.open(archive) as tar:
        names = tar.getnames()
    assert "agent-context-platform/tests/focused/v7-phases.json" in names
    assert not any(".private" in p for p in names)


def test_saved_plan_cannot_change_argv_or_selection(tmp_path, monkeypatch):
    phase = "p01-unit"
    job = runner.load_profile(phase)[0]
    nodes = ["tests/unit/test_focused_runner.py::test_case"]
    env = runner.clean_environment({"PATH": os.defpath})
    folder = tmp_path / phase
    folder.mkdir()
    prepared = runner.prepare_job(job, nodes, "c" * 32, env, folder, "node")
    prepared["argv"] = [sys.executable, "-m", "pytest"]  # rejected, never executed
    plan = {
        "phase": phase,
        "source_sha256": "unchanged",
        "run_id": "c" * 32,
        "node": "node",
        "chromium": "/unused",
        "jobs": [prepared],
    }
    runner.write(folder / "plan.json", plan)
    monkeypatch.setattr(runner, "EVIDENCE", tmp_path)
    monkeypatch.setattr(runner, "identity", lambda: "unchanged")
    monkeypatch.setattr(runner, "collect", lambda *args: nodes)
    monkeypatch.setattr(runner, "tools_for", lambda *args: ("node", "/unused"))
    with pytest.raises(FocusedError, match="graph"):
        runner.execute(phase, {"PATH": os.defpath})
    assert not (folder / ("c" * 32)).exists()


def test_stale_plan_is_refused_before_collection_or_services(tmp_path, monkeypatch):
    folder = tmp_path / "p01-unit"
    folder.mkdir()
    runner.write(
        folder / "plan.json", {"phase": "p01-unit", "run_id": "d" * 32, "source_sha256": "old"}
    )
    monkeypatch.setattr(runner, "EVIDENCE", tmp_path)
    monkeypatch.setattr(runner, "identity", lambda: "new")
    monkeypatch.setattr(runner, "collect", lambda *args: pytest.fail("stale plan collected tests"))
    with pytest.raises(FocusedError, match="source changed"):
        runner.execute("p01-unit", {"PATH": os.defpath})


def test_manifest_cannot_supply_extra_runner_options(tmp_path):
    data = json.loads(runner.MANIFEST.read_text())
    data["jobs"]["runner-unit"]["options"] = ["--pyargs", "tests"]
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(data))
    with pytest.raises(FocusedError, match="options"):
        runner.load_profile("p01-unit", path)


def test_pytest_plugin_refuses_changed_collection_before_fixtures(tmp_path):
    from types import SimpleNamespace

    from tests import focused_plugin

    expected = tmp_path / "nodes.json"
    expected.write_text(json.dumps(["expected"]))
    config = SimpleNamespace(_focused={}, getoption=lambda _: str(expected))
    session = SimpleNamespace(config=config, items=[SimpleNamespace(nodeid="unselected")])
    with pytest.raises(pytest.UsageError, match="mismatch"):
        focused_plugin.pytest_collection_finish(session)


def test_interrupted_execution_records_incomplete_selection(tmp_path, monkeypatch):
    job = runner.load_profile("p01-unit")[0]
    env = runner.clean_environment({"PATH": os.defpath})
    nodes = ["tests/unit/test_focused_runner.py::test_case"]
    run_id = "e" * 32
    folder = tmp_path / "p01-unit"
    prepared = runner.prepare_job(job, nodes, run_id, env, folder, "node")
    runner.write(
        folder / "plan.json",
        {
            "phase": "p01-unit",
            "source_sha256": "fixed",
            "run_id": run_id,
            "node": "node",
            "chromium": "/unused",
            "jobs": [prepared],
        },
    )
    monkeypatch.setattr(runner, "EVIDENCE", tmp_path)
    monkeypatch.setattr(runner, "identity", lambda: "fixed")
    monkeypatch.setattr(runner, "tools_for", lambda *args: ("node", "/unused"))
    monkeypatch.setattr(runner, "collect", lambda *args: nodes)

    def interrupt(*args, **kwargs):
        pending = json.loads((folder / run_id / "summary.json").read_text())
        assert pending == {"status": "running", "jobs": []}
        assert json.loads((folder / run_id / "plan.json").read_text())["jobs"] == [prepared]
        raise KeyboardInterrupt

    monkeypatch.setattr(runner, "run", interrupt)
    with pytest.raises(KeyboardInterrupt):
        runner.execute("p01-unit", {"PATH": os.defpath})
    result = json.loads((folder / run_id / "summary.json").read_text())
    assert result["status"] == "incomplete"
    assert result["jobs"][0]["selected"] == 1
    assert result["jobs"][0]["collected"] is None
    assert result["jobs"][0]["passed"] is None


def test_phase_four_evaluation_plan_only_names_selected_startup(tmp_path):
    job = next(j for j in runner.load_profile("p04-real") if j["id"] == "p04-real")
    node = job["selectors"][0]
    planned = runner.prepare_job(job, [node], "a" * 32, {}, tmp_path, "node")
    commands = planned["fixture_graph"]["commands"]
    assert [c[1] for c in commands] == ["start", "start", "reset"]
    assert "acp-evaluation-api:p04" in commands[0]
    assert commands[0][commands[0].index("--approve-root") + 1] == "fictional-acceptance-review"
    assert planned["fixture_graph"]["diagnostic_children"]["no_other_journeys"]
    assert runner.evidence_folder("p04-real").parent.name == "runs"
    assert runner.evidence_folder("p04-real").parent.parent.name == "phase-4"


@pytest.mark.parametrize(
    "source",
    [
        "examples/agent-handoff/smoke.py",
        "examples/agent-handoff/fact.json",
        "examples/starter-notes-api/notes_app.py",
        "examples/starter-notes-api/test_app.py",
        "docs/guide/content.md",
    ],
)
def test_handoff_inputs_invalidate_focused_evidence(monkeypatch, source):
    before = runner.identity()
    original = Path.read_bytes
    changed = runner.ROOT / source

    def read(path):
        return original(path) + (b"\nchanged input" if path == changed else b"")

    monkeypatch.setattr(Path, "read_bytes", read)
    assert runner.identity() != before


@pytest.mark.parametrize(
    "source",
    [
        "benchmarks/retrieval/scorer.py",
        "benchmarks/retrieval/queries.json",
        "benchmarks/retrieval/split.json",
        "benchmarks/retrieval/model-lock.json",
        "benchmarks/retrieval/tasks/reference.py",
        "MANIFEST.in",
    ],
)
def test_benchmark_inputs_invalidate_focused_evidence(monkeypatch, source):
    before = runner.identity()
    original = Path.read_bytes

    def read(path):
        return original(path) + (b"changed input" if path == runner.ROOT / source else b"")

    monkeypatch.setattr(Path, "read_bytes", read)
    assert runner.identity() != before


def test_retrieval_plan_requires_pinned_model_without_starting_services(tmp_path, monkeypatch):
    from benchmarks.retrieval import model

    job = runner.load_profile("p06-real")[0]
    nodes = job["selectors"]
    with pytest.raises(FocusedError, match="offline model cache"):
        runner.prepare_job(job, nodes, "a" * 32, {}, tmp_path, "node")
    monkeypatch.setattr(model, "model_identity", lambda cache: {"cache": cache, "sha256": "first"})
    env = runner.clean_environment({"ACP_FOCUSED_MODEL_CACHE": str(tmp_path)})
    planned = runner.prepare_job(job, nodes, "a" * 32, env, tmp_path, "node")
    assert planned["model_input"]["sha256"] == "first"
    assert planned["argv"][-1] == nodes[0]
    assert len(planned["fixture_children"]) == 1  # only the known disposable archive
    monkeypatch.setattr(
        model, "model_identity", lambda cache: {"cache": cache, "sha256": "changed"}
    )
    assert runner.prepare_job(job, nodes, "a" * 32, env, tmp_path, "node") != planned
