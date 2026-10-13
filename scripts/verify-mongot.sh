#!/bin/bash
set -Eeuo pipefail

required_variables=(
    MONGODB_HOST
    MONGODB_PORT
    MONGODB_DATABASE
    MONGODB_ROOT_USERNAME
    MONGODB_APP_USERNAME
    MONGOT_SYNC_USERNAME
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

root_password="$(<"${MONGODB_ROOT_PASSWORD_FILE}")"
app_password="$(<"${MONGODB_APP_PASSWORD_FILE}")"
sync_password="$(<"${MONGOT_SYNC_PASSWORD_FILE}")"
common=(--quiet --host "${MONGODB_HOST}" --port "${MONGODB_PORT}")

MONGOT_HEALTH_HOST=mongot /scripts/mongot-healthcheck.sh

export MONGOT_SYNC_USERNAME
mongosh "${common[@]}" \
    --username "${MONGODB_ROOT_USERNAME}" \
    --password "${root_password}" \
    --authenticationDatabase admin \
    --eval '
        const user = db.getSiblingDB("admin").getUser(process.env.MONGOT_SYNC_USERNAME);
        const valid = user !== null && user.roles.length === 1 &&
            user.roles[0].role === "searchCoordinator" && user.roles[0].db === "admin";
        quit(valid ? 0 : 1);
    ' admin >/dev/null

mongosh "${common[@]}" \
    --username "${MONGOT_SYNC_USERNAME}" \
    --password "${sync_password}" \
    --authenticationDatabase admin \
    --eval 'quit(db.getSiblingDB("admin").runCommand({ping: 1}).ok === 1 ? 0 : 1)' \
    admin >/dev/null

export MONGODB_DATABASE
mongosh "${common[@]}" \
    --username "${MONGODB_APP_USERNAME}" \
    --password "${app_password}" \
    --authenticationDatabase "${MONGODB_DATABASE}" \
    --eval '
        const database = db.getSiblingDB(process.env.MONGODB_DATABASE);
        const probe = database.getCollection("_mongot_health_probe");
        probe.replaceOne({_id: "coordinator"}, {checkedAt: new Date()}, {upsert: true});
        let result;
        try {
            result = database.runCommand({
                aggregate: "_mongot_health_probe",
                pipeline: [{$listSearchIndexes: {}}],
                cursor: {},
            });
        } finally {
            probe.drop();
        }
        quit(result.ok === 1 ? 0 : 1);
    ' "${MONGODB_DATABASE}" >/dev/null

unset root_password app_password sync_password
echo "mongot health, synchronization identity, and mongod coordinator path verified."
