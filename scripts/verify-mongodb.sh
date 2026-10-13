#!/bin/bash
set -Eeuo pipefail

root_password="$(<"${MONGODB_ROOT_PASSWORD_FILE}")"
app_password="$(<"${MONGODB_APP_PASSWORD_FILE}")"
common=(--quiet --host "${MONGODB_HOST}" --port "${MONGODB_PORT}")

mongosh "${common[@]}" \
    --username "${MONGODB_ROOT_USERNAME}" \
    --password "${root_password}" \
    --authenticationDatabase admin \
    --eval 'quit(db.getSiblingDB("admin").runCommand({usersInfo: 1}).ok === 1 ? 0 : 1)' \
    admin >/dev/null

if mongosh "${common[@]}" \
    --eval 'quit(db.getSiblingDB("admin").runCommand({usersInfo: 1}).ok === 1 ? 0 : 1)' \
    admin >/dev/null 2>&1; then
    echo "Unauthenticated administrative access unexpectedly succeeded" >&2
    exit 1
fi

export MONGODB_DATABASE MONGODB_REPLICA_SET
mongosh "${common[@]}" \
    --username "${MONGODB_APP_USERNAME}" \
    --password "${app_password}" \
    --authenticationDatabase "${MONGODB_DATABASE}" \
    --eval '
        const appDb = db.getSiblingDB(process.env.MONGODB_DATABASE);
        const status = db.getSiblingDB("admin").runCommand({replSetGetStatus: 1});
        if (status.ok !== 1 || status.set !== process.env.MONGODB_REPLICA_SET || status.myState !== 1) {
            quit(2);
        }
        const probe = appDb.getCollection("_phase1_connection_probe");
        const inserted = probe.insertOne({checked: true});
        if (!inserted.acknowledged) {
            quit(3);
        }
        probe.deleteOne({_id: inserted.insertedId});
        probe.drop();
    ' "${MONGODB_DATABASE}" >/dev/null

if mongosh "${common[@]}" \
    --username "${MONGODB_APP_USERNAME}" \
    --password "${app_password}" \
    --authenticationDatabase "${MONGODB_DATABASE}" \
    --eval 'quit(db.getSiblingDB("admin").runCommand({usersInfo: 1}).ok === 1 ? 0 : 1)' \
    admin >/dev/null 2>&1; then
    echo "Application user unexpectedly has administrative user access" >&2
    exit 1
fi

unset root_password app_password
echo "Authentication, replica-set readiness, and application-user access verified."
