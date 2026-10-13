"""What a `contextctl` command can fail with, and what it exits with.

Exit codes are part of the interface: a script pipes them into `&&` and `||`,
so they distinguish the three things an operator can do something about —
the command was wrong, the operation failed, or the platform is not there.
"""

from typing import Final

# 0 and 2 are conventional and are produced by success and by argparse. The
# rest are ours, and are documented in docs/cli.md.
EXIT_OK: Final = 0
EXIT_FAILED: Final = 1
EXIT_USAGE: Final = 2
EXIT_UNAVAILABLE: Final = 3

EXIT_CODES: Final[dict[int, str]] = {
    EXIT_OK: "the command succeeded",
    EXIT_FAILED: "the command ran and did not succeed",
    EXIT_USAGE: "the arguments were wrong",
    EXIT_UNAVAILABLE: "the platform could not be reached, or is not ready",
}


class CliError(Exception):
    """A failure worth reporting to whoever ran the command.

    Everything a user sees is one of these. An unexpected exception is a defect
    in this tool and is reported as such rather than printed raw, because its
    text is written for a traceback and can name a connection string.
    """

    exit_code: int = EXIT_FAILED

    def __init__(self, message: str, *, hint: str | None = None) -> None:
        super().__init__(message)
        self.hint = hint


class UsageError(CliError):
    """The command was asked for something it cannot do as written."""

    exit_code = EXIT_USAGE


class UnavailableError(CliError):
    """The platform could not be reached, or is not ready to answer."""

    exit_code = EXIT_UNAVAILABLE
