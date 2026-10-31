"""Private-namespace stdlib bootstrap; not an agent runtime or model simulator.

Only an owned Unix proxy socket leaves the namespace. Credential cache bytes
are consumed by the native CLI, never printed or recorded as evidence.
"""

import contextlib
import json
import os
import selectors
import shutil
import socket
import socketserver
import subprocess
import threading
from pathlib import Path

MAX_BYTES = 67108864
slots = threading.BoundedSemaphore(4)


class Forward(socketserver.BaseRequestHandler):
    def handle(self):
        if not slots.acquire(blocking=False):
            return
        try:
            with socket.socket(socket.AF_UNIX) as target, selectors.DefaultSelector() as ready:
                target.connect("/control/route.sock")
                ready.register(self.request, selectors.EVENT_READ, target)
                ready.register(target, selectors.EVENT_READ, self.request)
                count = 0
                while events := ready.select(600):
                    for selected, _ in events:
                        data = selected.fileobj.recv(65536)
                        if not data:
                            return
                        count += len(data)
                        if count > MAX_BYTES:
                            return
                        selected.data.sendall(data)
        except OSError:
            return
        finally:
            slots.release()


class Server(socketserver.ThreadingTCPServer):
    daemon_threads = True
    allow_reuse_address = False


def copy_regular(source, target):
    """Read each parent/leaf without following an untrusted workspace symlink."""
    source = Path(source)
    relative = source.relative_to("/work") if source.is_relative_to("/work") else None
    descriptors = []
    try:
        if relative is None:
            descriptor = os.open(source, os.O_RDONLY | os.O_NOFOLLOW)
        else:
            descriptor = os.open("/work", os.O_RDONLY | os.O_DIRECTORY)
            descriptors.append(descriptor)
            for part in relative.parts[:-1]:
                descriptor = os.open(
                    part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor
                )
                descriptors.append(descriptor)
            descriptor = os.open(relative.name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=descriptor)
        with os.fdopen(descriptor, "rb") as incoming:
            metadata = os.fstat(incoming.fileno())
            import stat

            if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > 1048576:
                raise ValueError("nonregular or oversized submission")
            data = incoming.read(1048577)
            if len(data) > 1048576:
                raise ValueError("submission size limit")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)


def main():
    invocation = json.loads(Path("/control/invocation.json").read_bytes())
    if set(invocation) != {"argv", "environment", "credentials", "mcp"}:
        raise ValueError("unexpected bootstrap inputs")
    os.umask(0o077)
    for folder in (
        "/cli-home",
        "/cli-home/home",
        "/runtime",
        "/runtime/tmp",
        "/runtime/xdg",
        "/work",
    ):
        Path(folder).mkdir(mode=0o700, exist_ok=True)
        Path(folder).chmod(0o700)
    initial = {
        p.relative_to("/inputs").as_posix() for p in Path("/inputs").rglob("*") if p.is_file()
    }
    for name in sorted(initial):
        copy_regular(Path("/inputs") / name, Path("/work") / name)
    home = Path("/cli-home")
    if invocation["credentials"]:
        shutil.copyfile("/credential-cache/auth.json", home / "auth.json")
        (home / "auth.json").chmod(0o600)
    environment = invocation["environment"]
    if invocation["mcp"]:
        token = Path("/control/mcp-token").read_text().strip()
        if not token or len(token) > 4096:
            raise ValueError("invalid owned MCP credential")
        # Native helpers can expose their private /proc environment. Keep the
        # bearer value exclusively in this fresh parent-only config file, never
        # argv or process environment. Task filesystem policy denies this home.
        mcp = invocation["mcp"]
        if not isinstance(mcp, dict) or set(mcp) != {"url", "tools"}:
            raise ValueError("exact disposable MCP configuration required")
        config = (
            "[mcp_servers.agent_context_platform]\n"
            + "url = "
            + json.dumps(mcp["url"])
            + "\n"
            + "enabled_tools = "
            + json.dumps(mcp["tools"])
            + "\n"
            + "startup_timeout_sec = 10\ntool_timeout_sec = 30\n"
            + "[mcp_servers.agent_context_platform.http_headers]\n"
            + "Authorization = "
            + json.dumps("Bearer " + token)
            + "\n"
        )
        (home / "config.toml").write_text(config)
        (home / "config.toml").chmod(0o600)
    with Server(("127.0.0.1", 7890), Forward) as server:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            # Native stdin/stdout are pipes owned by the experiment controller.
            # Its JSONL filter drops all raw text, arguments and reasoning.
            process = subprocess.Popen(  # noqa: S603 -- exact sealed native argv
                invocation["argv"],
                env=environment,
                cwd="/work",
            )
            code = process.wait()
        finally:
            server.shutdown()
            thread.join(timeout=5)
            if invocation["credentials"] and (home / "auth.json").is_file():
                # Preserve only the intentionally provisioned cache, outside
                # evidence. Native refresh can replace it between attempts.
                with contextlib.suppress(OSError):
                    target = Path("/credential-cache/auth.updated")
                    shutil.copyfile(home / "auth.json", target)
                    target.chmod(0o600)
                    os.replace(target, "/credential-cache/auth.json")
            try:
                files = list(Path("/work").rglob("*"))
                if any(p.is_symlink() for p in files):
                    raise ValueError("linked submission")
                actual = {p.relative_to("/work").as_posix() for p in files if not p.is_dir()}
                if actual != initial:
                    raise ValueError("unexpected or missing submission")
                for name in sorted(initial):
                    copy_regular(Path("/work") / name, Path("/submission") / name)
            except (OSError, ValueError):
                # No untrusted filename, content or exception text is persisted.
                Path("/submission/INVALID_SUBMISSION").write_text("input allowlist changed")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
