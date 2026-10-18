#!/bin/bash
set -euo pipefail
export KC_DB_PASSWORD="$(cat /run/secrets/keycloak-db-password)"
# Bootstrap is an explicit commissioning overlay, absent during ordinary operation.
if [[ "${KEYCLOAK_BOOTSTRAP_ENABLED:-false}" == "true" ]]; then
    : "${KC_BOOTSTRAP_ADMIN_USERNAME:?Set the initial provider operator}"
    export KC_BOOTSTRAP_ADMIN_PASSWORD="$(cat /run/secrets/keycloak-bootstrap-password)"
    test -n "$KC_BOOTSTRAP_ADMIN_PASSWORD"
else
    unset KC_BOOTSTRAP_ADMIN_USERNAME KC_BOOTSTRAP_ADMIN_PASSWORD
fi
exec /opt/keycloak/bin/kc.sh start "$@"
