#!/bin/bash
# Restore the application database, following the documented order.
#
# Deliberately not a one-command disaster recovery: restoring is step 2 of
# eleven, and the steps around it — stopping the API, rebuilding the search
# indexes, checking what came back — are what make a restore a recovery. This
# does the part that is mechanical and prints what is left to do.
#
# See docs/backup.md for the full procedure.
set -Eeuo pipefail

project_directory="${PROJECT_DIRECTORY:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
compose_service="${COMPOSE_SERVICE:-context-api}"
restore_arguments=("$@")
if [[ ${#restore_arguments[@]} -eq 0 ]]; then
    restore_arguments=(--latest)
fi

cd "${project_directory}"

contextctl=(docker compose run --rm --no-deps -T "${compose_service}" python -m app.cli.contextctl)
if [[ -n "${CONTEXTCTL_LOCAL:-}" ]]; then
    contextctl=(python -m app.cli.contextctl)
fi

if [[ -z "${RESTORE_CONFIRMED:-}" ]]; then
    cat >&2 <<'WARNING'
This overwrites the application database.

Stop the API first:

    docker compose stop context-api

Then re-run with RESTORE_CONFIRMED=1:

    RESTORE_CONFIRMED=1 ./scripts/restore.sh --latest
    RESTORE_CONFIRMED=1 ./scripts/restore.sh --name context-20260916-131532.archive.gz
WARNING
    exit 2
fi

echo "Restoring ${restore_arguments[*]} at $(date --iso-8601=seconds)"
"${contextctl[@]}" restore "${restore_arguments[@]}" --drop --yes

cat <<'NEXT'

Restored and verified against the managed backup manifest. Search indexes are
not in a backup — they are derived state — so continue in docs/backup.md:

    docker compose start mongot
    docker compose run --rm --no-deps -T context-api \
        python -m app.cli.contextctl skills reindex
    docker compose start context-api
    docker compose exec -T context-api python -m app.cli.contextctl health

Then run the smoke tests in docs/backup.md before calling the recovery done.
NEXT
