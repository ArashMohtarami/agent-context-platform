#!/bin/bash
set -Eeuo pipefail

health_host=${MONGOT_HEALTH_HOST:-127.0.0.1}
health_port=${MONGOT_HEALTH_PORT:-8080}

exec 3<>"/dev/tcp/${health_host}/${health_port}"
printf 'GET /health HTTP/1.1\r\nHost: %s\r\nConnection: close\r\n\r\n' "${health_host}" >&3
response="$(cat <&3)"
exec 3<&-
exec 3>&-

[[ "${response}" == *" 200 "* && "${response}" == *'"status":"SERVING"'* ]]
