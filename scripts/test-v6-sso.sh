#!/usr/bin/env bash
set -euo pipefail
export V6_OIDC_TESTS=1
export V6_LOCAL_IDENTITY_TESTS=1
exec "$(dirname "${BASH_SOURCE[0]}")/test-v6-isolation.sh" "$@"
