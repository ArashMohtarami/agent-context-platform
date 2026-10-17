#!/usr/bin/env bash
# Disposable replica set + real mongot. Never reads the deployment's .env.
set -euo pipefail
umask 077
export V6_REPOSITORY_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$V6_REPOSITORY_ROOT"
export V6_TEST_RUN="$(date +%s)-$$"
export V6_TEST_DIR="$(mktemp -d /tmp/acp-v6-phase2.XXXXXXXX)"
report_dir="${V6_REPORT_DIR:-$(mktemp -d /tmp/acp-v6-evidence.XXXXXXXX)}"
mkdir -p "$report_dir"
python="${V6_TEST_PYTHON:-$V6_REPOSITORY_ROOT/.venv/bin/python}"
compose=(docker compose -f tests/infrastructure/v6-isolation.compose.yml)
oidc_compose=(docker compose -f tests/infrastructure/v6-oidc.compose.yml)
cleanup() {
    if [[ "${V6_OIDC_TESTS:-0}" == "1" ]]; then
        docker logs "acp-v6-oidc-keycloak-$V6_TEST_RUN" >"$report_dir/provider.log" 2>&1 || true
        "${oidc_compose[@]}" down --remove-orphans >/dev/null 2>&1 || true
    fi
    "${compose[@]}" down --remove-orphans >/dev/null 2>&1 || true
    rm -rf -- "$V6_TEST_DIR"
}
trap cleanup EXIT
trap 'exit 130' INT TERM
"$python" - <<'PY'
import base64, os, secrets
from pathlib import Path
root = Path(os.environ['V6_TEST_DIR'])
for name in ('root', 'sync'):
    path = root / name
    path.write_text(secrets.token_urlsafe(32))
    path.chmod(0o444)
path = root / 'keyfile'
path.write_bytes(base64.b64encode(secrets.token_bytes(756)))
path.chmod(0o444)
PY
start_stack() {
    "${compose[@]}" up -d mongodb >/dev/null
    container="acp-v6-phase2-mongodb-$V6_TEST_RUN"
    ready=false
    for _ in $(seq 1 90); do
        if docker exec "$container" mongosh --quiet --file /bootstrap.js >/dev/null 2>&1 &&
           docker exec "$container" mongosh --quiet --file /sync.js >/dev/null 2>&1; then
            ready=true
            break
        fi
        sleep 1
    done
    if [[ "$ready" != true ]]; then
        echo 'Disposable replica set did not become ready (credentials withheld).' >&2
        exit 1
    fi
    "${compose[@]}" up -d mongot >/dev/null
    export V6_TEST_MONGODB_CONTAINER="$container"
    endpoint="$("${compose[@]}" port mongodb 27017)"
    export V6_TEST_MONGODB_URI
    V6_TEST_MONGODB_URI="$("$python" - "$endpoint" <<'PY'
import os, sys
from pathlib import Path
from urllib.parse import quote
password = (Path(os.environ['V6_TEST_DIR']) / 'root').read_text()
print('mongodb://v6-test-root:' + quote(password, safe='') + '@' + sys.argv[1] +
      '/?authSource=admin&replicaSet=acp_v6_phase2&directConnection=true')
PY
    )"
}

fresh_stack() {
    "${compose[@]}" down --remove-orphans >/dev/null 2>&1
    start_stack
}

start_stack
if [[ "${V6_SUMMARIES_DEVELOPMENT_ONLY:-0}" == "1" ]]; then
    for target in dashboard_aggregation report_revocation; do
        "$python" -m pytest -q "tests/integration/test_v6_${target}.py" --junitxml="$report_dir/${target}.xml" "$@"
        fresh_stack
    done
    exit 0
fi
if [[ "${V6_MANAGED_AGENTS_DEVELOPMENT_ONLY:-0}" == "1" ]]; then
    for target in managed_client_revocation credential_delivery service_account_reviews; do
        "$python" -m pytest -q "tests/integration/test_v6_${target}.py" --junitxml="$report_dir/${target}.xml" "$@"
        fresh_stack
    done
    exit 0
fi
if [[ "${V6_MANAGEMENT_DEVELOPMENT_ONLY:-0}" == "1" ]]; then
    "$python" -m pytest -q tests/integration/test_v6_membership_lifecycle.py --junitxml="$report_dir/membership-development.xml" "$@"
    fresh_stack
    "$python" -m pytest -q tests/integration/test_v6_access_request_races.py --junitxml="$report_dir/access-requests-development.xml" "$@"
    exit 0
fi
if [[ "${V6_POLICY_DEVELOPMENT_ONLY:-0}" == "1" ]]; then
    "$python" -m pytest -q tests/integration/test_v6_policy_fencing.py --junitxml="$report_dir/policy-development.xml" "$@"
    exit 0
