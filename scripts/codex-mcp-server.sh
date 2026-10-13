#!/usr/bin/env bash
# Launch the MCP server for an MCP client such as Codex.
#
# A client spawns this with an environment it controls, from a working
# directory it chooses, so nothing here depends on either: the repository root
# is resolved from this script's own location, and the interpreter is the
# project's virtual environment when one exists.
#
# Everything on stdout belongs to the MCP protocol. Diagnostics go to stderr.
set -Eeuo pipefail

script_directory="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
repository_root="$(cd -- "${script_directory}/.." && pwd)"

if [[ -x "${repository_root}/.venv/bin/python" ]]; then
    interpreter="${repository_root}/.venv/bin/python"
elif command -v python3 >/dev/null 2>&1; then
    interpreter="$(command -v python3)"
elif command -v python >/dev/null 2>&1; then
    interpreter="$(command -v python)"
else
    echo "no Python interpreter found for the MCP server" >&2
    exit 1
fi

cd -- "${repository_root}"
# PYTHONPATH is set explicitly so the server runs even when the client starts
# it from somewhere else and the package is not installed.
export PYTHONPATH="${repository_root}${PYTHONPATH:+:${PYTHONPATH}}"
exec "${interpreter}" -m app.run_mcp
