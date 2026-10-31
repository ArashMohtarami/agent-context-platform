"""Owned native account/config preflight; the route denies every model request.

Only the benchmark-specific login cache can be provisioned. No raw native event,
account identity, config/environment value or reasoning text becomes evidence.
This is not a vendor agent attempt, a usage-coverage proof or a launch approval.
"""

from __future__ import annotations

import json
import os
import secrets
import shutil
from pathlib import Path

from scripts.evaluation_lifecycle import atomic
from scripts.focused_process import FocusedError

from . import driver, gateway, native, runtime

BASE = driver.ROOT / ".private/v8-agent-value-planning/phase-4/native-preflights"


def prepare(*, with_benchmark_login=False):
    """Read-only prerequisite validation precedes all possible child processes."""
    if type(with_benchmark_login) is not bool:
        raise FocusedError("EXPLICIT_PREFLIGHT_MODE_REQUIRED")
    contract = driver.read(driver.SOURCE / "adapter.json")
    binary = native.BINARY.resolve(strict=True)
    if driver.dataset.sha(binary.read_bytes()) != contract["binary_sha256"]:
        raise FocusedError("UNREVIEWED_CLI_BINARY")
    if with_benchmark_login:
        native.private(native.CREDENTIAL_HOME, directory=True)
        native.private(native.CREDENTIAL_HOME / "auth.json")
    driver.common_skills()
    sources = driver.source_fingerprint()
    return binary, sources


