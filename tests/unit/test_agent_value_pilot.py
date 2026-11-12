"""Synthetic pilot control checks. No CLI, provider, credentials or live platform."""

import importlib
import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from scripts.evaluation_lifecycle import atomic
from scripts.evaluation_operator import agent_value_policy
from scripts.focused_process import FocusedError

pilot = importlib.import_module("benchmarks.agent-value-runner.pilot")
driver = pilot.driver


def synthetic_inputs(tmp_path, monkeypatch):
    """API-shaped authored data is compiler input, never live provenance evidence."""
    from scripts import evaluation_agent_value as helper

    binary = tmp_path / "synthetic-cli"
    binary.write_bytes(b"synthetic, not executable")
    monkeypatch.setattr(pilot.native, "BINARY", binary)
    seal = tmp_path / "seal.json"
    atomic(seal, {})
    monkeypatch.setattr(driver, "INPUT_SEAL", seal)
    monkeypatch.setattr(driver.sealing, "verify", lambda *args: None)
    original = driver.read

    def read(path):
        value = original(path)
        if path == driver.SOURCE / "adapter.json":
            value["binary_sha256"] = driver.dataset.sha(binary.read_bytes())
        return value

    monkeypatch.setattr(driver, "read", read)
    approval = {
        "kind": "actual chat-user prospective approval",
        "approved_by": "synthetic-unit-owner",
        "scope": "development exact12 descriptive only",
        "model": "gpt-6.1-sol",
        "effort": "high",
        "billing": "ChatGPT Plus",
        "additional_cash_authorized": "0.00",
        "purchased_credits_authorized": False,
        "api_billing_authorized": False,
        "primary216_authorized": False,
        "claim_approved": False,
        "amendment_sha256": "a" * 64,
    }
    snapshots = {}
    for task in pilot.calibration.TASKS:
        project = helper.OTHER if task.startswith("harbor-") else helper.PILOT_OTHER
        rows = [
            {
                **r,
                "context_id": f"{i + 1:024x}",
                "revision": 1,
                "updated_at": "2026-11-12T00:00:00Z",
            }
            for i, r in enumerate(pilot.delivery.import_requests(task, project))
        ]
        snapshots[task] = pilot.delivery.freeze(task, project, rows)
    bundle = {
        "kind": "actual-owned-pilot-commission",
        "policy_variant": "development-pilot-three-projects-v1",
        "snapshots": snapshots,
        "source_sha256": driver.digest(pilot.sources()),
        "protocols_verified": True,
        "instance": "c" * 32,
        "expires_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
        "activation": str(tmp_path / "synthetic-activation"),
        "activation_hashes": {},
    }
    return bundle, approval, {a: "d" * 64 for a in driver.dataset.ARMS}


def test_plus_pilot_plan_is_exact_zero_cash_source_bound_and_model_free(tmp_path, monkeypatch):
    bundle, approval, catalogs = synthetic_inputs(tmp_path, monkeypatch)
    monkeypatch.setattr(
        pilot.session, "execute", lambda *a, **k: pytest.fail("plan launched a child")
    )
    plan = pilot.make_plan(bundle, approval, catalogs)
    assert len(plan["attempts"]) == 12 and plan["workers"] == 1
    assert [r["parent_position"] for r in plan["attempts"]] == [
        7,
        8,
        9,
        31,
        32,
        33,
        43,
        44,
        45,
        46,
        47,
        48,
    ]
    assert plan["accounting"]["additional_cash_cap"] == "0.00"
    assert plan["accounting"]["token_or_quota_upper_bound"] is None
    assert not plan["primary_execution_authorized"] and not plan["claim_approved"]
    assert "--unshare-all" in plan["runtime_graph"]
    assert "HARD_REQUEST_TOKEN_AND_SPEND_ENFORCEMENT_UNPROVEN" in driver.BLOCKERS
    for change in (
        {"model": "other"},
        {"additional_cash_authorized": "1.00"},
        {"purchased_credits_authorized": True},
        {"primary216_authorized": True},
    ):
        with pytest.raises(FocusedError):
            pilot.make_plan(bundle, {**approval, **change}, catalogs)
    for change in ({"source_sha256": "0" * 64}, {"protocols_verified": False}, {"snapshots": {}}):
        with pytest.raises(FocusedError):
            pilot.make_plan({**bundle, **change}, approval, catalogs)
    with pytest.raises(FocusedError):
        pilot.make_plan(bundle, approval, {**catalogs, "extra": "e" * 64})


