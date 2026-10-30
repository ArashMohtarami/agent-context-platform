"""Model-free validation boundary, not a credential-bearing Codex launch path."""

from __future__ import annotations

import importlib
import json
import os
import resource
import selectors
import subprocess
import tempfile
import time
from pathlib import Path

from scripts.focused_process import Child

from .usage import Stream

ROOT = Path(__file__).resolve().parent
grading = importlib.import_module("benchmarks.agent-value.grading")


def argv(inputs: Path, skills: Path, bounds: dict, filter_fd: str) -> list[str]:
    return [
        "/usr/bin/bwrap",
        "--unshare-all",
        "--unshare-user",
        "--share-net",
        "--die-with-parent",
        "--new-session",
        "--disable-userns",
        "--cap-drop",
        "ALL",
        "--clearenv",
        "--setenv",
        "HOME",
        "/work/home",
        "--setenv",
        "CODEX_HOME",
        "/work/home/.codex",
        "--setenv",
        "PATH",
        "/usr/bin:/bin",
        "--ro-bind",
        "/usr",
        "/usr",
        "--ro-bind",
        "/lib",
        "/lib",
        "--ro-bind",
        "/lib64",
        "/lib64",
        "--ro-bind",
        str(inputs),
        "/inputs",
        "--ro-bind",
        str(skills),
        "/skills",
        "--ro-bind",
        str(ROOT / "stub.py"),
        "/stub.py",
        "--size",
        str(bounds["workspace_bytes"]),
        "--tmpfs",
        "/work",
        "--remount-ro",
        "/",
        "--chdir",
        "/work",
        "--seccomp",
        filter_fd,
        "/usr/bin/python3",
        "-I",
        "-B",
        "/stub.py",
    ]


def limits(bounds: dict) -> None:
    for key, cap in (
        (resource.RLIMIT_CPU, bounds["cpu_seconds"]),
        (resource.RLIMIT_AS, bounds["memory_bytes"]),
        (resource.RLIMIT_FSIZE, bounds["output_bytes"]),
        (resource.RLIMIT_NOFILE, 64),
        (resource.RLIMIT_CORE, 0),
    ):
        resource.setrlimit(key, (cap, cap))


class StreamChild(Child):
    """Reuse owned descendant/group stop; raw model text never goes to its log."""

    def __init__(self, command: list[str], policy, bounds: dict):
        self.output = open(os.devnull, "wb")
        try:
            self.process = subprocess.Popen(  # noqa: S603 -- fixed sandbox graph, no shell
                command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                env={"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8"},
                pass_fds=(policy.fileno(),),
                start_new_session=True,
                preexec_fn=lambda: limits(bounds),
            )
        except BaseException:
            self.output.close()
            raise


def execute(inputs: Path, skills: Path, bounds: dict, scenario: str, probes: list[str]) -> dict:
    if scenario not in {"ok", "timeout", "interrupt", "overflow", "probe", "bad-usage"}:
        raise ValueError("UNKNOWN_STUB_SCENARIO")
    stream = Stream("0.166.0")
    started = time.monotonic()
    code = "COMPLETED"
    count = 0
    probe = None
    with tempfile.TemporaryFile() as policy:
        grading.seccomp_filter(policy)
        child = StreamChild(argv(inputs, skills, bounds, str(policy.fileno())), policy, bounds)
        try:
            child.process.stdin.write(json.dumps({"scenario": scenario, "probes": probes}).encode())
            child.process.stdin.close()
            pending = b""
            with selectors.DefaultSelector() as selector:
                selector.register(child.process.stdout, selectors.EVENT_READ)
                running = True
                while running:
                    if time.monotonic() - started > bounds["wall_seconds"]:
                        code = "WALL_LIMIT"
                        break
                    for key, _ in selector.select(timeout=0.05):
                        chunk = os.read(key.fd, 4096)
                        if not chunk:
                            running = False
                            break
                        count += len(chunk)
                        pending += chunk
                        if count > bounds["output_bytes"] or len(pending) > 65536:
                            code = "OUTPUT_LIMIT"
                            running = False
                            break
                        while b"\n" in pending:
                            line, pending = pending.split(b"\n", 1)
                            event = json.loads(line)
                            if event.get("type") == "stub.probe":
                                values = event.get("denied")
                                if not isinstance(values, list) or any(
                                    type(v) is not bool for v in values
                                ):
                                    raise ValueError("INVALID_STUB_PROBE")
                                probe = values  # booleans only; no private file content retained
                            else:
                                stream.consume(event)
                            if (
                                stream.started > bounds["turns"]
                                or stream.tools > bounds["tools"]
                                or stream.events > bounds["events"]
                            ):
                                code = "EVENT_LIMIT"
                                running = False
                                break
                if pending and code == "COMPLETED":
                    code = "INCOMPLETE_EVENT"
            if code == "COMPLETED" and child.process.wait(timeout=1):
                code = "CHILD_FAILED"
        except (ValueError, UnicodeError, BrokenPipeError, subprocess.TimeoutExpired):
            code = "INVALID_CHILD_STREAM"
        finally:
            child.stop()
            if child.process.stdout:
                child.process.stdout.close()
            if child.process.stdin and not child.process.stdin.closed:
                child.process.stdin.close()
    return {
        "code": code,
        "exit_code": child.process.returncode,
        "duration_seconds": time.monotonic() - started,
        "output_bytes": count,
        "usage": stream.report(),
        "probe_denied": probe,
        "purpose": "synthetic stub validation; no vendor/model or task-success result",
    }
