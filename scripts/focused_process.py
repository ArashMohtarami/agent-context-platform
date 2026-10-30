"""Bounded subprocesses for the explicitly selected disposable test graph."""

import os
import signal
import subprocess
from pathlib import Path


class FocusedError(RuntimeError):
    """A required focused check is incomplete or refused."""


def descendant_handles(pid):
    """Pin only this child's Linux descendants, including detached MCP sessions."""
    handles, pending, seen = [], [pid], {pid}
    while pending:
        parent = pending.pop()
        children = set()
        for path in Path(f"/proc/{parent}/task").glob("*/children"):
            try:
                children.update(int(value) for value in path.read_text().split())
            except FileNotFoundError:
                pass
        for child in children - seen:
            seen.add(child)
            try:
                handle = os.pidfd_open(child)
            except ProcessLookupError:
                continue
            try:
                # Check ancestry after opening the stable handle. Never signal
                # an unrelated process through a reused numeric PID.
                fields = Path(f"/proc/{child}/stat").read_text().rsplit(")", 1)[1].split()
                if int(fields[1]) != parent:
                    os.close(handle)
                    continue
            except FileNotFoundError:
                os.close(handle)
                continue
            handles.append(handle)
            pending.append(child)
    return handles


class Child:
    def __init__(self, argv: list[str], *, cwd: Path, env: dict[str, str], log: Path):
        self.log = log
        log.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.output = log.open("wb")
        log.chmod(0o600)
        try:
            self.process = subprocess.Popen(  # noqa: S603 -- validated argv, never a shell
                argv,
                cwd=cwd,
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=self.output,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
        except BaseException:
            self.output.close()
            raise

    def stop(self):
        # Snapshot before terminating the parent: the SDK and nested fixture
        # commands can create sessions outside the parent's process group.
        handles = descendant_handles(self.process.pid) if self.process.poll() is None else []
        for handle in reversed(handles):
            try:
                signal.pidfd_send_signal(handle, signal.SIGKILL)
            except ProcessLookupError:
                pass
            finally:
                os.close(handle)
        # Kill the process group, including a child left behind by an exited parent.
        try:
            os.killpg(self.process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            self.process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass
        try:
            os.killpg(self.process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        self.process.wait(timeout=5)
        self.output.close()

    def wait(self, timeout: float) -> int:
        try:
            return self.process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            raise FocusedError(f"child exceeded {timeout}s; inspect private log") from None
        finally:
            self.stop()


def run(argv: list[str], *, cwd: Path, env: dict[str, str], log: Path, timeout: float) -> int:
    return Child(argv, cwd=cwd, env=env, log=log).wait(timeout)
