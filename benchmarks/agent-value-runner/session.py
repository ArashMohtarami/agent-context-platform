"""One bounded native Plus session. No raw protocol/reasoning text is retained."""

from __future__ import annotations

import json
import os
import secrets
import shutil
import subprocess
import time
from pathlib import Path

from scripts.evaluation_lifecycle import atomic
from scripts.focused_process import FocusedError

from . import driver, gateway, native, preflight, runtime


class Observations:
    """Allowlisted tool metrics; protocol values and command text stay in memory."""

    def __init__(self, mcp):
        self.mcp = mcp
        self.tools = {}
        self.commands = 0
        self.read_search_actions = 0
        self.turn_status = None

    def consume(self, event):
        method, params = event.get("method"), event.get("params", {})
        if not isinstance(params, dict):
            raise FocusedError("INVALID_NATIVE_NOTIFICATION")
        if method == "turn/completed":
            value = params.get("turn", {}).get("status")
            self.turn_status = (
                value if value in {"completed", "failed", "interrupted"} else "unknown"
            )
        if method not in {"item/started", "item/completed"}:
            return
        item = params.get("item", {})
        kind = item.get("type")
        if kind not in {
            "userMessage",
            "agentMessage",
            "reasoning",
            "plan",
            "fileChange",
            "commandExecution",
            "mcpToolCall",
            "functionCallOutput",
        }:
            raise FocusedError("UNDECLARED_NATIVE_ITEM")
        if kind not in {"commandExecution", "mcpToolCall", "fileChange", "functionCallOutput"}:
            return
        identifier = item.get("id")
        if not isinstance(identifier, str) or len(identifier) > 256:
            raise FocusedError("INVALID_NATIVE_TOOL_ID")
        key = driver.digest(identifier)
        if kind == "functionCallOutput":
            namespace, tool = item.get("namespace") or "functions", item.get("name")
            if namespace == "functions" and isinstance(tool, str) and tool.startswith("functions."):
                tool = tool.removeprefix("functions.")
            if not (
                (namespace == "functions" and tool in gateway.ORDINARY)
                or (
                    self.mcp and namespace == "mcp__agent_context_platform" and tool in native.TOOLS
                )
            ):
                raise FocusedError("UNDECLARED_NATIVE_FUNCTION_OUTPUT")
            if key not in self.tools:
                if len(self.tools) >= 64:
                    raise FocusedError("NATIVE_TOOL_COUNT_OR_ID_BOUND")
                self.tools[key] = {"tool": tool, "status": "started", "returned_bytes": None}
            if method == "item/completed":
                self.tools[key]["status"] = "completed"
                self.tools[key]["returned_bytes"] = len(json.dumps(item.get("output")).encode())
            return
        if method == "item/started":
            if key in self.tools or len(self.tools) >= 64:
                raise FocusedError("NATIVE_TOOL_COUNT_OR_ID_BOUND")
            tool = "commandExecution"
            if kind == "mcpToolCall":
                tool = item.get("tool")
                if (
                    not self.mcp
                    or item.get("server") != "agent_context_platform"
                    or tool not in native.TOOLS
                ):
                    raise FocusedError("UNDECLARED_NATIVE_MCP_TOOL")
                arguments = item.get("arguments", {})
                if tool == "search_knowledge" and arguments.get("sources") != ["project_context"]:
                    raise FocusedError("EXPLICIT_PROJECT_CONTEXT_SOURCE_REQUIRED")
            elif kind == "fileChange":
                tool = "apply_patch"
            else:
                self.commands += 1
                self.read_search_actions += sum(
                    a.get("type") in {"read", "search", "listFiles"}
                    for a in item.get("commandActions", [])
                    if isinstance(a, dict)
                )
            self.tools[key] = {"tool": tool, "status": "started", "returned_bytes": None}
        elif key in self.tools:
            status = item.get("status")
            self.tools[key]["status"] = (
                status if status in {"completed", "failed", "declined", "inProgress"} else "unknown"
            )
            result = (
                item.get("result")
                if kind == "mcpToolCall"
                else item.get("changes")
                if kind == "fileChange"
                else item.get("aggregatedOutput")
            )
            if result is not None:
                self.tools[key]["returned_bytes"] = len(json.dumps(result).encode())

    def report(self):
        return {
            "tools": list(self.tools.values()),
            "ordinary_commands": self.commands,
            "ordinary_read_search_actions": self.read_search_actions,
            "returned_bytes_basis": "serialized native tool result; not billed tokens",
            "mcp_used": any(r["tool"] in native.TOOLS for r in self.tools.values()),
            "turn_status": self.turn_status,
        }


