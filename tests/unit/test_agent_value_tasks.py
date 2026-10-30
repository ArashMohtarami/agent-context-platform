"""Portable public-input contracts; private grader validation is integration-only."""

import importlib
from collections import Counter

import pytest

dataset = importlib.import_module("benchmarks.agent-value.dataset")
sealing = importlib.import_module("benchmarks.agent-value.seal")


def test_public_task_inventory_and_family_split_are_complete():
    manifest = dataset.load()
    assert Counter(t["split"] for t in manifest["tasks"]) == {"development": 8, "heldout": 24}
    assert len({t["family_id"] for t in manifest["tasks"]}) == 32
    assert Counter(t["category"] for t in manifest["tasks"] if t["split"] == "heldout") == {
        category: 4 for category in dataset.CATEGORIES
    }
    assert len(sealing.public_files(dataset.ROOT)) == 280


def test_public_materializations_keep_equal_knowledge_and_exclude_private_inputs(tmp_path):
    for selected in dataset.load()["tasks"]:
        receipts = {}
        for arm in dataset.ARMS:
            workspace = tmp_path / (selected["task_id"] + "-" + arm)
            receipts[arm] = dataset.materialize(selected["task_id"], workspace, arm)
            names = {
                p.relative_to(workspace).as_posix() for p in workspace.rglob("*") if p.is_file()
            }
            assert names >= dataset.INPUTS | {"TASK.md"}
            if arm == "markdown":
                records = dataset.snapshot(selected)
                assert names == dataset.INPUTS | {"TASK.md"} | {
                    "knowledge/" + row["record_id"] + ".md" for row in records
                }
                assert all(
                    row["project_id"] == selected["project_id"]
                    and row["split"] in {"common", selected["split"]}
                    for row in records
                )
            else:
                assert names == dataset.INPUTS | {"TASK.md"}
            assert all(part not in names for part in ("checks.json", "response.json", ".git"))
        assert receipts["repository"]["knowledge"] == []
        assert receipts["markdown"]["knowledge"] == receipts["mcp"]["knowledge"]
        assert receipts["markdown"]["files"]["TASK.md"] == receipts["mcp"]["files"]["TASK.md"]


def test_public_inputs_refuse_links_traversal_and_missing_private_seal(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    outside = tmp_path / "outside"
    outside.write_text("fictional unit sentinel")
    (source / "linked").symlink_to(outside)
    with pytest.raises(ValueError, match="symlink"):
        dataset.read(source, "linked")
    with pytest.raises(ValueError, match="allowlist"):
        dataset.read(source, "../outside")
    with pytest.raises(FileNotFoundError):
        sealing.build(tmp_path / "missing-private-bundle")
