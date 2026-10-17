#!/usr/bin/env bash
# Phase 3 reuses the disposable Phase 2 replica set and preserves its full gate.
set -euo pipefail
export V6_LOCAL_IDENTITY_TESTS=1
exec "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/test-v6-isolation.sh"