def rpc(connection, observations, method, params):
    if method not in native.PREFLIGHT_METHODS | {"thread/start", "turn/start"}:
        raise FocusedError("UNDECLARED_PILOT_CONTROL_METHOD")
    connection.counter += 1
    number = connection.counter
    connection.send({"id": number, "method": method, "params": params})
    while True:
        event = connection.next()
        if event.get("id") == number and "method" not in event:
            if "error" in event or "result" not in event:
                raise FocusedError("NATIVE_PILOT_CONTROL_REFUSED")
            return event["result"]
        observe(connection, observations, event)


def observe(connection, observations, event):
    if "method" in event and "id" in event:
        connection.send({"id": event["id"], "error": {"code": -32601, "message": "denied"}})
        raise FocusedError("UNEXPECTED_NATIVE_APPROVAL_OR_TOOL_REQUEST")
    observations.consume(event)


def account(connection, observations):
    return native.account_gate(
        rpc(connection, observations, "account/read", {"refreshToken": False}),
        rpc(connection, observations, "account/rateLimits/read", {}),
    )


def receipt_summary(policy, *, capture):
    """A reserved wire request is not proof that it reached the provider."""
    rows = policy.receipts.rows
    settled = all(r["terminal"] and r["http_status"] for r in rows)
    return {
        "numeric_receipts": policy.receipts.report(),
        "model_requests_started": len(rows),
        "model_requests_forwarded": 0 if capture else len(rows) if settled else None,
        "routing_mismatch": "ACTUAL_ROUTING_CHANGED" in policy.receipts.flags
        or any(
            r["resolved_model"] not in {None, native.MODEL}
            or r["resolved_tier"] not in {None, "default"}
            for r in rows
        ),
        "receipt_failure": bool(
            policy.receipts.flags
            - {
                "MISSING_PROVIDER_RESPONSE_ID",
                "MODEL_RESOLUTION_CHANGED_OR_UNKNOWN",
                "SERVICE_TIER_CHANGED_OR_UNKNOWN",
                "RESPONSE_USAGE_UNKNOWN",
            }
        ),
        "transport_settled": settled,
    }


def require_mcp(status):
    rows = status.get("data", [])
    if (
        status.get("nextCursor") is not None
        or len(rows) != 1
        or rows[0].get("name") != "agent_context_platform"
        or rows[0].get("runtimeStatus") != "connected"
        or rows[0].get("authStatus") != "bearerToken"
        or rows[0].get("toolsError") is not None
        or set(rows[0].get("tools", {})) != set(native.TOOLS)
    ):
        raise FocusedError("EXACT_CONNECTED_NATIVE_MCP_REQUIRED")


