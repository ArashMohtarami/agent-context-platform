"""Small sealed experiment control surface. Paid Codex execution fails closed.

Only the separately labeled synthetic stub path can launch in this phase.
No credentials, active client configuration, model lookup or service startup.
"""

from __future__ import annotations

import argparse
import contextlib
import fcntl
import importlib
import json
import os
import re
import secrets
import shutil
from datetime import UTC, datetime
from pathlib import Path

from scripts.evaluation_lifecycle import atomic
from scripts.focused_process import FocusedError

from . import calibration, sandbox

dataset = importlib.import_module("benchmarks.agent-value.dataset")
sealing = importlib.import_module("benchmarks.agent-value.seal")
ROOT = Path(__file__).resolve().parents[2]
SOURCE = Path(__file__).resolve().parent
STATE = ROOT / ".private/v8-agent-value-planning/phase-2/experiments"
INPUT_SEAL = ROOT / ".private/v8-agent-value-planning/phase-1/initial-seal.json"
HIDDEN = INPUT_SEAL.parent / "hidden"
BLOCKERS = [
    "NO_SOURCE_BOUND_SUBSCRIPTION_OR_PROVIDER_AUTHORIZATION_RECEIPT",
    "HARD_REQUEST_TOKEN_AND_SPEND_ENFORCEMENT_UNPROVEN",
    "CREDENTIAL_BROKER_AND_ALLOWLISTED_CODEX_MCP_EGRESS_UNIMPLEMENTED",
    "FULL_REQUEST_RETRY_COMPACTION_ACCOUNTING_UNPROVEN",
    "CLI_COUNTER_INCLUSION_AND_RESOLVED_MODEL_UNPROVEN",
    "INSTALLED_TOOL_CONFIG_SKILL_EQUIVALENCE_UNVERIFIED",
    "CURRENT_RUNTIME_ACTIVATION_AND_NARROW_CREDENTIAL_SCOPE_RECEIPTS_REQUIRED",
]
HEX = r"[a-f0-9]{64}"
LABEL = r"[A-Za-z0-9][A-Za-z0-9_.-]{0,100}"


def fail(code):
    raise FocusedError(code)


def read(path: Path):
    return json.loads(dataset.read(path.parent, path.name))


def digest(value):
    return dataset.sha(dataset.canonical(value))


def canonical_path(path: Path):
    if path.absolute() != path.resolve() or path.is_symlink():
        fail("NONCANONICAL_CONTROL_PATH")


def integer(value, low, high):
    if type(value) is not int or not low <= value <= high:
        fail("INVALID_BOUND")


