"""MongoDB latency, measured by the driver rather than by the callers.

PyMongo already times every command it sends and publishes the result. Reading
that is the whole implementation: every query, write, aggregation, and index
command is covered at once, including the ones inside the driver itself, and no
repository has to remember to wrap anything. A timer added around a call site
would measure fewer commands and drift the moment somebody added a query.

Only two things are taken from an event: the command's name and how long it
took. A started event also carries the command document — the filter, the
pipeline, the documents being written — and a failed event carries the server's
error reply. Neither is read here. That is what makes this a latency
measurement rather than a second copy of the database in the log.
"""

import logging
from typing import Final

from pymongo import monitoring

from app.observability.instruments import record_mongodb_command

MICROSECONDS: Final = 1_000_000.0

logger = logging.getLogger("agent_context.mongodb")


class CommandMetricsListener(monitoring.CommandListener):
    """Record the duration of every command the driver sends.

    Nothing raised here may escape. The listener runs inside the driver's own
    path, so a failure in a measurement would become a failure of the query
    being measured — which is precisely backwards.
    """

    def started(self, event: monitoring.CommandStartedEvent) -> None:
        """Ignore the start of a command.

        Required by the driver's interface and deliberately empty: a started
        event's `command` is the full document being sent, and there is nothing
        this package could correctly do with it.
        """

    def succeeded(self, event: monitoring.CommandSucceededEvent) -> None:
        """Record how long one successful command took."""
        self._record(event.command_name, event.duration_micros, failed=False)

    def failed(self, event: monitoring.CommandFailedEvent) -> None:
        """Record one failed command, without the server's error reply.

        The reply can quote the operation that failed, so it is left where the
        driver raised it: the repository turns that into an application error
        with a message written for a caller, and that is what gets logged.
        """
        self._record(event.command_name, event.duration_micros, failed=True)
        logger.warning(
            "mongodb_command_failed",
            extra={
                "db_command": event.command_name,
                "duration_ms": round(event.duration_micros / 1_000, 3),
            },
        )

    @staticmethod
    def _record(command: str, duration_micros: int, *, failed: bool) -> None:
        """Record one command, surviving anything that goes wrong doing so."""
        try:
            record_mongodb_command(
                command=command,
                seconds=duration_micros / MICROSECONDS,
                failed=failed,
            )
        # Deliberately broad. This runs inside the driver, where an exception
        # would surface as a database failure rather than as the measurement
        # problem it actually is.
        except Exception:
            logger.debug("mongodb_metric_not_recorded", exc_info=True)
