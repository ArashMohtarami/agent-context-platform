"""Original fixture/hidden-check validation. No agent/provider execution."""

import importlib
import json
import shutil
from pathlib import Path

import pytest

dataset = importlib.import_module("benchmarks.agent-value.dataset")
grading = importlib.import_module("benchmarks.agent-value.grading")
sealing = importlib.import_module("benchmarks.agent-value.seal")
ROOT = Path(__file__).resolve().parents[2]
PRIVATE = ROOT / ".private/v8-agent-value-planning/phase-1/hidden"


def reviewed_candidate_seal():
    path = PRIVATE.parent / "candidate-seal.json"
    if not path.is_file():
        pytest.fail("explicit candidate input seal missing; incomplete")
    value = json.loads(path.read_text())
    sealing.verify(value, PRIVATE)
    return value


def test_family_split_and_seal_reject_leakage_or_tampering(tmp_path):
    manifest = dataset.load()
    assert len(manifest["tasks"]) == 32
    assert (
        sum(t["outcome"] == "coding" and t["split"] == "heldout" for t in manifest["tasks"]) == 20
    )
    if not PRIVATE.is_dir():
        pytest.fail("original private checker bundle is a prerequisite; no fabricated substitute")
    seal = reviewed_candidate_seal()
    assert seal == sealing.build(PRIVATE)
    assert seal["execution_authorized"] is False
    assert "private" not in sealing.commitment(seal)
    copied = tmp_path / "public"
    shutil.copytree(dataset.ROOT, copied, ignore=shutil.ignore_patterns("__pycache__"))
    hidden = tmp_path / "hidden"
    shutil.copytree(PRIVATE, hidden)
    sealing.verify(seal, hidden, copied)
    first = manifest["tasks"][0]
    checks = hidden / first["task_id"] / "checks.json"
    original = checks.read_bytes()
    value = json.loads(original)
    value["cases"].pop()
    checks.write_text(json.dumps(value))
    with pytest.raises(ValueError, match="checker selection|seal changed"):
        sealing.verify(seal, hidden, copied)
    checks.write_bytes(original)
    split = copied / "split.json"
    value = json.loads(split.read_text())
    value["heldout"].append(value["development"][0])
    split.write_text(json.dumps(value))
    with pytest.raises(ValueError, match="split manifest"):
        sealing.build(hidden, copied)
    split.write_bytes((dataset.ROOT / "split.json").read_bytes())
    knowledge = copied / "knowledge/index.json"
    value = json.loads(knowledge.read_text())
    selected = next(r for r in value["records"] if r["split"] == "heldout")
    selected["split"] = "development"
    knowledge.write_text(json.dumps(value))
    with pytest.raises(ValueError, match="split leakage"):
        sealing.build(hidden, copied)
    knowledge.write_bytes((dataset.ROOT / "knowledge/index.json").read_bytes())
    reference = hidden / first["task_id"] / "reference/domain.py"
    reference.write_text(reference.read_text() + "\n# changed\n")
    with pytest.raises(ValueError, match="seal changed"):
        sealing.verify(seal, hidden, copied)
    target = tmp_path / "seal.json"
    sealing.write_once(target, dataset.canonical(seal))
    sealing.write_once(target, dataset.canonical(seal))
    with pytest.raises(ValueError, match="cannot be replaced"):
        sealing.write_once(target, b"different")


