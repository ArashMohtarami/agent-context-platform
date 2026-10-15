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
cleanup() {
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
export V6_UNIT_EVIDENCE_XML="$report_dir/unit.xml"
export V6_REAL_EVIDENCE_XML="$report_dir/real-rehearsal.xml"
"$python" -m pytest -q tests/unit --junitxml="$V6_UNIT_EVIDENCE_XML"
real_tests=(tests/integration/test_v6_tenant_isolation.py
            tests/integration/test_v6_mcp_isolation.py
            tests/integration/test_v6_job_isolation.py)
# The first report supplies real evidence to the explicit operator cutover
# rehearsal. The complete selector is then run without skips or deselection.
"$python" -m pytest -q "${real_tests[@]}" -k 'not operator_cutover' \
    --junitxml="$V6_REAL_EVIDENCE_XML"
"$python" -m pytest -q "${real_tests[@]}" --junitxml="$report_dir/real.xml"
printf 'V6 isolation reports: %s\n' "$report_dir"
