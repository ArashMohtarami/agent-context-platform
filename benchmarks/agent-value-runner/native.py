"""Pinned native Codex control protocol; no model request in account preflight.

Only the purpose-provisioned benchmark credential directory is accepted. Native
credentials, account identifiers and raw protocol text never become evidence.
"""

from __future__ import annotations

import importlib
import json
import os
import re
import resource
import selectors
import shutil
import subprocess
import time
from decimal import Decimal, InvalidOperation
from pathlib import Path

from scripts.focused_process import Child, FocusedError

dataset = importlib.import_module("benchmarks.agent-value.dataset")
ROOT = Path(__file__).resolve().parents[2]
CREDENTIAL_HOME = ROOT / ".private/agent-value-credentials/home/.codex"
MODEL = "gpt-6.1-sol"
VERSION = "0.166.0"
BINARY = Path(
    "/home/arash/.codex/packages/standalone/releases/0.166.0-x86_64-unknown-linux-musl/bin/codex"
)
TOOLS = (
    "get_platform_capabilities",
    "list_projects",
    "resolve_project",
    "search_knowledge",
    "search_context",
    "get_context",
)
DISABLED = (
    "apps",
    "plugins",
    "hooks",
    "browser_use",
    "computer_use",
    "multi_agent",
    "memories",
    "shell_snapshot",
    "skill_search",
    "skill_mcp_dependency_install",
    "fast_mode",
    "ultrafast_mode",
    "unbounded_connection_retries",
    "use_legacy_landlock",
    "view_image",
    "image_generation",
    "code_mode_host",
    "code_mode",
    "multi_agent_v2",
    "tool_suggest",
    "sleep_tool",
)
PREFLIGHT_METHODS = {
    "initialize",
    "account/read",
    "account/rateLimits/read",
    "config/read",
    "skills/list",
    "mcpServerStatus/list",
    "codex/sandbox-state",
}


def private(path: Path, *, directory=False):
    if path.absolute() != path.resolve() or path.is_symlink():
        raise FocusedError("NONCANONICAL_NATIVE_STATE")
    metadata = path.stat()
    if metadata.st_uid != os.getuid() or metadata.st_mode & 0o077:
        raise FocusedError("PRIVATE_NATIVE_STATE_REQUIRED")
    valid = path.is_dir() if directory else path.is_file() and metadata.st_nlink == 1
    if not valid:
        raise FocusedError("REGULAR_NATIVE_STATE_REQUIRED")


def stage_credentials(home: Path):
    """Copy only the intentionally provisioned benchmark cache, never active login.

    File bytes are not parsed, hashed, returned or logged. This is runtime
    credential provisioning, outside the evidence and task input inventories.
    """
    private(CREDENTIAL_HOME, directory=True)
    source = CREDENTIAL_HOME / "auth.json"
    private(source)
    if not 1 <= source.stat().st_size <= 65536:
        raise FocusedError("INVALID_BENCHMARK_CREDENTIAL_CACHE")
    private(home, directory=True)
    destination = home / "auth.json"
    if os.path.lexists(destination):
        raise FocusedError("FRESH_NATIVE_HOME_REQUIRED")
    with source.open("rb") as incoming, destination.open("xb") as outgoing:
        os.chmod(destination, 0o600)
        shutil.copyfileobj(incoming, outgoing, length=65536)
    return {"source": "purpose-provisioned benchmark login", "secret_values_recorded": False}