def configuration(value):
    fields = {
        "version",
        "mode",
        "study",
        "task_ids",
        "repeats",
        "model",
        "effort",
        "service_tier",
        "currency",
        "total_cap",
        "attempt_reservation",
        "price_sha256",
        "tool_schema_sha256",
        "cli",
        "bounds",
        "stub_scenario",
    }
    if (
        not isinstance(value, dict)
        or set(value) != fields
        or type(value["version"]) is not int
        or value["version"] != 1
    ):
        fail("UNKNOWN_OR_MISSING_CONFIGURATION")
    integer(value["repeats"], 1, 3)
    for key in ("model", "effort", "service_tier", "currency"):
        if not isinstance(value[key], str) or not re.fullmatch(LABEL, value[key]):
            fail("EXPLICIT_CONFIGURATION_REQUIRED")
    for key in ("price_sha256", "tool_schema_sha256"):
        if not isinstance(value[key], str) or not re.fullmatch(HEX, value[key]):
            fail("EXPLICIT_HASH_REQUIRED")
    integer(value["total_cap"], 1, 10**12)
    integer(value["attempt_reservation"], 1, value["total_cap"])
    limits = {
        "wall_seconds": (1, 600),
        "cpu_seconds": (1, 600),
        "memory_bytes": (67108864, 2147483648),
        "workspace_bytes": (1048576, 536870912),
        "output_bytes": (1024, 16777216),
        "turns": (1, 32),
        "tools": (1, 64),
        "events": (1, 4096),
    }
    if not isinstance(value["bounds"], dict) or set(value["bounds"]) != set(limits):
        fail("UNKNOWN_RESOURCE_BOUND")
    for key, (low, high) in limits.items():
        integer(value["bounds"][key], low, high)
    tasks = value["task_ids"]
    if not isinstance(tasks, list) or not tasks or any(not isinstance(t, str) for t in tasks):
        fail("EMPTY_TASK_SELECTION")
    if tasks != sorted(set(tasks)):
        fail("DUPLICATE_OR_UNORDERED_TASKS")
    rows = dataset.load()["tasks"]
    by_id = {t["task_id"]: t for t in rows}
    if any(t not in by_id for t in tasks):
        fail("UNKNOWN_TASK")
    if value["mode"] == "stub-validation":
        if (
            value["study"] != "stub-validation"
            or value["repeats"] != 1
            or len(tasks) > 4
            or any(by_id[t]["split"] != "development" for t in tasks)
            or value["model"] != "synthetic-model"
            or value["currency"] != "TEST"
            or value["stub_scenario"] not in {"ok", "timeout", "interrupt", "overflow", "bad-usage"}
        ):
            fail("STUB_SCOPE_ONLY")
    elif value["mode"] == "codex-plan":
        if value["study"] == "calibration":
            if tasks != list(calibration.TASKS) or value["repeats"] != 1:
                fail("EXACT_DEVELOPMENT_CALIBRATION_TRANCHE_REQUIRED")
        else:
            if value["study"] not in {"development", "heldout"} or value["repeats"] != 3:
                fail("UNREVIEWED_STUDY_DESIGN")
            if tasks != sorted(t["task_id"] for t in rows if t["split"] == value["study"]):
                fail("SAMPLE_REVISION_REQUIRES_NEW_DESIGN")
        if value["stub_scenario"] is not None:
            fail("STUB_SETTING_IN_CODEX_PLAN")
    else:
        fail("UNKNOWN_EXECUTION_MODE")
    if not isinstance(value["cli"], str) or not Path(value["cli"]).is_absolute():
        fail("EXPLICIT_INSTALLED_CLI_REQUIRED")
    contract = read(SOURCE / "adapter.json")
    if dataset.sha(Path(value["cli"]).read_bytes()) != contract["binary_sha256"]:
        fail("UNREVIEWED_CLI_BINARY")
    return value


def source_fingerprint():
    names = read(SOURCE / "inventory.json")["files"]
    actual = {
        p.relative_to(SOURCE).as_posix()
        for p in SOURCE.rglob("*")
        if p.is_file() and "__pycache__" not in p.parts
    }
    if names != sorted(set(names)) or set(names) != actual:
        fail("RUNNER_INVENTORY_CHANGED")
    sources = {
        str(SOURCE.relative_to(ROOT) / n): dataset.sha(dataset.read(SOURCE, n)) for n in names
    }
    for n in (
        "scripts/focused_process.py",
        "scripts/evaluation_lifecycle.py",
        "app/models/common.py",
        "app/mcp/tools/context.py",
        "app/mcp/formatting.py",
        "requirements.lock",
        "skills/python/testing.md",
        "skills/engineering/debugging.md",
    ):
        sources[n] = dataset.sha(dataset.read(ROOT, n))
    return sources


def common_skills():
    contract = read(SOURCE / "adapter.json")
    result = {}
    for row in contract["skills"]:
        if (
            dataset.sha(dataset.read(ROOT, row["source"])) != row["source_sha256"]
            or dataset.sha(dataset.read(SOURCE, row["path"])) != row["sha256"]
        ):
            fail("COMMON_SKILL_CHANGED")
        result[row["path"].removeprefix("skills/")] = row["sha256"]
    return result


