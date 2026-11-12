"""Exact12 descriptive Plus pilot: plan/run/status/resume/owned-cleanup.

Zero-cash authority is separate from unavailable token/quota/billing counters.
The original codex-plan launch guard is unchanged. No provider runs in pytest.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import signal
from datetime import UTC, datetime
from pathlib import Path

from scripts.evaluation_lifecycle import atomic
from scripts.focused_process import FocusedError

from . import calibration, delivery, driver, native, runtime, session

BASE = driver.ROOT / ".private/v8-agent-value-planning/phase-4/pilots"
COMMISSIONS = driver.ROOT / ".private/v8-agent-value-planning/phase-4/commissions"
ACCOUNTING = {
    "version": "descriptive-Plus-pilot-v1",
    "currency": "USD",
    "additional_cash_cap": "0.00",
    "purchased_credits_allowed": False,
    "api_billing_allowed": False,
    "token_or_quota_upper_bound": None,
    "failed_request_billing_coverage": "unproven",
    "cash_savings_claim_allowed": False,
    "primary216_authorized": False,
    "external_account_credit_additions": "must remain absent; not an account-wide spending lock",
}


def sources():
    value = driver.source_fingerprint()
    for path in sorted((driver.ROOT / "app").rglob("*.py")):
        value[str(path.relative_to(driver.ROOT))] = driver.dataset.sha(path.read_bytes())
    for name in (
        "scripts/evaluation_agent_value.py",
        "scripts/evaluation_operator.py",
        "scripts/evaluation_recipient.py",
        "tests/integration/test_agent_value_mcp.py",
        "tests/integration/test_evaluation_bootstrap.py",
        "examples/agent-handoff/smoke.py",
    ):
        value[name] = driver.dataset.sha(driver.dataset.read(driver.ROOT, name))
    return value


def approved(value):
    allowed = {
        "version",
        "kind",
        "client_date",
        "client_timezone",
        "approved_by",
        "approval_text",
        "amendment_sha256",
        "amendment_path",
        "scope",
        "model",
        "effort",
        "billing",
        "additional_cash_authorized",
        "purchased_credits_authorized",
        "api_billing_authorized",
        "primary216_authorized",
        "claim_approved",
    }
    if (
        not isinstance(value, dict)
        or bool(set(value) - allowed)
        or value.get("kind") != "actual chat-user prospective approval"
        or value.get("scope") != "development exact12 descriptive only"
        or value.get("model") != native.MODEL
        or value.get("effort") != "high"
        or value.get("billing") != "ChatGPT Plus"
        or value.get("additional_cash_authorized") != "0.00"
        or value.get("purchased_credits_authorized") is not False
        or value.get("api_billing_authorized") is not False
        or value.get("primary216_authorized") is not False
        or value.get("claim_approved") is not False
        or not re.fullmatch(driver.HEX, value.get("amendment_sha256", ""))
    ):
        raise FocusedError("EXACT_DESCRIPTIVE_ACCOUNTING_APPROVAL_REQUIRED")


def make_plan(bundle, approval, catalogs):
    """Pure construction: no service, credential read, model or provider call."""
    approved(approval)
    if (
        not isinstance(catalogs, dict)
        or set(catalogs) != set(driver.dataset.ARMS)
        or any(not isinstance(h, str) or not re.fullmatch(driver.HEX, h) for h in catalogs.values())
    ):
        raise FocusedError("ACTUAL_PER_ARM_CATALOG_HASHES_REQUIRED")
    if (
        not isinstance(bundle, dict)
        or bundle.get("kind") != "actual-owned-pilot-commission"
        or bundle.get("policy_variant") != "development-pilot-three-projects-v1"
        or set(bundle.get("snapshots", {})) != set(calibration.TASKS)
        or bundle.get("source_sha256") != driver.digest(sources())
        or bundle.get("protocols_verified") is not True
        or not re.fullmatch(r"[a-f0-9]{32}", bundle.get("instance", ""))
    ):
        raise FocusedError("CURRENT_COMPLETE_ACTUAL_COMMISSION_REQUIRED")
    seal = driver.read(driver.INPUT_SEAL)
    driver.sealing.verify(seal, driver.HIDDEN)
    contract = driver.read(driver.SOURCE / "adapter.json")
    if driver.dataset.sha(native.BINARY.read_bytes()) != contract["binary_sha256"]:
        raise FocusedError("UNREVIEWED_CLI_BINARY")
    attempts = []
    seed, ordered = calibration.schedule("calibration", list(calibration.TASKS), 1)
    for task, repeat, arm, position in ordered:
        snapshot = bundle["snapshots"][task]
        inputs = delivery.inputs(task, arm, snapshot)
        attempts.append(
            {
                "id": driver.digest([task, repeat, arm])[:32],
                "task_id": task,
                "arm": arm,
                "repeat": repeat,
                "parent_position": position,
                "snapshot_sha256": snapshot["sha256"] if arm != "repository" else None,
                "inputs": {n: driver.dataset.sha(b) for n, b in inputs.items()},
                "prompt_sha256": driver.dataset.sha(inputs["TASK.md"]),
                "tools_sha256": catalogs[arm],
            }
        )
    value = {
        "version": 1,
        "kind": "development-exact12-descriptive-Plus-pilot",
        "source_hashes": sources(),
        "approval_sha256": driver.digest(approval),
        "commission_sha256": driver.digest(bundle),
        "accounting": ACCOUNTING,
        "cli": {
            "path": str(native.BINARY),
            "sha256": contract["binary_sha256"],
            "version": native.VERSION,
        },
        "model": native.MODEL,
        "effort": "high",
        "service_tier": "default",
        "skills": driver.common_skills(),
        "input_seal_sha256": driver.dataset.sha(driver.INPUT_SEAL.read_bytes()),
        "seed": seed,
        "attempts": attempts,
        "workers": 1,
        "bounds": {
            "wall_seconds": 600,
            "cpu_seconds": 600,
            "requests": 32,
            "tools": 64,
            "events": 4096,
            "memory_bytes": 2147483648,
            "workspace_bytes": 536870912,
            "output_bytes": 16777216,
            "grader_seconds": 30,
            "cleanup_seconds": 15,
        },
        "runtime_graph": runtime.argv(
            native.BINARY,
            Path("<inputs>"),
            Path("<owned-control>"),
            driver.SOURCE / "skills",
            identity="0" * 32,
        ),
        "native_config": native.configuration(
            Path("/vendor/codex"), Path("/work"), Path("/cli-home")
        ),
        "conditional_agent_grader_cleanup_seconds": 7740,
        "task_exposure": "published author-reviewed prompts; independent review unavailable",
        "cache": "fresh home/process/workspace; natural provider cache; no warmup or order changes",
        "billed_spend": None,
        "human_active_minutes": None,
        "human_elapsed_minutes": None,
        "primary_execution_authorized": False,
        "claim_approved": False,
    }
    value["sha256"] = driver.digest(value)
    return value


def owned(run_id):
    driver.identity(run_id)
    folder = BASE / run_id
    native.private(folder, directory=True)
    for name in ("owner.json", "plan.json", "ledger.json", "approval.json", "commission.json"):
        native.private(folder / name)
    plan, ledger = driver.read(folder / "plan.json"), driver.read(folder / "ledger.json")
    if driver.read(folder / "owner.json") != {
        "uid": os.getuid(),
        "path": str(folder),
        "run_id": run_id,
        "plan_sha256": plan.get("sha256"),
    }:
        raise FocusedError("FOREIGN_PILOT_OWNERSHIP")
    if plan.get("sha256") != driver.digest({k: v for k, v in plan.items() if k != "sha256"}):
        raise FocusedError("PILOT_PLAN_INTEGRITY_FAILED")
    if ledger.get("plan_sha256") != plan["sha256"] or [
        r["id"] for r in ledger.get("attempts", [])
    ] != [r["id"] for r in plan["attempts"]]:
        raise FocusedError("ALL12_RECONCILIATION_FAILED")
    if any(
        r.get("state") not in {"scheduled", "launched", "completed", "failed", "unknown"}
        for r in ledger["attempts"]
    ):
        raise FocusedError("UNKNOWN_PILOT_LEDGER_STATE")
    return folder, plan, ledger


def create(bundle, approval, catalogs):
    plan = make_plan(bundle, approval, catalogs)
    verify_artifacts(bundle, catalogs)
    with driver.locked(BASE):
        # Exactly one vendor tranche per approved amendment, across cleanup.
        registry = BASE / "authorization-bindings.json"
        bindings = driver.read(registry) if registry.exists() else {}
        key = driver.digest(approval)
        if key in bindings:
            raise FocusedError("DESCRIPTIVE_APPROVAL_ALREADY_BOUND")
        run_id = secrets.token_hex(16)
        folder = BASE / run_id
        bindings[key] = {"run_id": run_id, "plan_sha256": plan["sha256"]}
        atomic(registry, bindings)  # bind before any runnable state can exist
        folder.mkdir(mode=0o700)
        atomic(
            folder / "owner.json",
            {
                "uid": os.getuid(),
                "path": str(folder),
                "run_id": run_id,
                "plan_sha256": plan["sha256"],
            },
        )
        for name, value in (("plan", plan), ("approval", approval), ("commission", bundle)):
            atomic(folder / (name + ".json"), value)
        atomic(
            folder / "ledger.json",
            {
                "plan_sha256": plan["sha256"],
                "attempts": [
                    {
                        "id": r["id"],
                        "state": "scheduled",
                        "success": None,
                        "billed_spend": None,
                        "cash_liability": "not-launched",
                        "usage_complete": False,
                    }
                    for r in plan["attempts"]
                ],
            },
        )
        atomic(
            folder / "authorization.json",
            {
                "version": 1,
                "approved_by": approval["approved_by"],
                "authority": "explicit chat-user instruction; independent claim approval absent",
                "approval_sha256": key,
                "plan_sha256": plan["sha256"],
                "source_sha256": driver.digest(sources()),
                "attempt_ids": [r["id"] for r in plan["attempts"]],
                "additional_USD_cap": "0.00",
                "expires_at": bundle["expires_at"],
            },
        )
    return {"run_id": run_id, "plan": plan}


def verify_artifacts(bundle, catalogs):
    """Only existing owned receipts are read; planning starts no services."""
    folder = COMMISSIONS / bundle["instance"]
    native.private(folder, directory=True)
    native.private(folder / "commission.json")
    if driver.read(folder / "commission.json") != bundle:
        raise FocusedError("ACTUAL_COMMISSION_ARTIFACT_REQUIRED")
    for arm, expected in catalogs.items():
        path = folder / ("catalog-" + arm) / "session.json"
        native.private(path)
        report = driver.read(path)
        session.gateway.verify_catalog(report.get("catalog"), mcp=arm == "mcp")
        if (
            report.get("kind") != "catalog-only-no-provider"
            or report.get("model_requests_forwarded") != 0
            or report.get("account_before") is None
            or report.get("account_after") is None
            or report.get("code") != "CATALOG_CAPTURED"
            or report.get("runtime_removed") is not True
            or report.get("catalog", {}).get("allowed") is not True
            or report["catalog"]["tools_sha256"] != expected
            or report.get("source_sha256") != driver.digest(driver.source_fingerprint())
        ):
            raise FocusedError("CURRENT_ACTUAL_NATIVE_CATALOG_REQUIRED")


def status(run_id):
    _, plan, ledger = owned(run_id)
    rows = ledger["attempts"]
    return {
        "run_id": run_id,
        "plan_sha256": plan["sha256"],
        "scheduled": 12,
        "launched": sum(r["state"] != "scheduled" for r in rows),
        "states": {
            s: sum(r["state"] == s for r in rows) for s in sorted({r["state"] for r in rows})
        },
        "successes": sum(r["success"] is True for r in rows),
        "unknown_liability": sum(r["cash_liability"] == "unknown" for r in rows),
        "actual_billed_spend": None,
        "token_or_quota_upper_bound": None,
        "final_primary_lock": False,
        "claim_approved": False,
    }


def check_current(folder, plan):
    bundle, approval = (
        driver.read(folder / "commission.json"),
        driver.read(folder / "approval.json"),
    )
    catalogs = {r["arm"]: r["tools_sha256"] for r in plan["attempts"]}
    if make_plan(bundle, approval, catalogs) != plan:
        raise FocusedError("STALE_PILOT_SOURCE_OR_INPUTS")
    receipt = driver.read(folder / "authorization.json")
    binding = driver.read(BASE / "authorization-bindings.json").get(driver.digest(approval))
    if binding != {"run_id": folder.name, "plan_sha256": plan["sha256"]}:
        raise FocusedError("PILOT_AUTHORIZATION_REPLAY_REFUSED")
    if receipt.get("plan_sha256") != plan["sha256"] or receipt.get(
        "approval_sha256"
    ) != driver.digest(approval):
        raise FocusedError("PILOT_AUTHORIZATION_MISMATCH")
    if datetime.fromisoformat(receipt["expires_at"]) <= datetime.now(UTC):
        raise FocusedError("PILOT_AUTHORIZATION_EXPIRED")
    return bundle


def run(run_id):
    from . import commission

    with driver.locked(BASE):
        folder, plan, ledger = owned(run_id)
        bundle = check_current(folder, plan)
        if any(r["state"] in {"unknown", "launched"} for r in ledger["attempts"]):
            raise FocusedError("RECONCILE_UNKNOWN_BEFORE_NEW_LAUNCH")
        if ledger.get("halted_attempt"):
            raise FocusedError("RECONCILE_PILOT_FAILURE_BEFORE_NEW_LAUNCH")
        for row, attempt in zip(ledger["attempts"], plan["attempts"], strict=True):
            if row["state"] != "scheduled":
                continue
            check_current(folder, plan)
            commission.verify_live(bundle, attempt["task_id"])
            session.preflight.prepare(with_benchmark_login=True)
            target = folder / row["id"]
            target.mkdir(mode=0o700)
            inputs = delivery.inputs(
                attempt["task_id"], attempt["arm"], bundle["snapshots"][attempt["task_id"]]
            )
            if {n: driver.dataset.sha(b) for n, b in inputs.items()} != attempt["inputs"]:
                raise FocusedError("PILOT_INPUT_HASH_MISMATCH")
            row.update(state="launched", cash_liability="unknown")
            atomic(folder / "ledger.json", ledger)  # before every possible provider side effect
            try:
                settings = commission.connection(bundle, attempt["task_id"], attempt["arm"])
                result = session.execute(
                    inputs, target, tools_sha256=attempt["tools_sha256"], **settings
                )
                grade = delivery.grade(
                    attempt["task_id"],
                    attempt["arm"],
                    target / "submission",
                    bundle["snapshots"][attempt["task_id"]],
                    driver.HIDDEN,
                    driver.read(driver.INPUT_SEAL),
                )
                atomic(target / "grade.json", grade)
                settled = result["cash_liability"] == "zero-credit-state-rechecked"
                completed = result["code"] == "COMPLETED" and not result["routing_mismatch"]
                row.update(
                    state="completed"
                    if completed and settled
                    else "failed"
                    if settled
                    else "unknown",
                    cash_liability=result["cash_liability"],
                    success=bool(grade["success"] and completed and settled),
                    usage_complete=result["numeric_receipts"]["coverage"]
                    == "complete-observed-route",
                    model_requests_forwarded=result["model_requests_forwarded"],
                    session_sha256=driver.dataset.sha((target / "session.json").read_bytes()),
                    grade_sha256=driver.dataset.sha((target / "grade.json").read_bytes()),
                )
                if not completed or not settled:
                    ledger["halted_attempt"] = row["id"]
                atomic(folder / "ledger.json", ledger)
                if not completed or not settled:
                    break
            except BaseException:
                row.update(state="unknown", cash_liability="unknown", success=False)
                atomic(folder / "ledger.json", ledger)
                raise
        finalize(folder, plan, ledger)
    return status(run_id)


def finalize(folder, plan, ledger):
    """Freeze descriptive outcomes only; absent results remain explicit failures."""
    from . import analysis

    rows = ledger["attempts"]
    terminal = all(r["state"] in {"completed", "failed"} for r in rows)
    if not terminal:
        return
    sessions = [driver.read(folder / r["id"] / "session.json") for r in rows]
    vendor_observed = all(
        s.get("kind") == "native-Plus-attempt" and (s.get("model_requests_forwarded") or 0) > 0
        for s in sessions
    )
    families = {t: [0.0, 0.0, 0.0] for t in calibration.TASKS}
    for row, attempt in zip(rows, plan["attempts"], strict=True):
        families[attempt["task_id"]][driver.dataset.ARMS.index(attempt["arm"])] = float(
            row["success"] is True
        )
    atomic(
        folder / "descriptive-result.json",
        {
            "kind": "descriptive development pilot only",
            "plan_sha256": plan["sha256"],
            "scheduled": 12,
            "terminal": 12,
            "vendor_session_coverage_complete": vendor_observed,
            "observed_vendor_requests": [s.get("model_requests_forwarded") for s in sessions],
            "mcp_used": [s["metrics"]["mcp_used"] for s in sessions],
            "coding": analysis.paired_bootstrap(families),
            "billed_spend": None,
            "cost_or_token_savings_claim": False,
            "native_usage_inclusion_and_failed_request_billing_proven": False,
            "descriptive_lock": vendor_observed,
            "primary_execution_lock": False,
            "primary216_authorized": False,
            "source_sha256": driver.digest(plan["source_hashes"]),
            "claim_approved": False,
        },
    )


def resume(run_id, receipt):
    """Only reconcile retained terminal transport + actual zero-credit recheck.

    Does not accept invented cash values, erase an attempt or automatically retry.
    Missing transport/account evidence remains unresolved even with a reviewer name.
    """
    with driver.locked(BASE):
        folder, plan, ledger = owned(run_id)
        check_current(folder, plan)
        unknown = [r for r in ledger["attempts"] if r["state"] in {"launched", "unknown"}]
        required = [r["id"] for r in unknown]
        halted = ledger.get("halted_attempt")
        if halted and halted not in required:
            required.append(halted)
        if (
            not isinstance(receipt, dict)
            or set(receipt) != {"plan_sha256", "reviewer", "attempt_ids"}
            or receipt["plan_sha256"] != plan["sha256"]
            or not isinstance(receipt["reviewer"], str)
            or not re.fullmatch(driver.LABEL, receipt["reviewer"])
            or receipt["attempt_ids"] != required
        ):
            raise FocusedError("COMPLETE_PILOT_RECONCILIATION_REQUIRED")
        late_recheck = None
        for row in ledger["attempts"]:
            if row["id"] not in required:
                continue
            path = folder / row["id"] / "session.json"
            native.private(path)
            result = driver.read(path)
            if not result.get("runtime_removed") or any(
                not r["terminal"] or not r["http_status"]
                for r in result["numeric_receipts"]["requests"]
            ):
                raise FocusedError("UNKNOWN_PROVIDER_LIABILITY_REMAINS_RESERVED")
            if result.get("routing_mismatch"):
                raise FocusedError("ROUTING_CHANGE_REQUIRES_NEW_REVIEWED_PLAN")
            if result.get("account_after") is None:
                if late_recheck is None:
                    late_recheck = account_recheck(folder)
                row["late_account_recheck"] = late_recheck
            elif result.get("cash_liability") != "zero-credit-state-rechecked":
                raise FocusedError("UNKNOWN_PROVIDER_LIABILITY_REMAINS_RESERVED")
        for row in unknown:
            row.update(
                state="failed",
                success=False,
                cash_liability="zero-credit-state-rechecked",
                reconciliation_sha256=driver.digest(receipt),
            )
        if halted:
            ledger.pop("halted_attempt")
            ledger["failure_reconciliation_sha256"] = driver.digest(receipt)
        atomic(folder / "ledger.json", ledger)
        finalize(folder, plan, ledger)
    return status(run_id)


def account_recheck(folder):
    """An explicit resume may obtain a late model-free credit receipt.

    The original session's missing account-after measurement stays missing.
    This never settles an unterminated wire request or retries a vendor attempt.
    """
    result = session.preflight.run(with_benchmark_login=True)
    path = Path(result["evidence"]) / "result.json"
    native.private(path)
    value = driver.read(path)
    if (
        value.get("kind") != "actual-installed-cli-model-free-preflight"
        or value.get("status") != "model-free-preflight-passed"
        or value.get("source_sha256") != driver.digest(driver.source_fingerprint())
        or value.get("account") is None
        or value.get("model_requests_allowed") is not False
        or value.get("provider_agent_attempts") != 0
        or value.get("numeric_receipts", {}).get("requests") != []
        or value.get("runtime_removed") is not True
    ):
        raise FocusedError("ACTUAL_MODEL_FREE_CASH_RECHECK_REQUIRED")
    receipt = {"evidence": str(path), "sha256": driver.dataset.sha(path.read_bytes())}
    atomic(folder / ("account-recheck-" + path.parent.name + ".json"), receipt)
    return receipt


def cleanup(run_id):
    from . import commission

    with driver.locked(BASE):
        folder, _, ledger = owned(run_id)
        if any(r["state"] in {"launched", "unknown"} for r in ledger["attempts"]):
            raise FocusedError("UNRESOLVED_PILOT_LIABILITY_RETAIN_RESOURCES")
        result = commission.cleanup(driver.read(folder / "commission.json"))
        atomic(folder / "cleanup.json", result)
    return {"run_id": run_id, "evidence_retained": True, **result}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    plan = commands.add_parser("plan")
    plan.add_argument("--commission", type=Path, required=True)
    plan.add_argument("--approval", type=Path, required=True)
    plan.add_argument("--catalogs", type=Path, required=True)
    for name in ("run", "status", "resume", "owned-cleanup"):
        item = commands.add_parser(name)
        item.add_argument("--run-id", required=True)
        if name == "resume":
            item.add_argument("--reconciliation", type=Path, required=True)
    args = parser.parse_args()
    os.umask(0o077)

    def interrupted(*_):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, interrupted)
    try:
        if args.command == "plan":
            result = create(
                driver.read(args.commission), driver.read(args.approval), driver.read(args.catalogs)
            )
        elif args.command == "run":
            result = run(args.run_id)
        elif args.command == "status":
            result = status(args.run_id)
        elif args.command == "resume":
            result = resume(args.run_id, driver.read(args.reconciliation))
        else:
            result = cleanup(args.run_id)
        print(json.dumps(result, indent=2))
        return 0
    except (FocusedError, OSError, ValueError, KeyError, TypeError, KeyboardInterrupt):
        print(
            json.dumps(
                {"status": "refused", "code": "PILOT_PREREQUISITE_OR_RECONCILIATION_REQUIRED"}
            )
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
