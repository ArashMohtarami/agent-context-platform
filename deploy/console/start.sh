#!/bin/sh
set -eu
# Substitute only installation values, never nginx variables or VITE_* secrets.
: "${CONSOLE_SERVER_NAME:?Set a DNS hostname or IPv4 address matching the TLS SAN}"
: "${CONSOLE_UPSTREAM:=context-api:8000}"
case "$CONSOLE_SERVER_NAME" in ''|*[!a-zA-Z0-9.-]*) echo 'Invalid console hostname' >&2; exit 1;; esac
case "$CONSOLE_UPSTREAM" in ''|*[!a-zA-Z0-9.:-]*) echo 'Invalid console upstream' >&2; exit 1;; esac
export CONSOLE_SERVER_NAME CONSOLE_UPSTREAM
envsubst '${CONSOLE_SERVER_NAME} ${CONSOLE_UPSTREAM}' < /etc/acp-console/nginx.conf.template > /tmp/acp-nginx.conf
exec nginx -c /tmp/acp-nginx.conf -g 'daemon off;'