def test_plus_pilot_unknown_attempt_is_never_retried_or_zero_filled(tmp_path, monkeypatch):
    commission = importlib.import_module("benchmarks.agent-value-runner.commission")
    bundle, approval, catalogs = synthetic_inputs(tmp_path, monkeypatch)
    monkeypatch.setattr(pilot, "BASE", tmp_path / "pilots")
    monkeypatch.setattr(pilot, "verify_artifacts", lambda *args: None)
    monkeypatch.setattr(pilot.session.preflight, "prepare", lambda **kwargs: None)
    record = pilot.create(bundle, approval, catalogs)
    run_id = record["run_id"]
    with pytest.raises(FocusedError, match="ALREADY_BOUND"):
        pilot.create(bundle, approval, catalogs)
    monkeypatch.setattr(commission, "verify_live", lambda *args: None)
    monkeypatch.setattr(commission, "connection", lambda *args: {})
    calls = []

    def interrupted(*args, **kwargs):
        calls.append(True)
        raise KeyboardInterrupt

    monkeypatch.setattr(pilot.session, "execute", interrupted)
    with pytest.raises(KeyboardInterrupt):
        pilot.run(run_id)
    state = pilot.status(run_id)
    assert state["unknown_liability"] == 1 and state["states"]["scheduled"] == 11
    assert state["actual_billed_spend"] is None
    with pytest.raises(FocusedError, match="RECONCILE_UNKNOWN"):
        pilot.run(run_id)
    with pytest.raises(FocusedError, match="UNRESOLVED"):
        pilot.cleanup(run_id)
    assert len(calls) == 1
    with pytest.raises(FocusedError):
        pilot.resume(
            run_id,
            {
                "plan_sha256": record["plan"]["sha256"],
                "reviewer": "unit-reviewer",
                "attempt_ids": [],
            },
        )
    assert pilot.status(run_id)["unknown_liability"] == 1
    # A source revision cannot erase an interrupted row or obtain another tranche.
    with pytest.raises(FocusedError, match="SETTLED_UNFINISHED"):
        pilot.rebind(run_id, bundle, catalogs)
    folder, _, ledger = pilot.owned(run_id)
    first_id = ledger["attempts"][0]["id"]
    atomic(
        folder / first_id / "session.json",
        {
            "kind": "native-Plus-attempt",
            "runtime_removed": True,
            "account_after": {"synthetic": True},
            "cash_liability": "zero-credit-state-rechecked",
            "numeric_receipts": {"requests": [{"terminal": True, "http_status": 200}]},
        },
    )
    pilot.resume(
        run_id,
        {
            "plan_sha256": record["plan"]["sha256"],
            "reviewer": "synthetic-reviewer",
            "attempt_ids": [first_id],
        },
    )
    original_sources = pilot.sources
    changed_sources = original_sources()
    for i, name in enumerate(sorted(pilot.ADAPTER_REVISION_FILES)):
        changed_sources[name] = str(i) * 64
    monkeypatch.setattr(pilot, "sources", lambda: changed_sources)
    monkeypatch.setattr(commission, "activation_evidence", lambda path: (Path(path), {}))
    revised_bundle = {
        **bundle,
        "source_sha256": driver.digest(changed_sources),
        "adapter_revision": {
            "name": pilot.ADAPTER_REVISION,
            "previous_commission_sha256": driver.digest(bundle),
        },
    }
    with pytest.raises(FocusedError, match="SCOPE_WIDENED"):
        pilot.rebind(
            run_id,
            {**revised_bundle, "expires_at": (datetime.now(UTC) + timedelta(hours=2)).isoformat()},
            catalogs,
        )
    before = driver.read(folder / first_id / "session.json")
    state = pilot.rebind(run_id, revised_bundle, catalogs)
    assert state["launched"] == 1 and state["states"] == {"failed": 1, "scheduled": 11}
    _, revised, new_ledger = pilot.owned(run_id)
    assert [r["id"] for r in revised["attempts"]] == [r["id"] for r in record["plan"]["attempts"]]
    assert new_ledger["attempts"][0]["plan_sha256_at_launch"] == record["plan"]["sha256"]
    assert driver.read(folder / first_id / "session.json") == before
    assert len(calls) == 1
    with pytest.raises(FocusedError, match="SETTLED_UNFINISHED"):
        pilot.rebind(run_id, revised_bundle, catalogs)
    with pytest.raises(FocusedError):
        pilot.adapter_delta(changed_sources, {**changed_sources, "foreign.py": "a" * 64})
    monkeypatch.setattr(pilot, "sources", original_sources)
    # A separate synthetic authority exercises terminal all12 reconciliation;
    # none of these authored receipt objects is vendor-success evidence.
    monkeypatch.setattr(pilot, "BASE", tmp_path / "completed-pilots")
    complete = pilot.create(bundle, {**approval, "approved_by": "synthetic-second-owner"}, catalogs)

    def completed(inputs, destination, **kwargs):
        (destination / "submission").mkdir()
        value = {
            "kind": "native-Plus-attempt",
            "code": "COMPLETED",
            "routing_mismatch": False,
            "cash_liability": "zero-credit-state-rechecked",
            "model_requests_forwarded": 1,
            "metrics": {"mcp_used": False},
            "numeric_receipts": {"coverage": "incomplete"},
        }
        atomic(destination / "session.json", value)
        return value

    monkeypatch.setattr(pilot.session, "execute", completed)
    monkeypatch.setattr(pilot.delivery, "grade", lambda *args: {"success": False})
    outcome = pilot.run(complete["run_id"])
    assert outcome["states"] == {"completed": 12} and outcome["successes"] == 0
    assert outcome["actual_billed_spend"] is None
    assert pilot.run(complete["run_id"])["launched"] == 12
    monkeypatch.setattr(pilot, "BASE", tmp_path / "halted-pilots")
    halted = pilot.create(bundle, {**approval, "approved_by": "synthetic-third-owner"}, catalogs)

    def failed(inputs, destination, **kwargs):
        value = completed(inputs, destination, **kwargs)
        value.update(
            code="NATIVE_TURN_FAILED",
            account_after={"synthetic": True},
            runtime_removed=True,
            numeric_receipts={
                "coverage": "incomplete",
                "requests": [{"terminal": True, "http_status": 500}],
            },
        )
        atomic(destination / "session.json", value)
        return value

    monkeypatch.setattr(pilot.session, "execute", failed)
    state = pilot.run(halted["run_id"])
    assert state["states"] == {"failed": 1, "scheduled": 11}
    with pytest.raises(FocusedError, match="RECONCILE_PILOT_FAILURE"):
        pilot.run(halted["run_id"])
    receipt = {
        "plan_sha256": halted["plan"]["sha256"],
        "reviewer": "synthetic-reviewer",
        "attempt_ids": [halted["plan"]["attempts"][0]["id"]],
    }
    pilot.resume(halted["run_id"], receipt)
    monkeypatch.setattr(pilot.session, "execute", completed)
    state = pilot.run(halted["run_id"])
    assert state["launched"] == 12 and state["states"] == {"failed": 1, "completed": 11}


