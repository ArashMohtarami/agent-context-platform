"""Exact owned native process graph: user cgroup, filesystem/PID/network namespaces.

No global install, shared helper edit, Docker socket or credential-valued argv.
Planning this graph starts no process, service, model or provider request.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from urllib.parse import urlsplit

from scripts.focused_process import FocusedError

from . import native


def argv(binary, inputs, control, skills, *, identity, workspace_bytes=536870912):
    if not re.fullmatch(r"[a-f0-9]{32}", identity):
        raise FocusedError("EXACT_OWNED_NATIVE_ID_REQUIRED")
    if type(workspace_bytes) is not int or not 1048576 <= workspace_bytes <= 536870912:
        raise FocusedError("NATIVE_WORKSPACE_BOUND_REQUIRED")
    unit = "acp-v8-agent-value-" + identity + ".scope"
    return [
        "/usr/bin/systemd-run",
        "--user",
        "--scope",
        "--quiet",
        "--collect",
        "--unit=" + unit,
        "--property=MemoryMax=2147483648",
        "--property=MemorySwapMax=0",
        "--property=TasksMax=128",
        "--property=CPUQuota=100%",
        "/usr/bin/bwrap",
        "--unshare-all",
        "--die-with-parent",
        "--new-session",
        "--cap-drop",
        "ALL",
        "--clearenv",
        "--ro-bind",
        "/usr",
        "/usr",
        "--symlink",
        "/usr/bin",
        "/bin",
        "--symlink",
        "/usr/sbin",
        "/sbin",
        "--ro-bind",
        "/lib",
        "/lib",
        "--ro-bind",
        "/lib64",
        "/lib64",
        "--proc",
        "/proc",
        "--dev",
        "/dev",
        "--ro-bind",
        str(binary),
        "/vendor/codex",
        "--ro-bind",
        str(Path(__file__).with_name("relay.py")),
        "/bootstrap.py",
        "--ro-bind",
        str(inputs),
        "/inputs",
        "--ro-bind",
        str(control / "route.sock"),
        "/control/route.sock",
        "--ro-bind",
        str(control / "invocation.json"),
        "/control/invocation.json",
        "--ro-bind",
        str(control / "ca.pem"),
        "/control/ca.pem",
        "--bind",
        str(control / "credential-cache"),
        "/credential-cache",
        "--bind",
        str(control / "submission"),
        "/submission",
        "--ro-bind-try",
        str(control / "mcp-token"),
        "/control/mcp-token",
        "--size",
        "67108864",
        "--tmpfs",
        "/cli-home",
        "--ro-bind",
        str(skills),
        "/cli-home/skills",
        "--size",
        "67108864",
        "--tmpfs",
        "/runtime",
        "--size",
        str(workspace_bytes),
        "--tmpfs",
        "/work",
        "--size",
        "67108864",
        "--tmpfs",
        "/tmp",  # noqa: S108 -- private size-bounded namespace tmpfs, not host /tmp
        "--remount-ro",
        "/",
        "--chdir",
        "/work",
        "--setenv",
        "PATH",
        "/usr/bin:/bin",
        "--setenv",
        "LANG",
        "C.UTF-8",
        "/usr/bin/python3",
        "-I",
        "-B",
        "/bootstrap.py",
    ]


def environment():
    # Only the scope launcher needs the user bus. It is never mounted or passed
    # into the network/filesystem namespace or an agent shell environment.
    return {
        "PATH": "/usr/bin:/bin",
        "LANG": "C.UTF-8",
        "XDG_RUNTIME_DIR": "/run/user/" + str(os.getuid()),
    }


def invocation(*, command, credentials, mcp):
    if type(credentials) is not bool:
        raise FocusedError("EXPLICIT_NATIVE_CREDENTIAL_MODE_REQUIRED")
    if mcp is not False:
        if (
            not isinstance(mcp, dict)
            or set(mcp) != {"url", "tools"}
            or mcp["tools"] != list(native.TOOLS)
        ):
            raise FocusedError("EXACT_NATIVE_MCP_TOOLS_REQUIRED")
        peer = urlsplit(mcp["url"])
        if (
            peer.scheme != "https"
            or peer.hostname != "127.0.0.1"
            or not peer.port
            or peer.path != "/mcp"
            or peer.query
            or peer.fragment
            or peer.username
            or peer.password
        ):
            raise FocusedError("EXACT_OWNED_NATIVE_MCP_URL_REQUIRED")
    config = native.configuration(Path("/vendor/codex"), Path("/work"), Path("/cli-home"))
    return {
        "argv": ["/vendor/codex", "--no-daemon", *native.overrides(config), *command],
        "environment": {
            "HOME": "/cli-home/home",
            "CODEX_HOME": "/cli-home",
            "PATH": "/usr/bin:/bin",
            "LANG": "C.UTF-8",
            "TMPDIR": "/runtime/tmp",
            "XDG_RUNTIME_DIR": "/runtime/xdg",
            "HTTPS_PROXY": "http://127.0.0.1:7890",
            "HTTP_PROXY": "http://127.0.0.1:7890",
            "NO_PROXY": "",
            "CODEX_CA_CERTIFICATE": "/control/ca.pem",
            "SSL_CERT_FILE": "/control/ca.pem",
            "RUST_LOG": "off",
        },
        "credentials": credentials,
        "mcp": mcp,
    }