def codex_argv(config):
    return [
        config["cli"],
        "--no-daemon",
        "--ask-for-approval",
        "never",
        "exec",
        "--strict-config",
        "--ignore-user-config",
        "--ignore-rules",
        "--ephemeral",
        "--skip-git-repo-check",
        "--sandbox",
        "workspace-write",
        "--model",
        config["model"],
        "--config",
        "model_reasoning_effort=" + json.dumps(config["effort"]),
        "--config",
        "service_tier=" + json.dumps(config["service_tier"]),
        "--config",
        'shell_environment_policy.inherit="none"',
        "--config",
        'history.persistence="none"',
        "--config",
        'web_search="disabled"',
        "--config",
        'model_reasoning_summary="none"',
        "--json",
        "--cd",
        "/work",
        "-",
    ]


def make_plan(config, input_seal: Path = INPUT_SEAL, hidden: Path = HIDDEN):
    config = configuration(config)
    seal = read(input_seal)
    sealing.verify(seal, hidden)
    seed, schedule = calibration.schedule(config["study"], config["task_ids"], config["repeats"])
    attempts = []
    by_id = {t["task_id"]: t for t in dataset.load()["tasks"]}
    input_cache = {
        (t, arm): dataset.inputs(by_id[t], arm) for t in config["task_ids"] for arm in dataset.ARMS
    }
    knowledge_cache = {
        t: [
            {k: r[k] for k in ("record_id", "revision", "status", "sha256")}
            for r in dataset.snapshot(by_id[t])
        ]
        for t in config["task_ids"]
    }
    for task_id, repeat, arm, parent_position in schedule:
        attempt = {
            "id": digest([task_id, repeat, arm])[:32],
            "task_id": task_id,
            "family_id": task_id,
            "repeat": repeat,
            "arm": arm,
            "input_hashes": {k: dataset.sha(v) for k, v in input_cache[task_id, arm].items()},
            "knowledge": knowledge_cache[task_id] if arm != "repository" else [],
            "prompt_sha256": dataset.sha(input_cache[task_id, arm]["TASK.md"]),
        }
        if config["study"] == "calibration":
            attempt["parent_development_position"] = parent_position
        attempts.append(attempt)
    value = {
        "version": 1,
        "configuration": config,
        "input_seal_sha256": dataset.sha(input_seal.read_bytes()),
        "task_commitments": {k: seal[k] for k in ("public_sha256", "private_sha256")},
        "source_hashes": source_fingerprint(),
        "skills": common_skills(),
        "schedule_algorithm": "digest-order-blocks-v1; six arm permutations cyclic",
        "seed": seed,
        "attempts": attempts,
        "concurrency": 1,
        "codex_argv": codex_argv(config),
        "environment": {
            "HOME": "/work/home",
            "CODEX_HOME": "/work/home/.codex",
            "PATH": "/usr/bin:/bin",
        },
        "sandbox_argv": sandbox.argv(
            Path("<attempt-inputs>"), Path("<common-skills>"), config["bounds"], "<seccomp-fd>"
        ),
        "paid_launch_blockers": BLOCKERS.copy(),
        "execution_authorized": False,
        "task_exposure": (
            "Phase1 prompts published by user request; author-reviewed; independent task review "
            "unavailable is a disclosed limitation, not a coding-pilot launch prohibition"
        ),
        "provider_credentials": (
            "no values accepted or inherited; broker not implemented; paid launch refused"
        ),
    }
    value["sha256"] = digest(value)
    return value


def validate_plan(plan):
    value = {k: v for k, v in plan.items() if k != "sha256"}
    if digest(value) != plan.get("sha256") or make_plan(plan["configuration"]) != plan:
        fail("STALE_OR_WIDENED_PLAN")


