#!/bin/bash
set -Eeuo pipefail
umask 077

config_template=/etc/mongot/mongot.yml.template
runtime_config=/tmp/mongot.yml
password_file=/run/secrets/mongot-sync-password
runtime_password_file=/tmp/mongot-sync-password

if [[ -z "${MONGOT_SYNC_USERNAME:-}" ]]; then
    echo "Required variable MONGOT_SYNC_USERNAME is not set" >&2
    exit 1
fi
if [[ ! "${MONGOT_SYNC_USERNAME}" =~ ^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$ ]]; then
    echo "MONGOT_SYNC_USERNAME contains unsupported characters" >&2
    exit 1
fi
if [[ ! "${MONGOT_LOG_LEVEL:-INFO}" =~ ^(TRACE|DEBUG|INFO|WARN|ERROR)$ ]]; then
    echo "MONGOT_LOG_LEVEL is invalid" >&2
    exit 1
fi
if [[ ! -s "${password_file}" ]]; then
    echo "The mongot synchronization password secret is missing or empty" >&2
    exit 1
fi
if [[ -z "$(tail -c 1 "${password_file}")" ]]; then
    echo "The mongot synchronization password must not end with a newline" >&2
    exit 1
fi

cp "${password_file}" "${runtime_password_file}"
chmod 0400 "${runtime_password_file}"

sed \
    -e "s/__MONGOT_SYNC_USERNAME__/${MONGOT_SYNC_USERNAME}/g" \
    -e "s/__MONGOT_LOG_LEVEL__/${MONGOT_LOG_LEVEL:-INFO}/g" \
    "${config_template}" >"${runtime_config}"

exec /mongot-community/mongot --config="${runtime_config}"
