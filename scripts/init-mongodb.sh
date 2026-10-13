#!/bin/bash
set -Eeuo pipefail

required_variables=(
    MONGODB_HOST
    MONGODB_PORT
    MONGODB_REPLICA_SET
    MONGODB_ROOT_USERNAME
    MONGODB_APP_USERNAME
    MONGOT_SYNC_USERNAME
    MONGODB_DATABASE
    MONGODB_ROOT_PASSWORD_FILE
    MONGODB_APP_PASSWORD_FILE
    MONGOT_SYNC_PASSWORD_FILE
)

for variable_name in "${required_variables[@]}"; do
    if [[ -z "${!variable_name:-}" ]]; then
        echo "Required variable ${variable_name} is not set" >&2
        exit 1
    fi
done

if [[ ! -s "${MONGODB_ROOT_PASSWORD_FILE}" || ! -s "${MONGODB_APP_PASSWORD_FILE}" ||
    ! -s "${MONGOT_SYNC_PASSWORD_FILE}" ]]; then
    echo "A required MongoDB password secret is missing or empty" >&2
    exit 1
fi

root_password="$(<"${MONGODB_ROOT_PASSWORD_FILE}")"
export MONGODB_APP_PASSWORD
MONGODB_APP_PASSWORD="$(<"${MONGODB_APP_PASSWORD_FILE}")"
export MONGOT_SYNC_PASSWORD
MONGOT_SYNC_PASSWORD="$(<"${MONGOT_SYNC_PASSWORD_FILE}")"

root_mongosh=(
    mongosh
    --quiet
    --host "${MONGODB_HOST}"
    --port "${MONGODB_PORT}"
    --username "${MONGODB_ROOT_USERNAME}"
    --password "${root_password}"
    --authenticationDatabase admin
)

for _attempt in {1..60}; do
    if "${root_mongosh[@]}" --eval 'quit(db.adminCommand({ping: 1}).ok === 1 ? 0 : 1)' admin \
        >/dev/null 2>&1; then
        break
    fi
    sleep 2
done

if ! "${root_mongosh[@]}" --eval 'quit(db.adminCommand({ping: 1}).ok === 1 ? 0 : 1)' admin \
    >/dev/null 2>&1; then
    echo "MongoDB did not become reachable for authenticated bootstrap" >&2
    exit 1
fi

"${root_mongosh[@]}" admin /scripts/init-replica-set.js

for _attempt in {1..60}; do
    if "${root_mongosh[@]}" --eval '
        const status = db.getSiblingDB("admin").runCommand({replSetGetStatus: 1});
        quit(status.ok === 1 && status.myState === 1 ? 0 : 1);
    ' admin >/dev/null 2>&1; then
        break
    fi
    sleep 2
done

if ! "${root_mongosh[@]}" --eval '
    const status = db.getSiblingDB("admin").runCommand({replSetGetStatus: 1});
    quit(status.ok === 1 && status.myState === 1 ? 0 : 1);
' admin >/dev/null 2>&1; then
    echo "MongoDB replica set did not elect a primary" >&2
    exit 1
fi

"${root_mongosh[@]}" admin /scripts/create-application-user.js
"${root_mongosh[@]}" admin /scripts/create-mongot-user.js

unset MONGODB_APP_PASSWORD MONGOT_SYNC_PASSWORD root_password
echo "MongoDB replica set, application user, and mongot synchronization user are ready."
