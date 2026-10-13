"""Production entry point that configures logging before Uvicorn starts.

One worker on purpose. Each worker loads its own copy of the embedding model,
so a second one doubles the resident memory before it serves a single extra
request — and the work this process does is I/O-bound on MongoDB, which one
event loop handles well. Scale by giving the container more CPU, or by running
more containers behind a proxy, not by adding workers inside one.
"""

import uvicorn

from app.core.config import Settings, get_settings
from app.core.logging import configure_logging

_RUN_TARGET = "app.main:app"


def options(settings: Settings) -> dict[str, object]:
    """Build the server options, separately so a test can read them."""
    return {
        "host": "0.0.0.0",  # noqa: S104 - container ingress is constrained by Compose networking.
        "port": 8000,
        "log_config": None,
        "log_level": settings.log_level.lower(),
        # The request middleware emits one structured access event per request.
        "access_log": False,
        # On SIGTERM, stop accepting and give what is in flight this long to
        # finish. Without a limit a shutdown waits for the slowest request
        # there is, so a deploy can hang on one call; with it, an orchestrator's
        # own kill timeout is never the thing that ends the process.
        "timeout_graceful_shutdown": settings.api_shutdown_timeout_seconds,
        # A ceiling on requests in flight. Past it the server answers 503
        # rather than accepting work it will not finish: retrieval holds an
        # embedding model and a database connection, so unbounded acceptance
        # turns a burst into an out-of-memory kill instead of a queue.
        "limit_concurrency": settings.api_max_concurrent_requests,
        # Bounded so a client that opens connections and never speaks cannot
        # hold them open indefinitely.
        "timeout_keep_alive": settings.api_keep_alive_seconds,
        # This API answers programs. Naming the server and its version in every
        # response tells a scanner what to look up and helps nobody else.
        "server_header": False,
        # Forwarded TLS facts are accepted only from an operator-configured
        # proxy range.  The remote MCP edge relies on the resulting ASGI scheme
        # and never trusts X-Forwarded-Proto by itself.
        "proxy_headers": settings.api_proxy_headers,
        "forwarded_allow_ips": settings.api_forwarded_allow_ips or "",
    }


def main() -> None:
    """Start Uvicorn with environment-driven structured logging."""
    settings = get_settings()
    configure_logging(settings.log_level)
    uvicorn.run(_RUN_TARGET, **options(settings))  # type: ignore[arg-type]


if __name__ == "__main__":
    main()
