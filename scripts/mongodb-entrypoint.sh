#!/bin/bash
set -Eeuo pipefail

source_keyfile=/run/secrets/mongodb-keyfile
runtime_keyfile=/run/mongodb-internal/keyfile

if [[ ! -s "${source_keyfile}" ]]; then
    echo "MongoDB keyfile secret is missing or empty" >&2
    exit 1
fi

install -d -m 0700 -o mongodb -g mongodb "$(dirname "${runtime_keyfile}")"
install -m 0400 -o mongodb -g mongodb "${source_keyfile}" "${runtime_keyfile}"

exec /usr/local/bin/docker-entrypoint.sh "$@"
