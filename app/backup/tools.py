"""Running `mongodump` and `mongorestore` without leaking the password.

The password never reaches the process table. MongoDB's tools read it from a
configuration file, so it is written to a private file for the length of one
call and removed afterwards, rather than passed in argv where every process on
the host could read it.

This is the only place in the platform that shells out. It exists because
`mongodump` is the supported way to take a consistent copy of a MongoDB
database, and reimplementing it over the driver would be a worse backup.
"""

import asyncio
import json
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from app.core.config import Settings

DUMP: Final = "mongodump"
RESTORE: Final = "mongorestore"
# Long enough for a real database, bounded so a hung tool cannot wedge a
# deployment script forever.
DEFAULT_TIMEOUT_SECONDS: Final = 3_600.0
CONFIG_MODE: Final = 0o600
MISSING_TOOL_HINT: Final = (
    "MongoDB Database Tools are not on PATH; rebuild the context-api image "
    "or install mongodump and mongorestore in this operator environment"
)


class ToolNotFoundError(RuntimeError):
    """The MongoDB Database Tools are not installed here."""


class ToolFailedError(RuntimeError):
    """The tool ran and did not succeed."""


@dataclass(frozen=True, slots=True)
class ToolResult:
    """What one invocation produced."""

    stdout: str
    stderr: str


class MongoTools:
    """Invoke the MongoDB Database Tools against one deployment."""

    def __init__(
        self, settings: Settings, *, timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS
    ) -> None:
        self._settings = settings
        self._timeout = timeout_seconds

    def connection_arguments(self, *, database: str | None = None) -> list[str]:
        """Return the arguments that address this deployment, minus the secret.

        `--authenticationDatabase` is the application's own database because
        that is where its user was created; the application never holds root
        credentials, and a backup is taken as the application user.
        """
        arguments = [
            f"--uri={self._settings.mongodb_uri}",
            f"--username={self._settings.mongodb_username}",
            f"--authenticationDatabase={self._settings.mongodb_database}",
        ]
        if database is not None:
            arguments.append(f"--db={database}")
        return arguments

    async def run(self, tool: str, arguments: list[str]) -> ToolResult:
        """Run one tool, with the password handed over out of band."""
        if shutil.which(tool) is None:
            raise ToolNotFoundError(f"{tool} was not found")
        password = self._settings.mongodb_password_value()

        directory = Path(tempfile.mkdtemp(prefix="contextctl-"))
        configuration = directory / "mongo.yaml"
        try:
            # Created empty at 0600 before anything is written, so the secret is
            # never briefly readable by another user on the host.
            configuration.touch(mode=CONFIG_MODE)
            # JSON string syntax is valid YAML and safely represents `#`, `:`,
            # quotes, and newlines that would change an unquoted YAML scalar.
            configuration.write_text(
                f"password: {json.dumps(password)}\n",
                encoding="utf-8",
            )
            process = await asyncio.create_subprocess_exec(
                tool,
                *arguments,
                f"--config={configuration}",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                async with asyncio.timeout(self._timeout):
                    out, errors = await process.communicate()
            except TimeoutError as error:
                process.kill()
                await process.communicate()
                raise ToolFailedError(
                    f"{tool} did not finish within {self._timeout:.0f}s"
                ) from error
        except OSError as error:
            raise ToolFailedError(f"{tool} could not be started: {error}") from error
        finally:
            shutil.rmtree(directory, ignore_errors=True)

        decoded = ToolResult(
            stdout=out.decode("utf-8", "replace"),
            stderr=errors.decode("utf-8", "replace"),
        )
        if process.returncode != 0:
            raise ToolFailedError(f"{tool} failed: {summarize(decoded.stderr)}")
        return decoded


def summarize(errors: str) -> str:
    """Return the tool's last useful line, without its progress noise."""
    lines = [line.strip() for line in errors.splitlines()]
    meaningful = [line for line in lines if line and "%" not in line]
    return meaningful[-1] if meaningful else "no output"