fi
if [[ "${V6_OIDC_TESTS:-0}" == "1" ]]; then
    export V6_OIDC_PORT
    V6_OIDC_PORT="$("$python" - <<'PY'
import socket
with socket.socket() as listener:
    listener.bind(('127.0.0.1', 0))
    print(listener.getsockname()[1])
PY
    )"
    "$python" tests/infrastructure/v6_oidc_prepare.py
    "${oidc_compose[@]}" up -d >/dev/null
    export V6_OIDC_REGISTRATIONS_FILE="$V6_TEST_DIR/oidc/registrations.json"
    "$python" - <<'PY'
import os, ssl, time, urllib.request
from pathlib import Path
root = Path(os.environ['V6_TEST_DIR']) / 'oidc'
context = ssl.create_default_context(cafile=root / 'server.crt')
url = 'https://127.0.0.1:' + os.environ['V6_OIDC_PORT'] + '/realms/v6-org-a/.well-known/openid-configuration'
for attempt in range(180):
    try:
        with urllib.request.urlopen(url, context=context, timeout=2) as response:
            assert response.status == 200
        break
    except Exception:
        if attempt == 179:
            raise SystemExit('Disposable HTTPS Keycloak did not become ready (secrets withheld).')
        time.sleep(1)
PY
    if [[ "${V6_SSO_DEVELOPMENT_ONLY:-0}" == "1" ]]; then
        "$python" -m pytest -q tests/integration/test_v6_oidc_provider.py \
            tests/integration/test_v6_org_switch.py --junitxml="$report_dir/sso-development.xml" "$@"
        exit 0
    fi
fi
export V6_UNIT_EVIDENCE_XML="$report_dir/unit.xml"
export V6_REAL_EVIDENCE_XML="$report_dir/real-rehearsal.xml"
if [[ "${V6_IDENTITY_DEVELOPMENT_ONLY:-0}" == "1" ]]; then
    echo 'Development-only identity selector; this does not establish the Phase 2 gate.'
    "$python" -m pytest -q tests/integration/test_v6_local_identity.py \
        --junitxml="$report_dir/identity-development.xml"
    exit 0
fi
"$python" -m pytest -q tests/unit --junitxml="$V6_UNIT_EVIDENCE_XML"
real_tests=(tests/integration/test_v6_tenant_isolation.py
            tests/integration/test_v6_mcp_isolation.py
            tests/integration/test_v6_job_isolation.py)
# The first report supplies real evidence to the explicit operator cutover
# rehearsal. The complete selector is then run without skips or deselection.
"$python" -m pytest -q "${real_tests[@]}" -k 'not operator_cutover' \
    --junitxml="$V6_REAL_EVIDENCE_XML"
# Each selector gets fresh tmpfs storage and search workers. Reusing the same
# small replica set across dozens of create/drop/index fixtures can exhaust its
# cgroup with retained engine state; that is not authentication evidence.
fresh_stack
"$python" -m pytest -q "${real_tests[@]}" --junitxml="$report_dir/real.xml"
if [[ "${V6_LOCAL_IDENTITY_TESTS:-0}" == "1" ]]; then
    fresh_stack
    "$python" -m pytest -q tests/integration/test_v6_local_identity.py \
        --junitxml="$report_dir/identity.xml"
fi
if [[ "${V6_OIDC_TESTS:-0}" == "1" ]]; then
    fresh_stack
    "$python" -m pytest -q tests/integration/test_v6_oidc_provider.py \
        tests/integration/test_v6_org_switch.py --junitxml="$report_dir/sso.xml"
fi
if [[ "${V6_POLICY_TESTS:-0}" == "1" ]]; then
    fresh_stack
    "$python" -m pytest -q tests/integration/test_v6_policy_fencing.py --junitxml="$report_dir/policy.xml"
fi
if [[ "${V6_MANAGEMENT_TESTS:-0}" == "1" ]]; then
    fresh_stack
    "$python" -m pytest -q tests/integration/test_v6_membership_lifecycle.py --junitxml="$report_dir/membership.xml"
    fresh_stack
    "$python" -m pytest -q tests/integration/test_v6_access_request_races.py --junitxml="$report_dir/access-requests.xml"
fi
if [[ "${V6_MANAGED_AGENTS_TESTS:-0}" == "1" ]]; then
    for target in managed_client_revocation credential_delivery service_account_reviews; do
        fresh_stack
        "$python" -m pytest -q "tests/integration/test_v6_${target}.py" --junitxml="$report_dir/${target}.xml" "$@"
    done
fi
printf 'V6 isolation reports: %s\n' "$report_dir"
if [[ "${V6_SUMMARIES_TESTS:-0}" == "1" ]]; then
    for target in dashboard_aggregation report_revocation; do
        fresh_stack
        "$python" -m pytest -q "tests/integration/test_v6_${target}.py" --junitxml="$report_dir/${target}.xml" "$@"
    done
fi
