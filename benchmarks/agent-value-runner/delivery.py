"""Equal authorized M/C exports; hidden graders never enter delivery inputs."""

from __future__ import annotations

import importlib
import re
import tempfile
from datetime import datetime
from pathlib import Path

from pydantic import TypeAdapter

from app.models.common import ContentText, Identifier

dataset = importlib.import_module("benchmarks.agent-value.dataset")
grading = importlib.import_module("benchmarks.agent-value.grading")
FIELDS = (
    "context_id",
    "project_id",
    "type",
    "title",
    "importance",
    "tags",
    "source",
    "status",
    "external_id",
    "revision",
    "updated_at",
    "content",
)
CONTENT = TypeAdapter(ContentText)
IDENTIFIER = TypeAdapter(Identifier)


def import_requests(task_id, project_id):
    """Ordinary complete snapshot, no task-specific supporting excerpts."""
    project_id = IDENTIFIER.validate_python(project_id)
    return [
        {
            "project_id": project_id,
            "type": "convention",
            "title": row["record_id"],
            "content": CONTENT.validate_python(row["content"]),
            "importance": 0.5,
            "tags": ["v8-fixture"],
            "source": row["source"],
            "status": row["status"],
            "external_id": row["record_id"],
        }
        for row in dataset.snapshot(dataset.task(task_id))
    ]


def freeze(task_id, project_id, authorized_records):
    """Project only get_context fields; bind actual API IDs/revisions separately.

    Authored source revisions and fresh API revision counters are different
    namespaces. No fabricated API revision/history is inserted during import.
    """
    requests = {r["external_id"]: r for r in import_requests(task_id, project_id)}
    if not isinstance(authorized_records, list) or len(authorized_records) != len(requests):
        raise ValueError("complete authorized snapshot required")
    records = {}
    identities = set()
    for raw in authorized_records:
        if not isinstance(raw, dict) or any(k not in raw for k in FIELDS):
            raise ValueError("missing authorized record fields")
        row = {k: raw[k] for k in FIELDS}
        key = row["external_id"]
        if key not in requests or key in records:
            raise ValueError("extra or duplicate snapshot record")
        if (
            not isinstance(row["context_id"], str)
            or not re.fullmatch(r"[a-f0-9]{24}", row["context_id"])
            or row["context_id"] in identities
            or type(row["revision"]) is not int
            or row["revision"] < 1
            or not isinstance(row["updated_at"], str)
            or not row["updated_at"]
        ):
            raise ValueError("invalid actual record identity/revision")
        if any(row[k] != value for k, value in requests[key].items()):
            raise ValueError("wrong project/content/status or metadata in authorized snapshot")
        if datetime.fromisoformat(row["updated_at"].replace("Z", "+00:00")).tzinfo is None:
            raise ValueError("aware API timestamp required")
        identities.add(row["context_id"])
        records[key] = row
    selected = dataset.task(task_id)
    value = {
        "version": 1,
        "project_id": project_id,
        "knowledge_snapshot": selected["knowledge_snapshot"],
        "source": [
            {k: row[k] for k in ("record_id", "revision", "status", "sha256")}
            for row in dataset.snapshot(selected)
        ],
        "records": [records[k] for k in sorted(records)],
        "normalization": "existing ContentText validation, equally M/C; interior bytes preserved",
    }
    value["sha256"] = dataset.sha(dataset.canonical(value))
    return value


def inputs(task_id, arm, snapshot):
    selected = dataset.task(task_id)
    if snapshot != freeze(task_id, snapshot["project_id"], snapshot["records"]):
        raise ValueError("changed delivery snapshot")
    files = dataset.inputs(selected, arm)
    if arm == "markdown":
        for row in snapshot["records"]:
            files["knowledge/" + row["external_id"] + ".md"] = row["content"].encode()
        # This is declared equal agent metadata, not the raw REST controller receipt.
        files["knowledge/index.json"] = dataset.canonical(snapshot["records"])
    return files


def grade(task_id, arm, workspace, snapshot, private_root, seal):
    """Verify overlay protection, then grade only code against the original seal."""
    files = inputs(task_id, arm, snapshot)
    selected = dataset.task(task_id)
    if workspace.is_symlink() or any(p.is_symlink() for p in workspace.rglob("*")):
        return {"status": "failed", "success": False, "code": "INPUT_ALLOWLIST_CHANGED"}
    allowed = set(files) | ({"response.json"} if selected["outcome"] == "safe_action" else set())
    actual = {p.relative_to(workspace).as_posix() for p in workspace.rglob("*") if not p.is_dir()}
    if actual - allowed or set(files) - actual:
        return {"status": "failed", "success": False, "code": "INPUT_ALLOWLIST_CHANGED"}
    protected = set(files) - set(selected["editable"])
    if any(dataset.read(workspace, n) != files[n] for n in protected):
        return {"status": "failed", "success": False, "code": "PROTECTED_INPUT_CHANGED"}
    with tempfile.TemporaryDirectory(prefix="acp-v8-delivery-grade-") as temporary:
        target = Path(temporary) / "candidate"
        dataset.materialize(task_id, target, "repository")
        for name in selected["editable"]:
            (target / name).write_bytes(dataset.read(workspace, name))
        if "response.json" in actual:
            (target / "response.json").write_bytes(dataset.read(workspace, "response.json"))
        return grading.grade(task_id, target, private_root, seal=seal, arm="repository")
