#!/bin/bash
set -Eeuo pipefail

python_executable="${PYTHON_EXECUTABLE:-.venv/bin/python}"
if [[ ! -x "${python_executable}" ]]; then
    echo "${python_executable} is unavailable; install the development environment first" >&2
    exit 1
fi

mkdir -p .docker-wheels
"${python_executable}" -m pip download \
    --disable-pip-version-check \
    --dest .docker-wheels \
    --requirement requirements-dev.lock
