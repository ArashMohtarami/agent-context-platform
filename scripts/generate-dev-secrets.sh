#!/bin/bash
set -Eeuo pipefail
umask 077

mkdir -p secrets
chmod 0700 secrets

if [[ ! -s secrets/mongodb-root-password ]]; then
    openssl rand -base64 48 >secrets/mongodb-root-password
fi
if [[ ! -s secrets/mongodb-app-password ]]; then
    openssl rand -base64 48 >secrets/mongodb-app-password
fi
if [[ ! -s secrets/mongodb-keyfile ]]; then
    openssl rand -base64 756 >secrets/mongodb-keyfile
fi
if [[ ! -s secrets/mongot-sync-password ]]; then
    sync_password="$(openssl rand -base64 48)"
    printf '%s' "${sync_password}" >secrets/mongot-sync-password
    unset sync_password
fi
# The ignored directory remains owner-only. Files are readable in containers after the
# official MongoDB entrypoint drops privileges to its dedicated `mongodb` user.
chmod 0444 \
    secrets/mongodb-root-password \
    secrets/mongodb-app-password \
    secrets/mongodb-keyfile \
    secrets/mongot-sync-password

if [[ ! -e .env ]]; then
    root_username="mongodb_root_$(openssl rand -hex 6)"
    app_username="context_app_$(openssl rand -hex 6)"
    sync_username="mongot_sync_$(openssl rand -hex 6)"
    cat >.env <<EOF
APP_ENV=development
APP_NAME=Agent Context Platform
LOG_LEVEL=INFO
API_PORT=8000
MONGODB_URI=mongodb://mongodb:27017
MONGODB_DATABASE=agent_context
MONGODB_REPLICA_SET=rs0
MONGODB_APP_NAME=agent-context-platform
MONGODB_ROOT_USERNAME=${root_username}
MONGODB_APP_USERNAME=${app_username}
MONGOT_SYNC_USERNAME=${sync_username}
MONGODB_ROOT_PASSWORD_SECRET=./secrets/mongodb-root-password
MONGODB_APP_PASSWORD_SECRET=./secrets/mongodb-app-password
MONGODB_KEYFILE_SECRET=./secrets/mongodb-keyfile
MONGOT_SYNC_PASSWORD_SECRET=./secrets/mongot-sync-password
MONGOT_LOG_LEVEL=INFO
EOF
    chmod 0600 .env
fi

if ! grep -q '^MONGOT_SYNC_USERNAME=' .env; then
    sync_username="mongot_sync_$(openssl rand -hex 6)"
    {
        printf '\nMONGOT_SYNC_USERNAME=%s\n' "${sync_username}"
        printf 'MONGOT_SYNC_PASSWORD_SECRET=./secrets/mongot-sync-password\n'
        printf 'MONGOT_LOG_LEVEL=INFO\n'
    } >>.env
    chmod 0600 .env
fi

if ! grep -q '^EMBEDDING_MODEL=' .env; then
    {
        printf '\nEMBEDDING_ENABLED=true\n'
        printf 'EMBEDDING_MODEL=sentence-transformers/all-MiniLM-L6-v2\n'
        printf 'EMBEDDING_DIMENSIONS=384\n'
        printf 'EMBEDDING_DEVICE=cpu\n'
        printf 'EMBEDDING_ALLOW_DOWNLOADS=true\n'
    } >>.env
    chmod 0600 .env
fi

echo "Local MongoDB, mongot, and embedding configuration and secret files are present."