def test_native_session_redacts_tools_and_refuses_foreign_cleanup(tmp_path, monkeypatch):
    session = pilot.session
    observations = session.Observations(True)
    secret = "synthetic-reasoning-and-credential-shaped-text"
    item = {
        "type": "mcpToolCall",
        "id": "unit-call",
        "server": "agent_context_platform",
        "tool": "search_knowledge",
        "arguments": {"sources": ["project_context"], "query": secret},
        "status": "inProgress",
    }
    observations.consume({"method": "item/started", "params": {"item": item}})
    observations.consume(
        {
            "method": "item/completed",
            "params": {"item": {**item, "status": "completed", "result": {"content": secret}}},
        }
    )
    assert observations.report()["mcp_used"]
    assert secret not in json.dumps(observations.report())
    discovery = {
        "type": "mcpToolCall",
        "id": "ordinary-discovery",
        "server": "codex",
        "tool": "list_mcp_resources",
        "arguments": {},
        "status": "inProgress",
    }
    ordinary = session.Observations(False)
    ordinary.consume({"method": "item/started", "params": {"item": discovery}})
    assert not ordinary.report()["mcp_used"]
    assert ordinary.report()["tools"][0]["tool"] == "list_mcp_resources"
    with pytest.raises(FocusedError, match="UNDECLARED_NATIVE_MCP"):
        session.Observations(False).consume(
            {
                "method": "item/started",
                "params": {"item": {**discovery, "tool": "search_knowledge"}},
            }
        )
    output = {
        "type": "functionCallOutput",
        "id": "ordinary-output",
        "namespace": "functions",
        "name": "exec",
        "output": secret,
    }
    observations.consume({"method": "item/completed", "params": {"item": output}})
    assert secret not in json.dumps(observations.report())
    with pytest.raises(FocusedError, match="UNDECLARED_NATIVE_FUNCTION"):
        observations.consume(
            {"method": "item/completed", "params": {"item": {**output, "namespace": "foreign"}}}
        )
    bounded = session.Observations(False)
    for i in range(64):
        bounded.consume(
            {"method": "item/started", "params": {"item": {"id": str(i), "type": "fileChange"}}}
        )
    with pytest.raises(FocusedError, match="TOOL_COUNT"):
        bounded.consume(
            {"method": "item/started", "params": {"item": {"id": "65", "type": "fileChange"}}}
        )
    with pytest.raises(FocusedError, match="SOURCE_REQUIRED"):
        session.Observations(True).consume(
            {
                "method": "item/started",
                "params": {"item": {**item, "arguments": {"sources": ["memory"]}}},
            }
        )
    root = tmp_path / ("acp-v8-native-" + "a" * 32)
    root.mkdir(mode=0o700)
    owner = {"uid": os.getuid(), "identity": "a" * 32, "path": str(root)}
    atomic(root / "owner.json", owner)
    (root / "inputs").mkdir(mode=0o700)
    (root / "inputs/api.py").write_text("synthetic")
    (root / "foreign").mkdir()
    with pytest.raises(FocusedError):
        session.cleanup_runtime(root, owner, {"api.py"})
    assert (root / "inputs/api.py").read_text() == "synthetic" and (root / "foreign").is_dir()
    (root / "foreign").rmdir()
    session.cleanup_runtime(root, owner, {"api.py"})
    assert not root.exists()
    gateway = importlib.import_module("benchmarks.agent-value-runner.gateway")
    body = {
        "client_metadata": {},
        "include": [],
        "input": [
            {
                "type": "additional_tools",
                "tools": [
                    {
                        "type": "namespace",
                        "name": "functions",
                        "tools": [{"type": "function", "name": "exec_command"}],
                    }
                ],
            }
        ],
        "model": "gpt-6.1-sol",
        "parallel_tool_calls": False,
        "prompt_cache_key": "synthetic",
        "reasoning": {"effort": "high", "context": "all_turns"},
        "store": False,
        "stream": True,
        "text": {},
        "tool_choice": "auto",
    }
    policy = gateway.Policy(capture_catalog=True)
    with pytest.raises(FocusedError, match="CATALOG_CAPTURED"):
        policy.request(
            "chatgpt.com", "POST", "/backend-api/codex/responses", json.dumps(body).encode()
        )
    assert policy.catalog["allowed"] and policy.receipts.rows == []
    ordinary_names = ["functions." + n for n in gateway.ORDINARY]
    complete_catalog = {"allowed": True, "tool_names": ordinary_names}
    gateway.verify_catalog(complete_catalog, mcp=False)
    with pytest.raises(FocusedError, match="EXACT_NATIVE_CATALOG"):
        gateway.verify_catalog(complete_catalog, mcp=True)
    mcp_names = ["mcp__agent_context_platform." + n for n in session.native.TOOLS]
    gateway.verify_catalog({**complete_catalog, "tool_names": ordinary_names + mcp_names}, mcp=True)
    with pytest.raises(FocusedError, match="EXACT_NATIVE_CATALOG"):
        gateway.verify_catalog(
            {**complete_catalog, "tool_names": [*ordinary_names, "foreign.tool"]}, mcp=False
        )
    server = {
        "name": "agent_context_platform",
        "runtimeStatus": "connected",
        "authStatus": "bearerToken",
        "tools": dict.fromkeys(session.native.TOOLS),
    }
    session.require_mcp({"data": [server]})
    for change in ({"runtimeStatus": "failed"}, {"authStatus": "unknown"}, {"tools": {}}):
        with pytest.raises(FocusedError, match="EXACT_CONNECTED_NATIVE_MCP"):
            session.require_mcp({"data": [{**server, **change}]})
    recheck_path = tmp_path / "synthetic-account-recheck" / "result.json"
    recheck_path.parent.mkdir(mode=0o700)
    value = {
        "kind": "actual-installed-cli-model-free-preflight",
        "status": "model-free-preflight-passed",
        "source_sha256": driver.digest(driver.source_fingerprint()),
        "account": {"synthetic": True},
        "model_requests_allowed": False,
        "provider_agent_attempts": 0,
        "numeric_receipts": {"requests": []},
        "runtime_removed": True,
    }
    atomic(recheck_path, value)
    monkeypatch.setattr(
        session.preflight, "run", lambda **kwargs: {"evidence": str(recheck_path.parent)}
    )
    assert pilot.account_recheck(tmp_path)["sha256"] == driver.dataset.sha(
        recheck_path.read_bytes()
    )
    atomic(recheck_path, {**value, "provider_agent_attempts": 1})
    with pytest.raises(FocusedError, match="MODEL_FREE_CASH_RECHECK"):
        pilot.account_recheck(tmp_path)
    assert session.receipt_summary(policy, capture=True)["model_requests_forwarded"] == 0
    wire = gateway.Policy(model_requests=True, tools_sha256="a" * 64, descriptive=True)
    number = wire.receipts.start(
        prompt_sha256="b" * 64, tools_sha256="a" * 64, effort="high", tier="default"
    )
    summary = session.receipt_summary(wire, capture=False)
    assert summary["model_requests_started"] == 1
    assert summary["model_requests_forwarded"] is None and not summary["transport_settled"]
    wire.receipts.finish(number, 0, None)
    assert session.receipt_summary(wire, capture=False)["model_requests_forwarded"] is None
    wire.receipts.rows[0].update(http_status=200, resolved_model="foreign-model")
    summary = session.receipt_summary(wire, capture=False)
    assert summary["model_requests_forwarded"] == 1 and summary["routing_mismatch"]
    # Missing provider identity must never hide an explicit foreign model/tier.
    for response in ({"model": "foreign-model"}, {"service_tier": "priority"}):
        wire = gateway.Policy(model_requests=True, tools_sha256="a" * 64, descriptive=True)
        number = wire.receipts.start(
            prompt_sha256="b" * 64, tools_sha256="a" * 64, effort="high", tier="default"
        )
        wire.finish(number, 200, response)
        summary = session.receipt_summary(wire, capture=False)
        assert summary["routing_mismatch"] and summary["receipt_failure"]


