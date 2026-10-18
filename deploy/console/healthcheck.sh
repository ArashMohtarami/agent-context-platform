#!/bin/sh
set -eu
test -s /usr/share/nginx/html/console/index.html
# Loopback-only process readiness; external clients must verify the TLS chain.
exec wget -q --no-check-certificate --header "Host: ${CONSOLE_SERVER_NAME}" -O /dev/null https://127.0.0.1:8443/health/ready
