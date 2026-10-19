#!/usr/bin/env bash
# Full gate only in new tmpfs stacks. No deployment .env or live service access.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
: "${V6_ACCEPTANCE_MODEL_CACHE:?Set a private cache containing the reviewed real embedding model}"
: "${PLAYWRIGHT_CHROMIUM_EXECUTABLE:?Set the supported installed Chrome executable}"
# Never let a shell's earlier development selector turn this command green
# after running a subset. Workload overrides belong to the development runner.
for selector in "${!V6_@}"; do
    if [[ "$selector" == V6_*_DEVELOPMENT_ONLY || "$selector" == V6_*_ADAPTERS_ONLY ||
          "$selector" == V6_DASHBOARD_BROWSER_TESTS || "$selector" == V6_BROWSER_SERVE_ONLY ||
          "$selector" == V6_DEPLOYMENT_TESTS ]]; then
        if [[ "${!selector}" == "1" ]]; then
            echo 'Complete acceptance refuses development selectors.' >&2
            exit 2
        fi
    fi
done
export V6_PERFORMANCE_EVENTS=1000000
export V6_PERFORMANCE_CHUNKS=100000
export V6_REPORT_DIR="${V6_REPORT_DIR:-$(mktemp -d /tmp/acp-v6-release-evidence.XXXXXXXX)}"
mkdir -p "$V6_REPORT_DIR"
shopt -s nullglob dotglob
previous=("$V6_REPORT_DIR"/*)
if [[ "${#previous[@]}" -gt 0 ]]; then
    echo 'Complete acceptance requires a fresh evidence directory.' >&2
    exit 2
fi
shopt -u nullglob dotglob
python="${V6_TEST_PYTHON:-$PWD/.venv/bin/python}"
"$python" tests/infrastructure/v6_release.py --seal "$V6_REPORT_DIR"
docker build --target console -t acp-v6-console:phase15 . >"$V6_REPORT_DIR/console-build.txt" 2>&1
docker build --target production -t acp-v6-api:phase15 . >"$V6_REPORT_DIR/api-build.txt" 2>&1
export V6_FULL_ACCEPTANCE=1 V6_OIDC_TESTS=1 V6_DASHBOARD_HOSTING=1
export V6_TEST_MONGODB_MEMORY_LIMIT=3g
export V6_TEST_VOLUME_STORAGE=1
exec bash scripts/test-v6-isolation.sh "$@"