def run(*, with_benchmark_login=False):
    binary, sources = prepare(with_benchmark_login=with_benchmark_login)
    BASE.mkdir(mode=0o700, parents=True, exist_ok=True)
    native.private(BASE, directory=True)
    identity = secrets.token_hex(16)
    evidence = BASE / identity
    evidence.mkdir(mode=0o700)
    # Unix sockets have a 108-byte pathname limit on Linux. The random short
    # runtime root is owned, pinned in its receipt and never chosen by an agent.
    control = Path("/tmp") / ("acp-v8-native-" + identity)  # noqa: S108 -- owned random 0700 runtime
    control.mkdir(mode=0o700)
    owner = {"uid": os.getuid(), "identity": identity, "path": str(control)}
    atomic(control / "owner.json", owner)
    atomic(evidence / "owner.json", owner)
    for name in ("credential-cache", "submission", "inputs"):
        (control / name).mkdir(mode=0o700)
    if with_benchmark_login:
        native.stage_credentials(control / "credential-cache")
    invocation = runtime.invocation(
        command=["app-server"], credentials=with_benchmark_login, mcp=False
    )
    atomic(control / "invocation.json", invocation)
    argv = runtime.argv(
        binary, control / "inputs", control, driver.SOURCE / "skills", identity=identity
    )
    policy = gateway.Policy(model_requests=False)
    route = child = connection = None
    report = {
        "version": 1,
        "kind": "actual-installed-cli-model-free-preflight",
        "source_sha256": driver.digest(sources),
        "binary_sha256": driver.dataset.sha(binary.read_bytes()),
        "invocation_sha256": driver.digest(invocation),
        "argv": argv,
        "benchmark_login_provisioned": with_benchmark_login,
        "provider_agent_attempts": 0,
        "model_requests_allowed": False,
        "account": None,
        "config_verified": False,
        "skills_verified": False,
        "stage": "prepared",
        "status": "interrupted-or-unknown",
        "execution_authorized": False,
    }
    atomic(evidence / "result.json", report)
    try:
        route = gateway.Route(control, policy, socket_path=control / "route.sock")
        route.start()
        child = native.Process(
            argv,
            workspace=driver.ROOT,
            env=runtime.environment(),
            bounds={"cpu_seconds": 30, "output_bytes": 16777216},
        )
        connection = native.Control(child, seconds=30)
        report["stage"] = "initialize"
        connection.call(
            "initialize", {"clientInfo": {"name": "v8-model-free-preflight", "version": "1"}}
        )
        connection.send({"method": "initialized"})
        report["stage"] = "account-read"
        account = connection.call("account/read", {"refreshToken": False})
        if with_benchmark_login:
            limits = connection.call("account/rateLimits/read", {})
            report["account"] = native.account_gate(account, limits)
        elif account.get("account") is not None:
            raise FocusedError("UNEXPECTED_ACCOUNT_IN_FRESH_HOME")
        report["stage"] = "config-read"
        config = connection.call("config/read", {"includeLayers": False, "cwd": "/work"})
        actual = config.get("config", {})
        if (
            actual.get("model") != native.MODEL
            or actual.get("model_reasoning_effort") != "high"
            or actual.get("model_provider") != "benchmark-plus"
            or actual.get("service_tier") != "default"
        ):
            raise FocusedError("NATIVE_CONFIG_RESOLUTION_CHANGED")
        report["config_verified"] = True
        report["stage"] = "skills-list"
        skills = connection.call("skills/list", {"cwds": ["/work"], "forceReload": True})
        found = set()
        for row in skills.get("data", []):
            if row.get("errors"):
                raise FocusedError("NATIVE_SKILL_DISCOVERY_ERRORS")
            for skill in row.get("skills", []):
                if skill.get("enabled") is True:
                    found.add(skill.get("name"))
        if found != {"python-testing", "systematic-debugging"}:
            raise FocusedError("NATIVE_COMMON_SKILL_DISCOVERY_CHANGED")
        report["skills_verified"] = True
        report["status"] = "model-free-preflight-passed"
    except FocusedError as exc:
        import re

        code = str(exc)
        report["code"] = (
            code if re.fullmatch(r"[A-Z_]{1,100}", code) else "NATIVE_PREFLIGHT_REFUSED"
        )
        report["status"] = "model-free-preflight-refused"
        raise
    except (OSError, ValueError, KeyError, TypeError):
        # No exception text from account/config/transport is retained.
        report["status"] = "model-free-preflight-refused"
        raise
    finally:
        report["runtime_removed"] = False
        try:
            if connection is not None:
                connection.close()
            elif child is not None:
                child.stop()
            if route is not None:
                route.close()
            if with_benchmark_login:
                updated = control / "credential-cache/auth.json"
                if updated.is_file() and not updated.is_symlink():
                    native.private(updated)
                    target = native.CREDENTIAL_HOME / "auth.updated"
                    shutil.copyfile(updated, target)
                    target.chmod(0o600)
                    os.replace(target, native.CREDENTIAL_HOME / "auth.json")
            cleanup(control, owner)
            report["runtime_removed"] = True
        finally:
            report["route_rejections"] = policy.rejections
            report["http_routes_seen"] = policy.routes
            report["numeric_receipts"] = policy.receipts.report()
            atomic(evidence / "result.json", report)
    return {"evidence": str(evidence), "status": report["status"], "provider_agent_attempts": 0}


def cleanup(control, owner):
    """Exact owned runtime only; never signal saved PIDs or delete evidence."""
    native.private(control, directory=True)
    native.private(control / "owner.json")
    if (
        owner
        != {
            "uid": os.getuid(),
            "identity": control.name.removeprefix("acp-v8-native-"),
            "path": str(control),
        }
        or json.loads((control / "owner.json").read_bytes()) != owner
    ):
        raise FocusedError("FOREIGN_NATIVE_CLEANUP_REFUSED")
    allowed = {
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
    directories = {"credential-cache", "submission", "inputs"}
    for path in control.rglob("*"):
        name = path.relative_to(control).as_posix()
        if (
            path.is_symlink()
            or (path.is_dir() and name not in directories)
            or (not path.is_dir() and name not in allowed)
        ):
            raise FocusedError("FOREIGN_NATIVE_CLEANUP_ENTRY")
        if path.is_file() and path.stat().st_nlink != 1:
            raise FocusedError("LINKED_NATIVE_CLEANUP_ENTRY")
    shutil.rmtree(control)