def test_pilot_policy_variant_is_explicit_and_credentials_have_only_one_project():
    from scripts import evaluation_agent_value as helper
    from tests.unit.test_agent_value_commission import reviewed

    commission = importlib.import_module("benchmarks.agent-value-runner.commission")
    intent, state, review, policy = reviewed()
    policy["ceiling"] = helper.domain(review["org_id"], pilot=True) + helper.own(
        review["worker_id"]
    )
    review.update(
        version=2,
        policy_variant="development-pilot-three-projects-v1",
        projects=[helper.PROJECT, helper.OTHER, helper.PILOT_OTHER],
        policy_sha256=driver.dataset.sha(helper.canonical(policy)),
    )
    assert len(agent_value_policy(intent, state, review, policy).ceiling) == 12
    for replacement in (
        {"version": 1},
        {"policy_variant": "unknown"},
        {"projects": review["projects"] + ["foreign"]},
    ):
        with pytest.raises(ValueError):
            agent_value_policy(intent, state, {**review, **replacement}, policy)
    for project in commission.PROJECTS.values():
        rows = commission.readonly(review["org_id"], project)
        assert len(rows) == 4 and all(r["selector"] == "include" for r in rows)
        assert {r["action"] for r in rows} == {
            "agent:self",
            "projects:read",
            "context:read",
            "search:read",
        }
        assert all(r["ids"] == [project] for r in rows if r["action"] != "agent:self")
