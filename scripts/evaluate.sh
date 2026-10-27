#!/bin/sh
# Disposable evaluation only. Python discards inherited deployment configuration.
set -eu
evaluation_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
exec "$evaluation_root/.venv/bin/python" -I "$evaluation_root/scripts/evaluation_lifecycle.py" "$@"
