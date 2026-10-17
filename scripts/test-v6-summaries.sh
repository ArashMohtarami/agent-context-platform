#!/usr/bin/env bash
# Complete prerequisites plus the real Phase8 aggregation/report gate.
set -euo pipefail
export V6_SUMMARIES_TESTS=1
exec "$(dirname "${BASH_SOURCE[0]}")/test-v6-managed-agents.sh" "$@"