def execute(
    inputs, evidence, *, mcp=False, token_file=None, ca=None, tools_sha256=None, capture=False
):
    """Catalog capture never forwards a model request; real use needs a sealed catalog.

    Caller persists the launch reservation first. Files and credentials are never
    accepted from a task. Shared Child.stop and atomic preserve owned lifecycle.
    """
    binary, sources = preflight.prepare(with_benchmark_login=True)
    native.private(evidence, directory=True)
    identity = secrets.token_hex(16)
    control = Path("/tmp") / ("acp-v8-native-" + identity)  # noqa: S108 -- exact owned root
    control.mkdir(mode=0o700)
    owner = {"uid": os.getuid(), "identity": identity, "path": str(control)}
    atomic(control / "owner.json", owner)
    atomic(evidence / "runtime-owner.json", owner)
    for name in ("inputs", "submission", "credential-cache"):
        (control / name).mkdir(mode=0o700)
    for name, body in inputs.items():
        target = control / "inputs" / name
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        target.write_bytes(body)
        target.chmod(0o600)
    native.stage_credentials(control / "credential-cache")
    if mcp:
        native.private(token_file)
        shutil.copyfile(token_file, control / "mcp-token")
        (control / "mcp-token").chmod(0o600)
    invocation = runtime.invocation(command=["app-server"], credentials=True, mcp=mcp)
    atomic(control / "invocation.json", invocation)
    policy = gateway.Policy(
        model_requests=not capture,
        capture_catalog=capture,
        descriptive=not capture,
        tools_sha256=tools_sha256,
        persist=lambda value: atomic(evidence / "wire-receipts.json", value),
    )
    policy.model_requests = False  # no warmup/inference before the actual task turn
    route = child = connection = None
    metrics = Observations(bool(mcp))
    report = {
        "version": 1,
        "kind": "catalog-only-no-provider" if capture else "native-Plus-attempt",
        "source_sha256": driver.digest(sources),
        "account_before": None,
        "account_after": None,
        "configured_model": None,
        "configured_effort": None,
        "configured_tier": None,
        "code": "INTERRUPTED_OR_UNKNOWN",
        "runtime_removed": False,
    }
    atomic(evidence / "session.json", report)
    started = time.monotonic()
    try:
        from urllib.parse import urlsplit

        peer = urlsplit(mcp["url"]) if mcp else None
        route = gateway.Route(
            control,
            policy,
            socket_path=control / "route.sock",
            mcp_origin=f"https://127.0.0.1:{peer.port}" if peer else None,
            mcp_ca=ca if peer else None,
        )
        route.start()
        child = native.Process(
            runtime.argv(
                binary, control / "inputs", control, driver.SOURCE / "skills", identity=identity
            ),
            workspace=driver.ROOT,
            env=runtime.environment(),
            bounds={"cpu_seconds": 600, "output_bytes": 16777216},
        )
        connection = native.Control(child, seconds=600)
        rpc(
            connection,
            metrics,
            "initialize",
            {
                "clientInfo": {"name": "v8-descriptive-pilot", "version": "1"},
                "capabilities": {"experimentalApi": True},
            },
        )
        connection.send({"method": "initialized"})
        report["account_before"] = account(connection, metrics)
        config = rpc(connection, metrics, "config/read", {"includeLayers": False, "cwd": "/work"})
        configured = config.get("config", {})
        if (
            configured.get("model") != native.MODEL
            or configured.get("model_provider") != "benchmark-plus"
            or configured.get("model_reasoning_effort") != "high"
            or configured.get("service_tier") != "default"
        ):
            raise FocusedError("NATIVE_CONFIG_RESOLUTION_CHANGED")
        skills = rpc(connection, metrics, "skills/list", {"cwds": ["/work"], "forceReload": True})
        found = {
            s.get("name")
            for r in skills.get("data", [])
            for s in r.get("skills", [])
            if s.get("enabled")
        }
        if found != {"python-testing", "systematic-debugging"} or any(
            r.get("errors") for r in skills.get("data", [])
        ):
            raise FocusedError("NATIVE_COMMON_SKILLS_CHANGED")
        thread = rpc(
            connection,
            metrics,
            "thread/start",
            {
                "model": native.MODEL,
                "modelProvider": "benchmark-plus",
                "serviceTier": "default",
                "cwd": "/work",
                "ephemeral": True,
                "approvalPolicy": "never",
            },
        )
        for key, expected in (
            ("model", native.MODEL),
            ("modelProvider", "benchmark-plus"),
            ("reasoningEffort", "high"),
        ):
            if thread.get(key) != expected:
                raise FocusedError("NATIVE_THREAD_CONFIGURATION_CHANGED")
        report.update(
            configured_model=thread["model"],
            configured_effort=thread["reasoningEffort"],
            configured_tier=configured["service_tier"],
            thread_service_tier=thread.get("serviceTier"),
        )
        if thread.get("serviceTier") not in {None, "default"}:
            raise FocusedError("NATIVE_THREAD_TIER_CHANGED")
        if mcp:
            require_mcp(
                rpc(
                    connection,
                    metrics,
                    "mcpServerStatus/list",
                    {
                        "threadId": thread["thread"]["id"],
                        "serverName": "agent_context_platform",
                        "detail": "toolsAndAuthOnly",
                        "limit": 10,
                    },
                )
            )
        if policy.model_attempts:
            raise FocusedError("PRE_TURN_MODEL_WARMUP_REFUSED")
        atomic(evidence / "session.json", report)
        policy.model_requests = not capture
        rpc(
            connection,
            metrics,
            "turn/start",
            {
                "threadId": thread["thread"]["id"],
                "input": [{"type": "text", "text": inputs["TASK.md"].decode()}],
                "model": native.MODEL,
                "effort": "high",
                "serviceTier": "default",
                "summary": "none",
            },
        )
        while metrics.turn_status is None:
            observe(connection, metrics, connection.next())
        report["account_after"] = account(connection, metrics)
        report["code"] = (
            "CATALOG_CAPTURED"
            if capture and policy.catalog
            else ("COMPLETED" if metrics.turn_status == "completed" else "NATIVE_TURN_FAILED")
        )
    except FocusedError as error:
        code = str(error)
        import re

        report["code"] = code if re.fullmatch(r"[A-Z_]{1,100}", code) else "NATIVE_SESSION_REFUSED"
    except (OSError, ValueError, KeyError, TypeError):
        report["code"] = "NATIVE_SESSION_REFUSED"
    finally:
        try:
            if child is not None:
                if child.process.poll() is None:
                    child.process.stdin.close()
                    try:
                        child.process.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        pass
                if connection is not None:
                    connection.selector.close()
                child.stop()
            if route is not None:
                route.close()
            updated = control / "credential-cache/auth.json"
            native.private(updated)
            destination = native.CREDENTIAL_HOME / "auth.updated"
            shutil.copyfile(updated, destination)
            destination.chmod(0o600)
            os.replace(destination, native.CREDENTIAL_HOME / "auth.json")
            submission = evidence / "submission"
            shutil.copytree(control / "submission", submission)
            # Expand only the exact known input/submission allowlists for cleanup.
            cleanup_runtime(control, owner, set(inputs))
            report["runtime_removed"] = True
        finally:
            receipts = receipt_summary(policy, capture=capture)
            if report["code"] == "COMPLETED" and receipts["receipt_failure"]:
                report["code"] = "NATIVE_RECEIPT_REFUSED"
            report.update(
                duration_seconds=time.monotonic() - started,
                metrics=metrics.report(),
                catalog=policy.catalog,
                http_routes_seen=policy.routes,
                route_rejections=policy.rejections,
                model_requests_local_attempts=policy.model_attempts,
                **receipts,
                cash_liability="unknown"
                if report["account_after"] is None or not receipts["transport_settled"]
                else "zero-credit-state-rechecked",
                billed_spend=None,
                token_or_quota_upper_bound=None,
            )
            atomic(evidence / "session.json", report)
    return report