def configuration(binary: Path, workspace: Path, home: Path):
    """Exact isolated native settings. No legacy sandbox flag can override them."""
    if not all(p.is_absolute() and p.resolve() == p for p in (binary, workspace, home)):
        raise FocusedError("CANONICAL_NATIVE_PATHS_REQUIRED")
    return {
        "model": MODEL,
        "model_provider": "benchmark-plus",
        "model_reasoning_effort": "high",
        "service_tier": "default",
        "approval_policy": "never",
        "default_permissions": "pilot",
        "cli_auth_credentials_store": "file",
        "web_search": "disabled",
        "check_for_update_on_startup": False,
        "model_reasoning_summary": "none",
        "shell_environment_policy.inherit": "none",
        "shell_environment_policy.ignore_default_excludes": False,
        "shell_environment_policy.set": {"PYTHONDONTWRITEBYTECODE": "1", "PATH": "/usr/bin:/bin"},
        "history.persistence": "none",
        **{"features." + name: False for name in (*DISABLED, "goals", "incremental_tools")},
        # In 0.166.0 the boolean multi_agent flag alone still advertises the
        # collaboration namespace. The structured v2 switch was checked with
        # the installed binary against an inert, disconnected local endpoint.
        "features.multi_agent_v2": {
            "enabled": False,
            "disable_native_tools": True,
            "disable_direct_message": True,
            "wait_agent_enabled": False,
        },
        "features.code_mode": {"enabled": False, "direct_only_tool_namespaces": ["functions"]},
        "features.skip_host_skill_discovery": True,
        "model_providers.benchmark-plus": {
            "name": "OpenAI Plus benchmark route",
            "base_url": "https://chatgpt.com/backend-api/codex",
            "wire_api": "responses",
            "requires_openai_auth": True,
            "request_max_retries": 0,
            "stream_max_retries": 0,
            "supports_websockets": False,
        },
        "permissions.pilot.extends": ":workspace",
        "permissions.pilot.filesystem": {
            ":root": "deny",
            "/proc": "deny",
            ":minimal": "read",
            ":tmpdir": "write",
            ":slash_tmp": "deny",
            str(binary): "read",
            str(home / "skills"): "read",
            **{
                str(workspace / name): "read"
                for name in ("README.md", "TASK.md", "test_visible.py", "visible.json", "knowledge")
            },
            ":workspace_roots": {".": "write"},
        },
        "permissions.pilot.network.enabled": False,
    }


def overrides(config):
    """Render literal TOML overrides, without shell interpolation or secret values."""

    def toml(value):
        if isinstance(value, dict):
            return "{" + ",".join(json.dumps(k) + "=" + toml(v) for k, v in value.items()) + "}"
        if isinstance(value, list):
            return "[" + ",".join(toml(v) for v in value) + "]"
        if type(value) not in {str, int, bool}:
            raise FocusedError("UNREVIEWED_NATIVE_CONFIGURATION_TYPE")
        return json.dumps(value)

    return [item for key, value in config.items() for item in ("--config", key + "=" + toml(value))]


class Process(Child):
    """Owned lifecycle reuse; raw stdout/stderr never go to a log."""

    def __init__(self, argv, *, workspace, env, bounds):
        self.output = open(os.devnull, "wb")
        try:
            self.process = subprocess.Popen(  # noqa: S603 -- controller-built literal argv
                argv,
                cwd=workspace,
                env=env,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
                # Resident memory/process/CPU-rate limits are enforced by the
                # mandatory user scope in runtime.argv. V8 reserves virtual
                # address space beyond its resident limit, so RLIMIT_AS from
                # the Python stub helper is not a native runtime memory fuse.
                preexec_fn=lambda: process_limits(bounds),
            )
        except BaseException:
            self.output.close()
            raise


def process_limits(bounds):
    for key, cap in (
        (resource.RLIMIT_CPU, bounds["cpu_seconds"]),
        (resource.RLIMIT_FSIZE, bounds["output_bytes"]),
        (resource.RLIMIT_NOFILE, 256),
        (resource.RLIMIT_CORE, 0),
    ):
        resource.setrlimit(key, (cap, cap))


