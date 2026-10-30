"""Fixed development tranche and source-bound reconciliation; no provider launch."""

from __future__ import annotations

import hashlib
import importlib
import itertools
import re

dataset = importlib.import_module("benchmarks.agent-value.dataset")
TASKS = (
    "harbor-development-import",
    "harbor-development-precision",
    "parcel-development-pack",
    "parcel-development-tracking",
)
TERMINAL = {"completed", "failed", "timeout", "budget_exhausted", "invalid"}


def digest(value):
    return hashlib.sha256(dataset.canonical(value)).hexdigest()


def schedule(study, task_ids, repeats):
    """Calibration preserves the original development72 positions/permutations."""
    manifest = dataset.load()
    selected = {t["task_id"]: t for t in manifest["tasks"]}
    if study == "calibration":
        if task_ids != list(TASKS) or repeats != 1:
            raise ValueError("exact four development tasks and one attempt required")
        blocks = [
            (t["task_id"], r)
            for t in manifest["tasks"]
            if t["split"] == "development"
            for r in (1, 2, 3)
        ]
    else:
        blocks = [(t, r) for t in task_ids for r in range(1, repeats + 1)]
    if any(t not in selected for t in task_ids):
        raise ValueError("unknown task")
    seed = 20261030 if study == "heldout" else 20261031
    blocks.sort(key=lambda row: digest([seed, *row]))
    permutations = list(itertools.permutations(dataset.ARMS))
    rows = []
    for i, (task_id, repeat) in enumerate(blocks):
        if study == "calibration" and (task_id not in TASKS or repeat != 1):
            continue
        for j, arm in enumerate(permutations[i % 6]):
            rows.append((task_id, repeat, arm, i * 3 + j + 1))
    return seed, rows


def reconcile(plan, records, current_plan):
    """Keep every scheduled ID; never convert stubs, missing or stale data to a lock."""
    if plan != current_plan or plan.get("sha256") != digest(
        {k: v for k, v in plan.items() if k != "sha256"}
    ):
        raise ValueError("changed inputs/settings/source invalidate this calibration version")
    config = plan["configuration"]
    if config["study"] != "calibration":
        raise ValueError("development calibration only; no heldout outcomes")
    _, rows = schedule("calibration", config["task_ids"], config["repeats"])
    expected = [digest([t, r, a])[:32] for t, r, a, _ in rows]
    if [a["id"] for a in plan["attempts"]] != expected:
        raise ValueError("calibration schedule changed")
    if not isinstance(records, list) or any(not isinstance(r, dict) for r in records):
        raise ValueError("explicit attempt records required")
    ids = [r.get("id") for r in records]
    if (
        any(not isinstance(i, str) or not re.fullmatch(r"[a-f0-9]{32}", i) for i in ids)
        or len(set(ids)) != len(ids)
        or any(i not in expected for i in ids)
    ):
        raise ValueError("unknown or duplicate attempt record")
    by_id = {r["id"]: r for r in records}
    result = []
    for attempt in plan["attempts"]:
        row = by_id.get(attempt["id"])
        if row is None:
            result.append({"id": attempt["id"], "state": "missing", "success": False})
            continue
        if set(row) != {"id", "plan_sha256", "state", "kind", "success", "usage_complete"}:
            raise ValueError("unknown or missing reconciliation fields")
        if row["plan_sha256"] != plan["sha256"]:
            raise ValueError("record belongs to another plan/version")
        if row["state"] not in TERMINAL | {"scheduled", "reserved", "launched", "unknown"}:
            raise ValueError("unknown attempt state")
        if row["kind"] not in {"vendor", "synthetic"} or type(row["usage_complete"]) is not bool:
            raise ValueError("explicit provenance and usage coverage required")
        if row["success"] is not None and type(row["success"]) is not bool:
            raise ValueError("unknown grade")
        if row["success"] is True and (row["kind"] != "vendor" or row["state"] != "completed"):
            raise ValueError("synthetic or unsuccessful attempt cannot claim agent success")
        result.append(dict(row))
    return {
        "version": 1,
        "kind": "development-calibration-reconciliation",
        "plan_sha256": plan["sha256"],
        "scheduled": 12,
        "records": result,
        "missing": sum(r["state"] == "missing" for r in result),
        "unknown_liability": sum(r["state"] in {"reserved", "launched", "unknown"} for r in result),
        # Reconciliation is necessary, never an adapter/security/human approval.
        "final_lock": False,
        "provider_evidence_verified": False,
    }