def identity(value):
    if not isinstance(value, str) or not re.fullmatch(r"[a-f0-9]{32}", value):
        fail("INVALID_RUN_ID")


@contextlib.contextmanager
def locked(base: Path):
    canonical_path(base)
    base.mkdir(parents=True, exist_ok=True, mode=0o700)
    if base.stat().st_mode & 0o077:
        fail("CONTROL_DIRECTORY_PERMISSIONS")
    descriptor = os.open(base / ".worker.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            fail("ONE_WORKER_ALREADY_ACTIVE")
        yield
    finally:
        os.close(descriptor)


def create(plan, base: Path = STATE):
    validate_plan(plan)
    with locked(base):
        run_id = secrets.token_hex(16)
        folder = base / run_id
        folder.mkdir(mode=0o700)
        atomic(
            folder / "owner.json",
            {
                "version": 1,
                "run_id": run_id,
                "uid": os.getuid(),
                "canonical_path": str(folder),
                "plan_sha256": plan["sha256"],
            },
        )
        atomic(folder / "plan.json", plan)
        atomic(
            folder / "ledger.json",
            {
                "version": 1,
                "plan_sha256": plan["sha256"],
                "attempts": [
                    {
                        "id": a["id"],
                        "state": "scheduled",
                        "reserved": 0,
                        "charged": None,
                        "usage": None,
                        "task_success": None,
                    }
                    for a in plan["attempts"]
                ],
            },
        )
        return run_id


def owned(run_id, base):
    identity(run_id)
    canonical_path(base)
    folder = base / run_id
    canonical_path(folder)
    owner = read(folder / "owner.json")
    plan = read(folder / "plan.json")
    if plan.get("sha256") != digest({k: v for k, v in plan.items() if k != "sha256"}):
        fail("PLAN_INTEGRITY_FAILED")
    if owner != {
        "version": 1,
        "run_id": run_id,
        "uid": os.getuid(),
        "canonical_path": str(folder),
        "plan_sha256": plan.get("sha256"),
    }:
        fail("FOREIGN_RUN_OWNERSHIP")
    ledger = read(folder / "ledger.json")
    if (
        set(ledger) != {"version", "plan_sha256", "attempts"}
        or ledger["version"] != 1
        or ledger["plan_sha256"] != plan["sha256"]
        or [r["id"] for r in ledger["attempts"]] != [a["id"] for a in plan["attempts"]]
    ):
        fail("ALL_ATTEMPT_RECONCILIATION_FAILED")
    for row in ledger["attempts"]:
        if set(row) - {
            "id",
            "state",
            "reserved",
            "charged",
            "usage",
            "task_success",
            "result",
            "receipt_sha256",
            "settlement_id",
        } or row["state"] not in {
            "scheduled",
            "reserved",
            "launched",
            "unknown",
            "completed",
            "reconciled",
            "budget_exhausted",
        }:
            fail("UNKNOWN_LEDGER_STATE")
        integer(row["reserved"], 0, plan["configuration"]["attempt_reservation"])
        if row["charged"] is not None:
            integer(row["charged"], 0, plan["configuration"]["attempt_reservation"])
        if row["task_success"] is not None:
            fail("STUB_CANNOT_CLAIM_TASK_SUCCESS")
        if row["state"] in {"reserved", "launched", "unknown"}:
            if (
                row["reserved"] != plan["configuration"]["attempt_reservation"]
                or row["charged"] is not None
            ):
                fail("UNKNOWN_LIABILITY_MUST_STAY_RESERVED")
        elif row["reserved"] or (
            (row["state"] in {"completed", "reconciled"}) != (row["charged"] is not None)
        ):
            fail("LEDGER_ACCOUNTING_STATE_MISMATCH")
    settlements = [r["settlement_id"] for r in ledger["attempts"] if "settlement_id" in r]
    if any(not isinstance(s, str) or not re.fullmatch(LABEL, s) for s in settlements) or len(
        set(settlements)
    ) != len(settlements):
        fail("DUPLICATE_SETTLEMENT_ID")
    return folder, plan, ledger


def authorization(receipt, plan):
    fields = {
        "version",
        "receipt_id",
        "approved_by",
        "purpose",
        "plan_sha256",
        "currency",
        "total_cap",
        "attempt_reservation",
        "attempt_ids",
        "expires_utc",
    }
    if (
        not isinstance(receipt, dict)
        or set(receipt) != fields
        or type(receipt["version"]) is not int
        or receipt["version"] != 1
    ):
        fail("EXPLICIT_AUTHORIZATION_RECEIPT_REQUIRED")
    if not all(
        isinstance(receipt[k], str) and re.fullmatch(LABEL, receipt[k])
        for k in ("receipt_id", "approved_by")
    ):
        fail("MISSING_BUDGET_OWNER")
    config = plan["configuration"]
    integer(receipt["total_cap"], 1, 10**12)
    integer(receipt["attempt_reservation"], 1, receipt["total_cap"])
    for key in ("currency", "total_cap", "attempt_reservation"):
        if receipt[key] != config[key]:
            fail("AUTHORIZATION_BUDGET_MISMATCH")
    if (
        receipt["plan_sha256"] != plan["sha256"]
        or receipt["purpose"] != config["mode"]
        or receipt["attempt_ids"] != [a["id"] for a in plan["attempts"]]
    ):
        fail("AUTHORIZATION_SCOPE_MISMATCH")
    try:
        expires = datetime.fromisoformat(receipt["expires_utc"])
        if expires.tzinfo is None or expires <= datetime.now(UTC):
            fail("AUTHORIZATION_EXPIRED")
    except (TypeError, ValueError):
        fail("INVALID_AUTHORIZATION_EXPIRY")


def accounting(ledger, cap):
    spent = sum(r["charged"] for r in ledger["attempts"] if r["charged"] is not None)
    outstanding = sum(r["reserved"] for r in ledger["attempts"])
    if spent + outstanding > cap:
        fail("BUDGET_INVARIANT_BREACH")
    return {"spent": spent, "reserved": outstanding, "remaining": cap - spent - outstanding}


def status(run_id, base: Path = STATE):
    # Immutable plan and atomically replaced ledger permit consistent read-only
    # status while the exclusive worker holds its mutation lock.
    _, plan, ledger = owned(run_id, base)
    rows = ledger["attempts"]
    return {
        "run_id": run_id,
        "purpose": plan["configuration"]["mode"],
        "scheduled": len(rows),
        "states": {
            s: sum(r["state"] == s for r in rows) for s in sorted({r["state"] for r in rows})
        },
        "accounting": accounting(ledger, plan["configuration"]["total_cap"]),
        "usage_coverage": "incomplete; null is not zero",
        "graded": 0,
        "vendor_agent_attempts": 0,
        "paid_launch_blockers": plan["paid_launch_blockers"],
    }


def run(run_id, receipt, base: Path = STATE):
    with locked(base):
        folder, plan, ledger = owned(run_id, base)
        validate_plan(plan)
        authorization(receipt, plan)
        config = plan["configuration"]
        if config["mode"] != "stub-validation":
            fail("PAID_EXECUTION_BLOCKED_UNSUPPORTED_ACCOUNTING_AND_ISOLATION")
        # One authorization cannot fund another copy of the same plan after
        # owned cleanup or a second create. Keep this binding outside run state.
        registry_path = base / "authorization-bindings.json"
        registry = read(registry_path) if registry_path.exists() else {}
        binding = {"run_id": run_id, "receipt_sha256": digest(receipt)}
        key = digest(receipt["receipt_id"])
        if key in registry and registry[key] != binding:
            fail("AUTHORIZATION_RECEIPT_ALREADY_BOUND")
        registry[key] = binding
        atomic(registry_path, registry)
        if any(r["state"] in {"reserved", "launched", "unknown"} for r in ledger["attempts"]):
            fail("RECONCILE_UNKNOWN_BEFORE_NEW_LAUNCH")
        skills = folder / "skills"
        if not skills.exists():
            skills.mkdir(mode=0o700)
            for name in plan["skills"]:
                target = skills / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(dataset.read(SOURCE, "skills/" + name))
        skill_files = {
            p.relative_to(skills).as_posix() for p in skills.rglob("*") if not p.is_dir()
        }
        if skill_files != set(plan["skills"]) or any(
            dataset.sha(dataset.read(skills, n)) != h for n, h in plan["skills"].items()
        ):
            fail("DISPOSABLE_SKILL_BYTES_CHANGED")
        for row, attempt in zip(ledger["attempts"], plan["attempts"], strict=True):
            if row["state"] != "scheduled":
                continue  # original IDs are never retried
            available = accounting(ledger, config["total_cap"])["remaining"]
            if available < config["attempt_reservation"]:
                row["state"] = "budget_exhausted"
                atomic(folder / "ledger.json", ledger)
                continue
            row["reserved"] = config["attempt_reservation"]
            row["state"] = "reserved"
            row["receipt_sha256"] = digest(receipt)
            atomic(folder / "ledger.json", ledger)
            try:
                parent = folder / "inputs"
                parent.mkdir(exist_ok=True, mode=0o700)
                workspace = parent / row["id"]
                dataset.materialize(attempt["task_id"], workspace, attempt["arm"])
                row["state"] = "launched"  # persisted BEFORE possible side effect
                atomic(folder / "ledger.json", ledger)
                result = sandbox.execute(
                    workspace, skills, config["bounds"], config["stub_scenario"], []
                )
                row["result"] = result
                row["usage"] = result["usage"]
                if result["code"] != "COMPLETED":
                    row["state"] = "unknown"
                    atomic(folder / "ledger.json", ledger)
                    break
                row.update(
                    state="completed", charged=1, reserved=0, settlement_id="synthetic-" + row["id"]
                )  # TEST units only
                atomic(folder / "ledger.json", ledger)
            except BaseException:
                row["state"] = "unknown"
                atomic(folder / "ledger.json", ledger)
                raise
    return status(run_id, base)


def resume(run_id, reconciliation, base: Path = STATE):
    """Reconcile unknown liabilities; never replay a CLI or launch another ID."""
    with locked(base):
        folder, plan, ledger = owned(run_id, base)
        validate_plan(plan)
        unknown = [
            r for r in ledger["attempts"] if r["state"] in {"reserved", "launched", "unknown"}
        ]
        if (
            not isinstance(reconciliation, dict)
            or set(reconciliation) != {"version", "plan_sha256", "reviewer", "receipts"}
            or reconciliation["version"] != 1
            or type(reconciliation["version"]) is not int
            or reconciliation["plan_sha256"] != plan["sha256"]
            or not isinstance(reconciliation["reviewer"], str)
            or not re.fullmatch(LABEL, reconciliation["reviewer"])
        ):
            fail("RECEIPT_RECONCILIATION_REQUIRED")
        receipts = reconciliation["receipts"]
        if (
            not isinstance(receipts, list)
            or any(not isinstance(r, dict) for r in receipts)
            or [r.get("id") for r in receipts] != [r["id"] for r in unknown]
        ):
            fail("MISSING_EXTRA_OR_DUPLICATE_RECONCILIATION")
        settled = {r["settlement_id"] for r in ledger["attempts"] if "settlement_id" in r}
        incoming = [r.get("receipt_id") for r in receipts]
        if any(not isinstance(s, str) or not re.fullmatch(LABEL, s) for s in incoming):
            fail("INVALID_SETTLEMENT_RECEIPT_ID")
        if len(set(incoming)) != len(incoming) or settled.intersection(incoming):
            fail("SETTLEMENT_RECEIPT_ALREADY_USED")
        for receipt, row in zip(receipts, unknown, strict=True):
            if set(receipt) != {"id", "receipt_id", "charged", "coverage", "purpose"}:
                fail("UNKNOWN_RECONCILIATION_FIELDS")
            if (
                receipt["purpose"] != "synthetic-test-receipt"
                or config_mode(plan) != "stub-validation"
                or receipt["coverage"] != "complete"
                or not re.fullmatch(LABEL, receipt["receipt_id"])
            ):
                fail("UNSUPPORTED_REAL_BILLING_RECEIPT")
            integer(receipt["charged"], 0, row["reserved"])
        for receipt, row in zip(receipts, unknown, strict=True):
            row.update(
                state="reconciled",
                charged=receipt["charged"],
                reserved=0,
                receipt_sha256=digest(receipt),
                settlement_id=receipt["receipt_id"],
            )
        accounting(ledger, plan["configuration"]["total_cap"])
        atomic(folder / "ledger.json", ledger)
    return status(run_id, base)


def config_mode(plan):
    return plan["configuration"]["mode"]


def cleanup(run_id, base: Path = STATE):
    with locked(base):
        folder, plan, ledger = owned(run_id, base)
        if any(r["reserved"] for r in ledger["attempts"]):
            fail("UNRESOLVED_LIABILITY_RETAIN_EVIDENCE")
        allowed = {"owner.json", "plan.json", "ledger.json"}
        allowed.update("skills/" + n for n in plan["skills"])
        for attempt in plan["attempts"]:
            allowed.update("inputs/" + attempt["id"] + "/" + n for n in attempt["input_hashes"])
        actual = set()
        for path in folder.rglob("*"):
            if path.is_symlink():
                fail("LINKED_CLEANUP_REFUSED")
            if path.is_file():
                if path.stat().st_nlink != 1:
                    fail("LINKED_CLEANUP_REFUSED")
                actual.add(path.relative_to(folder).as_posix())
            elif not path.is_dir():
                fail("FOREIGN_CLEANUP_ENTRY")
        if actual - allowed:
            fail("FOREIGN_CLEANUP_ENTRY")
        # Refuse even empty foreign directories, not just unknown regular files.
        directories = {str(Path(n).parent) for n in allowed}
        directories |= {str(parent) for n in allowed for parent in Path(n).parents}
        if any(
            p.relative_to(folder).as_posix() not in directories
            for p in folder.rglob("*")
            if p.is_dir()
        ):
            fail("FOREIGN_CLEANUP_DIRECTORY")
        shutil.rmtree(folder)
    return {"run_id": run_id, "status": "owned-state-removed", "processes_signaled": 0}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    plan = sub.add_parser("plan")
    plan.add_argument("--config", type=Path, required=True)
    for name in ("run", "status", "resume", "owned-cleanup"):
        command = sub.add_parser(name)
        command.add_argument("--run-id", required=True)
        if name == "run":
            command.add_argument("--authorization", type=Path, required=True)
        elif name == "resume":
            command.add_argument("--reconciliation", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.command == "plan":
            value = make_plan(read(args.config))
            run_id = create(value)
            result = {"run_id": run_id, "plan_sha256": value["sha256"], "plan": value}
        elif args.command == "run":
            result = run(args.run_id, read(args.authorization))
        elif args.command == "resume":
            result = resume(args.run_id, read(args.reconciliation))
        elif args.command == "status":
            result = status(args.run_id)
        else:
            result = cleanup(args.run_id)
        print(json.dumps(result, indent=2))
        return 0
    except (FocusedError, ValueError, OSError, KeyError, TypeError):
        # No raw exception/config/stream text; it can contain credentials.
        print(json.dumps({"status": "refused", "code": "INVALID_UNSAFE_OR_UNSUPPORTED_REQUEST"}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
