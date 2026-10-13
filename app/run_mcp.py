"""Entry point for the MCP server process.

Run with `python -m app.run_mcp`. The server speaks MCP over stdin and stdout,
so logs go to stderr: writing anything else to stdout would corrupt the
protocol stream a client is reading.
"""

import asyncio
import logging
import sys

from app.core.config import get_mcp_settings
from app.core.logging import configure_logging
from app.mcp.server import run_stdio


def main() -> None:
    """Serve MCP over stdio until the client closes the connection."""
    settings = get_mcp_settings()
    configure_logging(settings.log_level, stream=sys.stderr)
    try:
        asyncio.run(run_stdio(settings))
    except KeyboardInterrupt:  # pragma: no cover - interactive interruption
        logging.getLogger("agent_context.mcp").info("mcp_server_stopped")


if __name__ == "__main__":
    main()
