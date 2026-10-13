#!/bin/bash
set -Eeuo pipefail

password_file=/run/secrets/mongodb-root-password
if [[ ! -s "${password_file}" ]]; then
    exit 1
fi

root_password="$(<"${password_file}")"

exec mongosh \
    --quiet \
    --host 127.0.0.1 \
    --port 27017 \
    --username "${MONGO_INITDB_ROOT_USERNAME}" \
    --password "${root_password}" \
    --authenticationDatabase admin \
    --eval '
        const status = db.getSiblingDB("admin").runCommand({replSetGetStatus: 1});
        quit(status.ok === 1 && status.myState === 1 ? 0 : 1);
    ' \
    admin >/dev/null
