#!/usr/bin/env bash
# Only disposable, uniquely named stacks; never reads deployment .env or restarts live services.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
export V6_REPORT_DIR="${V6_REPORT_DIR:-$(mktemp -d /tmp/acp-v6-deployment-evidence.XXXXXXXX)}"
mkdir -p "$V6_REPORT_DIR"
docker build --target console -t acp-v6-console:phase15 . >"$V6_REPORT_DIR/console-build.txt" 2>&1
docker build --target production -t acp-v6-api:phase15 . >"$V6_REPORT_DIR/api-build.txt" 2>&1
export V6_OIDC_TESTS=1
if [[ "${V6_DASHBOARD_HOSTING:-0}" != "1" ]]; then export V6_DEPLOYMENT_TESTS=1; fi
exec bash scripts/test-v6-isolation.sh "$@"
