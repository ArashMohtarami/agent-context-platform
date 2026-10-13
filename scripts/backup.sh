#!/bin/bash
# Take one backup, verify it, and apply retention.
#
# This is what a timer or a cron entry runs. It is a wrapper rather than an
# implementation: the decisions live in `contextctl`, and this exists so a
# scheduler has one command, one working directory, and one exit code.
#
# Exit codes are the CLI's: 0 succeeded, 1 failed, 3 the platform is not there.
# A scheduler should alert on any non-zero, and especially on a run that has
# not happened at all — a backup job that stops firing is silent by nature.
set -Eeuo pipefail

project_directory="${PROJECT_DIRECTORY:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
compose_service="${COMPOSE_SERVICE:-context-api}"
contextctl=(docker compose exec -T "${compose_service}" python -m app.cli.contextctl)

cd "${project_directory}"

# Inside the platform, because a backup reaches MongoDB directly and under
# Compose MongoDB is on an internal network with no host port.
if [[ -n "${CONTEXTCTL_LOCAL:-}" ]]; then
    contextctl=(python -m app.cli.contextctl)
fi

echo "Backing up ${project_directory} at $(date --iso-8601=seconds)"
"${contextctl[@]}" backup

# `contextctl backup` checks the SHA-256 and dry-run restore plan before it
# applies retention, so an invalid new archive cannot evict a known older one.
# Proving the archive actually materializes every document is the weekly
# `contextctl backups verify --restore` job.

echo "Backup complete."