def test_allowed_inputs_exclude_oracle_history_and_sibling_workspaces(tmp_path):
    manifest = dataset.load()
    for selected in manifest["tasks"]:
        materializations = {}
        for arm in dataset.ARMS:
            workspace = tmp_path / (selected["task_id"] + "-" + arm)
            materializations[arm] = dataset.materialize(selected["task_id"], workspace, arm)
            names = {
                p.relative_to(workspace).as_posix() for p in workspace.rglob("*") if p.is_file()
            }
            assert names == set(dataset.inputs(selected, arm))
            assert not any(
                any(
                    part in {".git", ".private", "reference", "checks.json"}
                    for part in Path(p).parts
                )
                for p in names
            )
        assert materializations["markdown"]["knowledge"] == materializations["mcp"]["knowledge"]
        assert materializations["repository"]["knowledge"] == []
        records = dataset.snapshot(selected)
        assert all(
            r["split"] in {"common", selected["split"]}
            and r["project_id"] == selected["project_id"]
            for r in records
        )
        assert len(records) >= 3  # a complete project/split snapshot, not a gold excerpt
    chosen = manifest["tasks"][0]
    workspace = tmp_path / "probe"
    dataset.materialize(chosen["task_id"], workspace, "repository")
    with pytest.raises(ValueError, match="new canonical"):
        dataset.materialize(chosen["task_id"], workspace, "repository")
    outside = tmp_path / "private-reference.txt"
    outside.write_text("fictional expected output; never mounted")
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git/answers").write_text("fictional old solution")
    probe = f"""
import os,socket
def execute(store,c):
    result={{}}
    paths={{
        'absolute':{str(outside)!r},
        'parent':'/work/../private-reference.txt',
        'history':'/work/../.git/answers',
        'host':'{PRIVATE.as_posix()}/folio-heldout-close/checks.json',
        'sibling':'/work/../folio-heldout-close/domain.py',
    }}
    for name,path in paths.items():
        try:
            with open(path) as stream: stream.read(1)
            result[name]=False
        except OSError: result[name]=True
    try:
        socket.socket()
        result['network']=False
    except PermissionError: result['network']=True
    try:
        pid=os.fork()
        if pid==0: os._exit(0)
        os.waitpid(pid,0)
        result['fork']=False
    except PermissionError: result['fork']=True
    try:
        with open('/work/domain.py','w') as stream: stream.write('changed')
        result['readonly']=False
    except OSError: result['readonly']=True
    return result
"""
    (workspace / "domain.py").write_text(probe)
    requests = [{"case_id": "isolation", "input": {"state": {}, "commands": [{"action": "probe"}]}}]
    report = grading.sandbox(workspace, requests)
    assert report["status"] == "completed", report
    assert report["rows"][0]["result"]["results"] == [
        {
            k: True
            for k in (
                "absolute",
                "parent",
                "history",
                "host",
                "sibling",
                "network",
                "fork",
                "readonly",
            )
        }
    ]
    for candidate, code in (
        ("silent", "import os\nos._exit(0)\n"),
        ("empty", 'import os\nprint(\'{"version":1,"rows":[]}\',flush=True)\nos._exit(0)\n'),
        ("error", "raise RuntimeError('fictional child failure')\n"),
        ("timeout", "import time\ntime.sleep(8)\n"),
    ):
        (workspace / "api.py").write_text(code)
        report = grading.sandbox(workspace, requests)
        assert report["status"] in {"error", "timeout"}, (candidate, report)
    (workspace / "api.py").unlink()
    (workspace / "api.py").symlink_to(outside)
    with pytest.raises(ValueError, match="symlink"):
        dataset.read(workspace, "api.py")


@pytest.mark.parametrize("task_id", [t["task_id"] for t in dataset.load()["tasks"]])
def test_behavioral_graders_accept_reference_and_reject_meaningful_mutants(tmp_path, task_id):
    selected = dataset.task(task_id)
    if not PRIVATE.is_dir():
        pytest.fail("private original reference/checker bundle missing; incomplete")
    seal = reviewed_candidate_seal()
    results = {}
    for variant in ("reference", "alternative", "mutant", "wrong"):
        workspace = tmp_path / variant
        dataset.materialize(task_id, workspace, "repository")
        source = PRIVATE / task_id / variant
        if selected["outcome"] == "safe_action":
            shutil.copyfile(source / "response.json", workspace / "response.json")
        else:
            for name in ("api.py", "domain.py"):
                shutil.copyfile(source / name, workspace / name)
        result = grading.grade(task_id, workspace, PRIVATE, seal=seal)
        results[variant] = result
        if variant in {"reference", "alternative"}:
            if selected["outcome"] == "safe_action":
                assert result["automated_checks_passed"] and result["success"] is None
                assert result["human_review_required"]
            else:
                assert result["success"] and result["passed"] == result["required"], result
        else:
            assert result["success"] is False, (task_id, variant, result)
            if selected["category"] == "feature" and variant == "mutant":
                assert 0 < result["passed"] < result["required"], result
    if selected["outcome"] == "coding":
        regression = tmp_path / "health-regression"
        shutil.copytree(tmp_path / "reference", regression)
        with (regression / "api.py").open("a") as stream:
            stream.write(
                "\noriginal_handle=handle\n"
                "def handle(envelope):\n"
                "    output=original_handle(envelope)\n"
                "    for i,command in enumerate(envelope['commands']):\n"
                "        if command['action']=='health': output['results'][i]={'ok':False}\n"
                "    return output\n"
            )
        result = grading.grade(task_id, regression, PRIVATE, seal=seal)
        assert result["success"] is False, result
        results["health-regression"] = result
    protected = tmp_path / "reference/visible.json"
    protected.write_text("{}\n")
    assert (
        grading.grade(task_id, protected.parent, PRIVATE, seal=seal)["code"]
        == "PROTECTED_INPUT_CHANGED"
    )
    # Numeric validation receipt only. These are authored fixtures, never agent runs.
    report = {"purpose": "grader-validation; no agent", "task_id": task_id, "variants": results}
    proof = Path(__import__("os").environ.get("ACP_FOCUSED_LIFECYCLE_REPORT", str(tmp_path))) / (
        task_id + "-grader.json"
    )
    proof.write_text(json.dumps(report, indent=2) + "\n")
