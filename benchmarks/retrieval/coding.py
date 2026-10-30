"""Optional paired study planner/checker; does not launch any model or agent."""

from __future__ import annotations

import copy
import hashlib
import json
import math
import random
import re
from pathlib import Path
from types import ModuleType
from typing import Any, cast

ROOT = Path(__file__).resolve().parent
TASK_ARMS = ("repository", "markdown", "platform")


def tasks() -> list[dict[str, Any]]:
    return cast(list[dict[str, Any]], json.loads((ROOT / "tasks.json").read_text())["tasks"])


def check_solution(module: ModuleType, task: dict[str, Any]) -> dict[str, Any]:
    """Check behavior of an explicitly reviewed module. Not a code sandbox.

    A future agent run must execute this in its own disposable, bounded workspace.
    No provider artifacts are imported by the default retrieval benchmark.
    """
    outcomes = []
    function = getattr(module, task["id"], None)
    if not callable(function):
        return {"task_id": task["id"], "checks": len(task["cases"]), "passed": 0, "success": False}
    for case in task["cases"]:
        inputs = copy.deepcopy(case["args"])
        try:
            actual = function(*inputs)
            passed = "raises" not in case and actual == case["expected"] and inputs == case["args"]
        except Exception as error:
            passed = case.get("raises") == type(error).__name__
        outcomes.append(passed)
    return {
        "task_id": task["id"],
        "checks": len(outcomes),
        "passed": sum(outcomes),
        "success": bool(outcomes) and all(outcomes),
    }


def paired_plan(
    *, agent: str, model: str, repo_commit: str, budget: dict[str, Any], seed: int = 20261024
) -> dict[str, Any]:
    if not agent.strip() or not model.strip() or not re.fullmatch(r"[0-9a-f]{40}", repo_commit):
        raise ValueError("pinned agent/model/repo identity required")
    if set(budget) != {"seconds", "input_tokens", "output_tokens", "money_total"} or any(
        not isinstance(v, int | float) or isinstance(v, bool) or v <= 0 for v in budget.values()
    ):
        raise ValueError("explicit positive time/token/total monetary bounds required")
    rng = random.Random(seed)  # noqa: S311 -- recorded study order
    attempts = []
    for task in tasks():
        prompt = task["prompt"]
        for repeat in range(3):
            arms = list(TASK_ARMS)
            rng.shuffle(arms)
            for arm in arms:
                attempts.append(
                    {
                        "id": f"{task['id']}-{repeat}-{arm}",
                        "task_id": task["id"],
                        "repeat": repeat,
                        "arm": arm,
                        "fresh_session": True,
                        "workspace": f"{task['id']}/{repeat}/{arm}",
                        "prompt": prompt,
                        "prompt_sha256": hashlib.sha256(prompt.encode()).hexdigest(),
                        "support": task["support"] if arm != "repository" else [],
                        "agent": agent,
                        "model": model,
                        "repo_commit": repo_commit,
                        "budget": budget,
                        "inputs": ["tasks/starter.py"],
                        "forbidden_inputs": ["tasks/reference.py", "tasks.json", "queries.json"],
                    }
                )
    return {
        "version": 1,
        "seed": seed,
        "status": "plan only; not measured",
        "attempts": attempts,
        "money_total_cap": budget["money_total"],
    }


def reconcile(plan: dict[str, Any], results: list[dict[str, Any]]) -> dict[str, Any]:
    planned = {a["id"] for a in plan["attempts"]}
    recorded = {r["id"] for r in results}
    if len(recorded) != len(results) or recorded - planned:
        raise ValueError("extra or duplicate coding outcomes")
    by_id = {r["id"]: r for r in results}
    cases = {task["id"]: len(task["cases"]) for task in tasks()}
    rows = []
    for attempt in plan["attempts"]:
        r = by_id.get(attempt["id"])
        if r and (
            r["status"] not in {"ok", "error", "timeout", "failed"}
            or not isinstance(r.get("elapsed_seconds"), int | float)
            or not math.isfinite(r["elapsed_seconds"])
            or r["elapsed_seconds"] < 0
        ):
            raise ValueError("invalid coding outcome")
        success = bool(
            r
            and r["status"] == "ok"
            and r.get("checks") == cases[attempt["task_id"]]
            and r.get("passed") == r.get("checks")
            and r["elapsed_seconds"] <= attempt["budget"]["seconds"]
        )
        rows.append(
            {
                "id": attempt["id"],
                "arm": attempt["arm"],
                "task_id": attempt["task_id"],
                "repeat": attempt["repeat"],
                "success": success,
                "status": r["status"] if r else "missing",
                "elapsed_seconds": r.get("elapsed_seconds") if r else None,
            }
        )
    paired = []
    for task in tasks():
        for repeat in range(3):
            block = [r for r in rows if r["task_id"] == task["id"] and r["repeat"] == repeat]
            paired.append(
                {
                    "task_id": task["id"],
                    "repeat": repeat,
                    "outcomes": {r["arm"]: r["success"] for r in block},
                    "elapsed_seconds": {r["arm"]: r["elapsed_seconds"] for r in block},
                }
            )
    intervals = None
    if planned == recorded:
        rng = random.Random(plan["seed"])  # noqa: S311 -- paired task bootstrap
        families = [task["id"] for task in tasks()]
        draws = []
        for _ in range(2000):
            values = [
                int(r["outcomes"]["platform"]) - int(r["outcomes"]["markdown"])
                for task_id in rng.choices(families, k=len(families))
                for r in paired
                if r["task_id"] == task_id
            ]
            draws.append(sum(values) / len(values))
        draws.sort()
        intervals = [draws[49], draws[1949]]
    return {
        "planned": len(planned),
        "recorded": len(recorded),
        "rows": rows,
        "complete": planned == recorded,
        "status": "complete" if planned == recorded else "incomplete; no benefit estimate",
        "paired": paired,
        "platform_minus_markdown_task_bootstrap_95_percentile": intervals,
        "success_by_arm": {
            arm: sum(r["success"] for r in rows if r["arm"] == arm) for arm in TASK_ARMS
        },
    }