class Control:
    """Small bounded JSONL IPC client, restricted to model-free native methods."""

    def __init__(self, child, *, seconds=30, max_bytes=1048576):
        self.child = child
        self.deadline = time.monotonic() + seconds
        self.maximum = max_bytes
        self.pending = b""
        self.events = 0
        self.counter = 0
        self.selector = selectors.DefaultSelector()
        self.selector.register(child.process.stdout, selectors.EVENT_READ)

    def send(self, value):
        self.child.process.stdin.write(dataset.canonical(value))
        self.child.process.stdin.flush()

    def next(self):
        while b"\n" not in self.pending:
            remaining = self.deadline - time.monotonic()
            if remaining <= 0 or not self.selector.select(remaining):
                raise FocusedError("NATIVE_CONTROL_DEADLINE")
            chunk = os.read(self.child.process.stdout.fileno(), 65536)
            if not chunk:
                raise FocusedError("NATIVE_CONTROL_CHILD_FAILED")
            self.pending += chunk
            if len(self.pending) > self.maximum:
                raise FocusedError("NATIVE_CONTROL_STREAM_BOUND")
        line, self.pending = self.pending.split(b"\n", 1)
        self.events += 1
        if self.events > 4096:
            raise FocusedError("NATIVE_CONTROL_EVENT_BOUND")
        value = json.loads(line)
        if not isinstance(value, dict):
            raise FocusedError("INVALID_NATIVE_CONTROL_EVENT")
        return value

    def call(self, method, params):
        if method not in PREFLIGHT_METHODS:
            raise FocusedError("MODEL_OR_UNDECLARED_METHOD_IN_PREFLIGHT")
        self.counter += 1
        number = self.counter
        self.send({"id": number, "method": method, "params": params})
        while True:
            response = self.next()
            if response.get("id") == number and "method" not in response:
                if "error" in response or "result" not in response:
                    raise FocusedError("NATIVE_CONTROL_REQUEST_REFUSED")
                return response["result"]
            if "id" in response and "method" in response:
                self.send({"id": response["id"], "error": {"code": -32601, "message": "denied"}})

    def close(self):
        self.selector.close()
        self.child.stop()


def account_gate(account, limits):
    """Require actual Plus auth and positively known absence of paid credits.

    Does not promise a token allowance or reserve quota shared with other work.
    The runtime must additionally prohibit API routes/purchases and recheck before
    each attempt. Unknown/missing credit state cannot mean zero spending risk.
    """
    if not isinstance(account, dict) or not isinstance(account.get("account"), dict):
        raise FocusedError("PLUS_SIGN_IN_REQUIRED")
    identity = account["account"]
    if identity.get("type") != "chatgpt" or identity.get("planType") != "plus":
        raise FocusedError("EXACT_PLUS_AUTHENTICATION_REQUIRED")
    buckets = limits.get("rateLimitsByLimitId") if isinstance(limits, dict) else None
    if not isinstance(buckets, dict) or not buckets:
        raise FocusedError("COMPLETE_PLUS_CREDIT_STATE_REQUIRED")
    result = []
    for key, bucket in sorted(buckets.items()):
        if not re.fullmatch(r"[A-Za-z0-9_.-]{1,100}", key) or not isinstance(bucket, dict):
            raise FocusedError("UNREVIEWED_PLUS_BUCKET")
        credits = bucket.get("credits")
        if (
            not isinstance(credits, dict)
            or credits.get("hasCredits") is not False
            or credits.get("unlimited") is not False
        ):
            raise FocusedError("PURCHASED_OR_UNKNOWN_CREDITS_REFUSED")
        try:
            balance = Decimal(credits["balance"])
        except (KeyError, TypeError, InvalidOperation):
            raise FocusedError("UNKNOWN_PLUS_CREDIT_BALANCE") from None
        if not balance.is_finite() or balance != 0:
            raise FocusedError("NONZERO_PLUS_CREDIT_BALANCE_REFUSED")
        windows = []
        for name in ("primary", "secondary"):
            window = bucket.get(name)
            if window is None:
                continue
            percent = window.get("usedPercent") if isinstance(window, dict) else None
            if type(percent) not in {float, int} or not 0 <= percent < 80:
                raise FocusedError("PLUS_QUOTA_EXHAUSTED_OR_UNKNOWN")
            windows.append({"window": name, "used_percent": percent})
        if not windows or bucket.get("rateLimitReachedType") is not None:
            raise FocusedError("PLUS_QUOTA_EXHAUSTED_OR_UNKNOWN")
        result.append({"bucket": key, "credit_balance": "0", "windows": windows})
    return {
        "auth_mode": "chatgpt",
        "plan": "plus",
        "buckets": result,
        "additional_cash_authorized": "0.00",
        "purchased_credits_authorized": False,
        "quota_upper_bound": None,
        "account_identity_retained": False,
    }
