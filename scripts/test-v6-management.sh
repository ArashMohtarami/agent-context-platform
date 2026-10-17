#!/usr/bin/env bash
# Full prerequisites and the real human-management transaction gate; disposable only.
set -euo pipefail
export V6_LOCAL_IDENTITY_TESTS=1
export V6_OIDC_TESTS=1
export V6_POLICY_TESTS=1
export V6_MANAGEMENT_TESTS=1
export V6_TEST_MONGODB_MEMORY_LIMIT="${V6_TEST_MONGODB_MEMORY_LIMIT:-3g}"
exec "$(dirname "${BASH_SOURCE[0]}")/test-v6-isolation.sh" "$@"