def cleanup_runtime(control, owner, names):
    """Owned input files only; no foreign file or directory can be removed."""
    native.private(control, directory=True)
    native.private(control / "owner.json")
    if (
        owner
        != {
            "uid": os.getuid(),
            "identity": control.name.removeprefix("acp-v8-native-"),
            "path": str(control),
        }
        or driver.read(control / "owner.json") != owner
    ):
        raise FocusedError("FOREIGN_NATIVE_CLEANUP_REFUSED")
    allowed = {"inputs/" + n for n in names} | {"submission/" + n for n in names}
    allowed |= {"submission/INVALID_SUBMISSION", "mcp-token"}
    allowed |= {
        "owner.json",
        "invocation.json",
        "route.sock",
        "ca.pem",
        "chatgpt.com.pem",
        "chatgpt.com.key",
        "auth.openai.com.pem",
        "auth.openai.com.key",
        "credential-cache/auth.json",
    }
    directories = {str(parent) for n in allowed for parent in Path(n).parents} | {
        "inputs",
        "submission",
    }
    directories.add("credential-cache")
    for folder in (control,):
        for p in folder.rglob("*"):
            name = p.relative_to(control).as_posix()
            if (
                p.is_symlink()
                or (p.is_dir() and name not in directories)
                or (not p.is_dir() and name not in allowed)
            ):
                raise FocusedError("FOREIGN_NATIVE_CLEANUP_ENTRY")
            if p.is_file() and p.stat().st_nlink != 1:
                raise FocusedError("LINKED_NATIVE_CLEANUP_ENTRY")
    shutil.rmtree(control)
