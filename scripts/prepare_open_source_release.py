#!/usr/bin/env python3
"""Audit public source and export it without local state or private Git history.

Only known project directories and root files are eligible. In a checkout,
Git supplies tracked and nonignored files; an extracted release uses the same
policy over its filesystem. Diagnostics report paths and rules, never matches.
This bounded scan supplements review; it cannot identify every possible secret.
"""

from __future__ import annotations

import argparse
import gzip
import io
import os
import re
import shutil
import subprocess
import tarfile
from pathlib import Path, PurePosixPath

PUBLIC_DIRECTORIES = frozenset(
    {
        "app",
        "deploy",
        "dashboard",
        "docker",
        "docs",
        "examples",
        "scripts",
        "skills",
        "tests",
        "website",
        ".github",
    }
)
# Deliberate narrow inventory: no local reports, candidate solutions or runs.
BENCHMARK_FILES = frozenset(
    "benchmarks/retrieval/" + name
    for name in (
        "README.md",
        "LICENSE",
        "corpus.json",
        "queries.json",
        "split.json",
        "settings.json",
        "freeze.json",
        "model-lock.json",
        "report-schema.json",
        "tasks.json",
        "scorer.py",
        "model.py",
        "live.py",
        "coding.py",
        "tasks/reference.py",
        "tasks/starter.py",
        "skills/org-a/fact-retry.md",
        "skills/org-a/fact-redact.md",
        "skills/org-b/fact-retry.md",
        "skills/org-b/fact-redact.md",
    )
)
# Original reviewed agent-value inputs only. Private graders/seals/runs are excluded.
BENCHMARK_FILES = BENCHMARK_FILES | frozenset(
    {
        "benchmarks/agent-value/LICENSE",
        "benchmarks/agent-value/README.md",
        "benchmarks/agent-value/dataset.py",
        "benchmarks/agent-value/design.json",
        "benchmarks/agent-value/grading.py",
        "benchmarks/agent-value/inventory.json",
        "benchmarks/agent-value/knowledge/folio/common/overview.md",
        "benchmarks/agent-value/knowledge/folio/development/lookup.md",
        "benchmarks/agent-value/knowledge/folio/development/merge.md",
        "benchmarks/agent-value/knowledge/folio/heldout/allocate.md",
        "benchmarks/agent-value/knowledge/folio/heldout/close.md",
        "benchmarks/agent-value/knowledge/folio/heldout/migration.md",
        "benchmarks/agent-value/knowledge/folio/heldout/prorate-retired.md",
        "benchmarks/agent-value/knowledge/folio/heldout/prorate.md",
        "benchmarks/agent-value/knowledge/folio/heldout/redact.md",
        "benchmarks/agent-value/knowledge/folio/heldout/reverse.md",
        "benchmarks/agent-value/knowledge/harbor/common/overview.md",
        "benchmarks/agent-value/knowledge/harbor/development/import.md",
        "benchmarks/agent-value/knowledge/harbor/development/precision-retired.md",
        "benchmarks/agent-value/knowledge/harbor/development/precision.md",
        "benchmarks/agent-value/knowledge/harbor/heldout/alert-retired.md",
        "benchmarks/agent-value/knowledge/harbor/heldout/alert.md",
        "benchmarks/agent-value/knowledge/harbor/heldout/calibration.md",
        "benchmarks/agent-value/knowledge/harbor/heldout/interpolate.md",
        "benchmarks/agent-value/knowledge/harbor/heldout/rollover.md",
        "benchmarks/agent-value/knowledge/harbor/heldout/units.md",
        "benchmarks/agent-value/knowledge/harbor/heldout/windows.md",
        "benchmarks/agent-value/knowledge/index.json",
        "benchmarks/agent-value/knowledge/parcel/common/overview.md",
        "benchmarks/agent-value/knowledge/parcel/development/pack.md",
        "benchmarks/agent-value/knowledge/parcel/development/tracking.md",
        "benchmarks/agent-value/knowledge/parcel/heldout/dispatch.md",
        "benchmarks/agent-value/knowledge/parcel/heldout/labels.md",
        "benchmarks/agent-value/knowledge/parcel/heldout/page.md",
        "benchmarks/agent-value/knowledge/parcel/heldout/promise-retired.md",
        "benchmarks/agent-value/knowledge/parcel/heldout/promise.md",
        "benchmarks/agent-value/knowledge/parcel/heldout/reserve.md",
        "benchmarks/agent-value/knowledge/parcel/heldout/retention.md",
        "benchmarks/agent-value/knowledge/studio/common/overview.md",
        "benchmarks/agent-value/knowledge/studio/development/equipment.md",
        "benchmarks/agent-value/knowledge/studio/development/hours.md",
        "benchmarks/agent-value/knowledge/studio/heldout/capacity.md",
        "benchmarks/agent-value/knowledge/studio/heldout/consent.md",
        "benchmarks/agent-value/knowledge/studio/heldout/move.md",
        "benchmarks/agent-value/knowledge/studio/heldout/refund-retired.md",
        "benchmarks/agent-value/knowledge/studio/heldout/refund.md",
        "benchmarks/agent-value/knowledge/studio/heldout/utilization.md",
        "benchmarks/agent-value/knowledge/studio/heldout/waitlist.md",
        "benchmarks/agent-value/schemas/private-checks.json",
        "benchmarks/agent-value/schemas/safe-response.json",
        "benchmarks/agent-value/schemas/seal.json",
        "benchmarks/agent-value/schemas/tasks.json",
        "benchmarks/agent-value/seal.py",
        "benchmarks/agent-value/split.json",
        "benchmarks/agent-value/tasks.json",
        "benchmarks/agent-value/tasks/folio-development-lookup/README.md",
        "benchmarks/agent-value/tasks/folio-development-lookup/api.py",
        "benchmarks/agent-value/tasks/folio-development-lookup/domain.py",
        "benchmarks/agent-value/tasks/folio-development-lookup/prompt.txt",
        "benchmarks/agent-value/tasks/folio-development-lookup/repository.py",
        "benchmarks/agent-value/tasks/folio-development-lookup/test_visible.py",
        "benchmarks/agent-value/tasks/folio-development-lookup/visible.json",
        "benchmarks/agent-value/tasks/folio-development-merge/README.md",
        "benchmarks/agent-value/tasks/folio-development-merge/api.py",
        "benchmarks/agent-value/tasks/folio-development-merge/domain.py",
        "benchmarks/agent-value/tasks/folio-development-merge/prompt.txt",
        "benchmarks/agent-value/tasks/folio-development-merge/repository.py",
        "benchmarks/agent-value/tasks/folio-development-merge/test_visible.py",
        "benchmarks/agent-value/tasks/folio-development-merge/visible.json",
        "benchmarks/agent-value/tasks/folio-heldout-allocate/README.md",
        "benchmarks/agent-value/tasks/folio-heldout-allocate/api.py",
        "benchmarks/agent-value/tasks/folio-heldout-allocate/domain.py",
        "benchmarks/agent-value/tasks/folio-heldout-allocate/prompt.txt",
        "benchmarks/agent-value/tasks/folio-heldout-allocate/repository.py",
        "benchmarks/agent-value/tasks/folio-heldout-allocate/test_visible.py",
        "benchmarks/agent-value/tasks/folio-heldout-allocate/visible.json",
        "benchmarks/agent-value/tasks/folio-heldout-close/README.md",
        "benchmarks/agent-value/tasks/folio-heldout-close/api.py",
        "benchmarks/agent-value/tasks/folio-heldout-close/domain.py",
        "benchmarks/agent-value/tasks/folio-heldout-close/prompt.txt",
        "benchmarks/agent-value/tasks/folio-heldout-close/repository.py",
        "benchmarks/agent-value/tasks/folio-heldout-close/test_visible.py",
        "benchmarks/agent-value/tasks/folio-heldout-close/visible.json",
        "benchmarks/agent-value/tasks/folio-heldout-migration/README.md",
        "benchmarks/agent-value/tasks/folio-heldout-migration/api.py",
        "benchmarks/agent-value/tasks/folio-heldout-migration/domain.py",
        "benchmarks/agent-value/tasks/folio-heldout-migration/prompt.txt",
        "benchmarks/agent-value/tasks/folio-heldout-migration/repository.py",
        "benchmarks/agent-value/tasks/folio-heldout-migration/test_visible.py",
        "benchmarks/agent-value/tasks/folio-heldout-migration/visible.json",
        "benchmarks/agent-value/tasks/folio-heldout-prorate/README.md",
        "benchmarks/agent-value/tasks/folio-heldout-prorate/api.py",
        "benchmarks/agent-value/tasks/folio-heldout-prorate/domain.py",
        "benchmarks/agent-value/tasks/folio-heldout-prorate/prompt.txt",
        "benchmarks/agent-value/tasks/folio-heldout-prorate/repository.py",
        "benchmarks/agent-value/tasks/folio-heldout-prorate/test_visible.py",
        "benchmarks/agent-value/tasks/folio-heldout-prorate/visible.json",
        "benchmarks/agent-value/tasks/folio-heldout-redact/README.md",
        "benchmarks/agent-value/tasks/folio-heldout-redact/api.py",
        "benchmarks/agent-value/tasks/folio-heldout-redact/domain.py",
        "benchmarks/agent-value/tasks/folio-heldout-redact/prompt.txt",
        "benchmarks/agent-value/tasks/folio-heldout-redact/repository.py",
        "benchmarks/agent-value/tasks/folio-heldout-redact/test_visible.py",
        "benchmarks/agent-value/tasks/folio-heldout-redact/visible.json",
        "benchmarks/agent-value/tasks/folio-heldout-reverse/README.md",
        "benchmarks/agent-value/tasks/folio-heldout-reverse/api.py",
        "benchmarks/agent-value/tasks/folio-heldout-reverse/domain.py",
        "benchmarks/agent-value/tasks/folio-heldout-reverse/prompt.txt",
        "benchmarks/agent-value/tasks/folio-heldout-reverse/repository.py",
        "benchmarks/agent-value/tasks/folio-heldout-reverse/test_visible.py",
        "benchmarks/agent-value/tasks/folio-heldout-reverse/visible.json",
        "benchmarks/agent-value/tasks/harbor-development-import/README.md",
        "benchmarks/agent-value/tasks/harbor-development-import/api.py",
        "benchmarks/agent-value/tasks/harbor-development-import/domain.py",
        "benchmarks/agent-value/tasks/harbor-development-import/prompt.txt",
        "benchmarks/agent-value/tasks/harbor-development-import/repository.py",
        "benchmarks/agent-value/tasks/harbor-development-import/test_visible.py",
        "benchmarks/agent-value/tasks/harbor-development-import/visible.json",
        "benchmarks/agent-value/tasks/harbor-development-precision/README.md",
        "benchmarks/agent-value/tasks/harbor-development-precision/api.py",
        "benchmarks/agent-value/tasks/harbor-development-precision/domain.py",
        "benchmarks/agent-value/tasks/harbor-development-precision/prompt.txt",
        "benchmarks/agent-value/tasks/harbor-development-precision/repository.py",
        "benchmarks/agent-value/tasks/harbor-development-precision/test_visible.py",
        "benchmarks/agent-value/tasks/harbor-development-precision/visible.json",
        "benchmarks/agent-value/tasks/harbor-heldout-alert/README.md",
        "benchmarks/agent-value/tasks/harbor-heldout-alert/api.py",
        "benchmarks/agent-value/tasks/harbor-heldout-alert/domain.py",
        "benchmarks/agent-value/tasks/harbor-heldout-alert/prompt.txt",
        "benchmarks/agent-value/tasks/harbor-heldout-alert/repository.py",
        "benchmarks/agent-value/tasks/harbor-heldout-alert/test_visible.py",
        "benchmarks/agent-value/tasks/harbor-heldout-alert/visible.json",
        "benchmarks/agent-value/tasks/harbor-heldout-calibration/README.md",
        "benchmarks/agent-value/tasks/harbor-heldout-calibration/api.py",
        "benchmarks/agent-value/tasks/harbor-heldout-calibration/domain.py",
        "benchmarks/agent-value/tasks/harbor-heldout-calibration/prompt.txt",
        "benchmarks/agent-value/tasks/harbor-heldout-calibration/repository.py",
        "benchmarks/agent-value/tasks/harbor-heldout-calibration/test_visible.py",
        "benchmarks/agent-value/tasks/harbor-heldout-calibration/visible.json",
        "benchmarks/agent-value/tasks/harbor-heldout-interpolate/README.md",
        "benchmarks/agent-value/tasks/harbor-heldout-interpolate/api.py",
        "benchmarks/agent-value/tasks/harbor-heldout-interpolate/domain.py",
        "benchmarks/agent-value/tasks/harbor-heldout-interpolate/prompt.txt",
        "benchmarks/agent-value/tasks/harbor-heldout-interpolate/repository.py",
        "benchmarks/agent-value/tasks/harbor-heldout-interpolate/test_visible.py",
        "benchmarks/agent-value/tasks/harbor-heldout-interpolate/visible.json",
        "benchmarks/agent-value/tasks/harbor-heldout-rollover/README.md",
        "benchmarks/agent-value/tasks/harbor-heldout-rollover/api.py",
        "benchmarks/agent-value/tasks/harbor-heldout-rollover/domain.py",
        "benchmarks/agent-value/tasks/harbor-heldout-rollover/prompt.txt",
        "benchmarks/agent-value/tasks/harbor-heldout-rollover/repository.py",
        "benchmarks/agent-value/tasks/harbor-heldout-rollover/test_visible.py",
        "benchmarks/agent-value/tasks/harbor-heldout-rollover/visible.json",
        "benchmarks/agent-value/tasks/harbor-heldout-units/README.md",
        "benchmarks/agent-value/tasks/harbor-heldout-units/api.py",
        "benchmarks/agent-value/tasks/harbor-heldout-units/domain.py",
        "benchmarks/agent-value/tasks/harbor-heldout-units/prompt.txt",
        "benchmarks/agent-value/tasks/harbor-heldout-units/repository.py",
        "benchmarks/agent-value/tasks/harbor-heldout-units/test_visible.py",
        "benchmarks/agent-value/tasks/harbor-heldout-units/visible.json",
        "benchmarks/agent-value/tasks/harbor-heldout-windows/README.md",
        "benchmarks/agent-value/tasks/harbor-heldout-windows/api.py",
        "benchmarks/agent-value/tasks/harbor-heldout-windows/domain.py",
        "benchmarks/agent-value/tasks/harbor-heldout-windows/prompt.txt",
        "benchmarks/agent-value/tasks/harbor-heldout-windows/repository.py",
        "benchmarks/agent-value/tasks/harbor-heldout-windows/test_visible.py",
        "benchmarks/agent-value/tasks/harbor-heldout-windows/visible.json",
        "benchmarks/agent-value/tasks/parcel-development-pack/README.md",
        "benchmarks/agent-value/tasks/parcel-development-pack/api.py",
        "benchmarks/agent-value/tasks/parcel-development-pack/domain.py",
        "benchmarks/agent-value/tasks/parcel-development-pack/prompt.txt",
        "benchmarks/agent-value/tasks/parcel-development-pack/repository.py",
        "benchmarks/agent-value/tasks/parcel-development-pack/test_visible.py",
        "benchmarks/agent-value/tasks/parcel-development-pack/visible.json",
        "benchmarks/agent-value/tasks/parcel-development-tracking/README.md",
        "benchmarks/agent-value/tasks/parcel-development-tracking/api.py",
        "benchmarks/agent-value/tasks/parcel-development-tracking/domain.py",
        "benchmarks/agent-value/tasks/parcel-development-tracking/prompt.txt",
        "benchmarks/agent-value/tasks/parcel-development-tracking/repository.py",
        "benchmarks/agent-value/tasks/parcel-development-tracking/test_visible.py",
        "benchmarks/agent-value/tasks/parcel-development-tracking/visible.json",
        "benchmarks/agent-value/tasks/parcel-heldout-dispatch/README.md",
        "benchmarks/agent-value/tasks/parcel-heldout-dispatch/api.py",
        "benchmarks/agent-value/tasks/parcel-heldout-dispatch/domain.py",
        "benchmarks/agent-value/tasks/parcel-heldout-dispatch/prompt.txt",
        "benchmarks/agent-value/tasks/parcel-heldout-dispatch/repository.py",
        "benchmarks/agent-value/tasks/parcel-heldout-dispatch/test_visible.py",
        "benchmarks/agent-value/tasks/parcel-heldout-dispatch/visible.json",
        "benchmarks/agent-value/tasks/parcel-heldout-labels/README.md",
        "benchmarks/agent-value/tasks/parcel-heldout-labels/api.py",
        "benchmarks/agent-value/tasks/parcel-heldout-labels/domain.py",
        "benchmarks/agent-value/tasks/parcel-heldout-labels/prompt.txt",
        "benchmarks/agent-value/tasks/parcel-heldout-labels/repository.py",
        "benchmarks/agent-value/tasks/parcel-heldout-labels/test_visible.py",
        "benchmarks/agent-value/tasks/parcel-heldout-labels/visible.json",
        "benchmarks/agent-value/tasks/parcel-heldout-page/README.md",
        "benchmarks/agent-value/tasks/parcel-heldout-page/api.py",
        "benchmarks/agent-value/tasks/parcel-heldout-page/domain.py",
        "benchmarks/agent-value/tasks/parcel-heldout-page/prompt.txt",
        "benchmarks/agent-value/tasks/parcel-heldout-page/repository.py",
        "benchmarks/agent-value/tasks/parcel-heldout-page/test_visible.py",
        "benchmarks/agent-value/tasks/parcel-heldout-page/visible.json",
        "benchmarks/agent-value/tasks/parcel-heldout-promise/README.md",
        "benchmarks/agent-value/tasks/parcel-heldout-promise/api.py",
        "benchmarks/agent-value/tasks/parcel-heldout-promise/domain.py",
        "benchmarks/agent-value/tasks/parcel-heldout-promise/prompt.txt",
        "benchmarks/agent-value/tasks/parcel-heldout-promise/repository.py",
        "benchmarks/agent-value/tasks/parcel-heldout-promise/test_visible.py",
        "benchmarks/agent-value/tasks/parcel-heldout-promise/visible.json",
        "benchmarks/agent-value/tasks/parcel-heldout-reserve/README.md",
        "benchmarks/agent-value/tasks/parcel-heldout-reserve/api.py",
        "benchmarks/agent-value/tasks/parcel-heldout-reserve/domain.py",
        "benchmarks/agent-value/tasks/parcel-heldout-reserve/prompt.txt",
        "benchmarks/agent-value/tasks/parcel-heldout-reserve/repository.py",
        "benchmarks/agent-value/tasks/parcel-heldout-reserve/test_visible.py",
        "benchmarks/agent-value/tasks/parcel-heldout-reserve/visible.json",
        "benchmarks/agent-value/tasks/parcel-heldout-retention/README.md",
        "benchmarks/agent-value/tasks/parcel-heldout-retention/api.py",
        "benchmarks/agent-value/tasks/parcel-heldout-retention/domain.py",
        "benchmarks/agent-value/tasks/parcel-heldout-retention/prompt.txt",
        "benchmarks/agent-value/tasks/parcel-heldout-retention/repository.py",
        "benchmarks/agent-value/tasks/parcel-heldout-retention/test_visible.py",
        "benchmarks/agent-value/tasks/parcel-heldout-retention/visible.json",
        "benchmarks/agent-value/tasks/studio-development-equipment/README.md",
        "benchmarks/agent-value/tasks/studio-development-equipment/api.py",
        "benchmarks/agent-value/tasks/studio-development-equipment/domain.py",
        "benchmarks/agent-value/tasks/studio-development-equipment/prompt.txt",
        "benchmarks/agent-value/tasks/studio-development-equipment/repository.py",
        "benchmarks/agent-value/tasks/studio-development-equipment/test_visible.py",
        "benchmarks/agent-value/tasks/studio-development-equipment/visible.json",
        "benchmarks/agent-value/tasks/studio-development-hours/README.md",
        "benchmarks/agent-value/tasks/studio-development-hours/api.py",
        "benchmarks/agent-value/tasks/studio-development-hours/domain.py",
        "benchmarks/agent-value/tasks/studio-development-hours/prompt.txt",
        "benchmarks/agent-value/tasks/studio-development-hours/repository.py",
        "benchmarks/agent-value/tasks/studio-development-hours/test_visible.py",
        "benchmarks/agent-value/tasks/studio-development-hours/visible.json",
        "benchmarks/agent-value/tasks/studio-heldout-capacity/README.md",
        "benchmarks/agent-value/tasks/studio-heldout-capacity/api.py",
        "benchmarks/agent-value/tasks/studio-heldout-capacity/domain.py",
        "benchmarks/agent-value/tasks/studio-heldout-capacity/prompt.txt",
        "benchmarks/agent-value/tasks/studio-heldout-capacity/repository.py",
        "benchmarks/agent-value/tasks/studio-heldout-capacity/test_visible.py",
        "benchmarks/agent-value/tasks/studio-heldout-capacity/visible.json",
        "benchmarks/agent-value/tasks/studio-heldout-consent/README.md",
        "benchmarks/agent-value/tasks/studio-heldout-consent/api.py",
        "benchmarks/agent-value/tasks/studio-heldout-consent/domain.py",
        "benchmarks/agent-value/tasks/studio-heldout-consent/prompt.txt",
        "benchmarks/agent-value/tasks/studio-heldout-consent/repository.py",
        "benchmarks/agent-value/tasks/studio-heldout-consent/test_visible.py",
        "benchmarks/agent-value/tasks/studio-heldout-consent/visible.json",
        "benchmarks/agent-value/tasks/studio-heldout-move/README.md",
        "benchmarks/agent-value/tasks/studio-heldout-move/api.py",
        "benchmarks/agent-value/tasks/studio-heldout-move/domain.py",
        "benchmarks/agent-value/tasks/studio-heldout-move/prompt.txt",
        "benchmarks/agent-value/tasks/studio-heldout-move/repository.py",
        "benchmarks/agent-value/tasks/studio-heldout-move/test_visible.py",
        "benchmarks/agent-value/tasks/studio-heldout-move/visible.json",
        "benchmarks/agent-value/tasks/studio-heldout-refund/README.md",
        "benchmarks/agent-value/tasks/studio-heldout-refund/api.py",
        "benchmarks/agent-value/tasks/studio-heldout-refund/domain.py",
        "benchmarks/agent-value/tasks/studio-heldout-refund/prompt.txt",
        "benchmarks/agent-value/tasks/studio-heldout-refund/repository.py",
        "benchmarks/agent-value/tasks/studio-heldout-refund/test_visible.py",
        "benchmarks/agent-value/tasks/studio-heldout-refund/visible.json",
        "benchmarks/agent-value/tasks/studio-heldout-utilization/README.md",
        "benchmarks/agent-value/tasks/studio-heldout-utilization/api.py",
        "benchmarks/agent-value/tasks/studio-heldout-utilization/domain.py",
        "benchmarks/agent-value/tasks/studio-heldout-utilization/prompt.txt",
        "benchmarks/agent-value/tasks/studio-heldout-utilization/repository.py",
        "benchmarks/agent-value/tasks/studio-heldout-utilization/test_visible.py",
        "benchmarks/agent-value/tasks/studio-heldout-utilization/visible.json",
        "benchmarks/agent-value/tasks/studio-heldout-waitlist/README.md",
        "benchmarks/agent-value/tasks/studio-heldout-waitlist/api.py",
        "benchmarks/agent-value/tasks/studio-heldout-waitlist/domain.py",
        "benchmarks/agent-value/tasks/studio-heldout-waitlist/prompt.txt",
        "benchmarks/agent-value/tasks/studio-heldout-waitlist/repository.py",
        "benchmarks/agent-value/tasks/studio-heldout-waitlist/test_visible.py",
        "benchmarks/agent-value/tasks/studio-heldout-waitlist/visible.json",
        "benchmarks/agent-value/worker.py",
    }
)

PUBLIC_ROOT_FILES = frozenset(
    {
        ".dockerignore",
        ".env.example",
        ".gitignore",
        ".graphifyignore",
        "CONTRIBUTING.md",
        "Dockerfile",
        "LICENSE",
        "MANIFEST.in",
        "Makefile",
        "README.md",
        "SECURITY.md",
        "docker-compose.yml",
        "docker-compose.website.yml",
        "pyproject.toml",
        "requirements.lock",
        "requirements-dev.lock",
    }
)
DASHBOARD_REQUIRED = frozenset(
    {
        "dashboard/package.json",
        "dashboard/package-lock.json",
        "dashboard/.node-version",
        "dashboard/LICENSE",
        "dashboard/THIRD_PARTY_NOTICES.txt",
        "dashboard/dependency-licenses.json",
        "dashboard/src/main.tsx",
        "dashboard/index.html",
        "dashboard/vite.config.ts",
        "dashboard/tsconfig.json",
        "dashboard/playwright.config.ts",
        "dashboard/eslint.config.js",
        "dashboard/scripts/check-build.mjs",
        "dashboard/scripts/licenses.mjs",
    }
)
PRIVATE_PARTS = frozenset(
    {
        ".git",
        ".private",
        ".codex",
        ".agents",
        ".aws",
        ".venv",
        ".graphify",
        "graphify-out",
        "secrets",
        "backups",
        "__pycache__",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        "node_modules",
        "test-results",
        "playwright-report",
        "blob-report",
        ".vite",
        ".npm",
        "coverage",
        "dist",
        "build",
        "htmlcov",
    }
)
SECRET_PATTERNS = (
    ("private key", re.compile(rb"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----")),
    ("platform API key", re.compile(rb"\bacp_[0-9a-f]{16}_[A-Za-z0-9_-]{32,}\b")),
    (
        "GitHub token",
        re.compile(rb"\b(?:gh[pousr]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{60,})\b"),
    ),
    ("AWS access key", re.compile(rb"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("OpenAI API key", re.compile(rb"\bsk-(?:proj-|svcacct-)?[A-Za-z0-9_-]{48,}\b")),
)
# Deliberately invalid literals already used by authentication adversarial tests.
TEST_API_KEYS = frozenset(
    {
        b"acp_" + b"0123456789abcdef_" + b"a" * 34,
        b"acp_" + b"0123456789abcdef_" + b"wrongsecret" * 3,
    }
)
PRIVATE_DOCUMENT = re.compile(
    r"(?:agent-context-platform-(?:plan|v\d.*)\.md|my-doc\.md|"
    r"(?:mvp-user-guide|open-source-release|repository-content-launch)\.md|"
    r"(?:acceptance-report|v\d+-readiness-report|v\d+-.*security-review)\.md)$"
)


def eligible(path: PurePosixPath) -> bool:
    """Return whether a relative path belongs to the public source inventory."""
    if path.is_absolute() or ".." in path.parts or any(p in PRIVATE_PARTS for p in path.parts):
        return False
    if PRIVATE_DOCUMENT.fullmatch(path.name):
        return False
    if path.name.startswith(".env") and path.name != ".env.example":
        return False
    if path.suffix in {".pyc", ".pyo", ".sqlite3", ".gz", ".zip", ".pem", ".key", ".tsbuildinfo"}:
        return False
    if any(p.endswith(".egg-info") for p in path.parts):
        return False
    if path.parts[0] == "benchmarks":
        return path.as_posix() in BENCHMARK_FILES
    if len(path.parts) == 1:
        return path.name in PUBLIC_ROOT_FILES
    return path.parts[0] in PUBLIC_DIRECTORIES or path.as_posix() == ".docker-wheels/.gitkeep"


def candidate_paths(root: Path) -> list[PurePosixPath]:
    """List files without traversing ignored private directories or Git history."""
    if (root / ".git").exists():
        git = shutil.which("git")
        if git is None:
            raise ValueError("Git is required to audit a checkout")
        result = subprocess.run(  # noqa: S603 - fixed read-only argv, no shell
            [git, "-C", str(root), "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
            check=True,
            capture_output=True,
        )
        return sorted({PurePosixPath(os.fsdecode(p)) for p in result.stdout.split(b"\0") if p})
    candidates = []
    for directory, subdirectories, filenames in os.walk(root, followlinks=False):
        relative = Path(directory).relative_to(root)
        subdirectories[:] = [p for p in subdirectories if p not in PRIVATE_PARTS]
        # Directory symlinks cannot be allowed to escape an otherwise public root.
        for name in list(subdirectories):
            child = Path(directory) / name
            if child.is_symlink():
                candidates.append(PurePosixPath((relative / name).as_posix()))
                subdirectories.remove(name)
        candidates.extend(PurePosixPath((relative / name).as_posix()) for name in filenames)
    return sorted(candidates)


def audited_files(root: Path, forbidden: tuple[str, ...] = ()) -> dict[PurePosixPath, bytes]:
    """Read eligible files and reject suspect public contents without printing them."""
    files: dict[PurePosixPath, bytes] = {}
    problems = []
    for relative in candidate_paths(root):
        if not eligible(relative):
            continue
        path = root / relative
        # Check every ancestor too: a tracked directory may have been replaced
        # by a symlink since Git last saw it.
        ancestors = (
            root / PurePosixPath(*relative.parts[:i]) for i in range(1, len(relative.parts) + 1)
        )
        if any(p.is_symlink() for p in ancestors):
            problems.append(f"{relative}: symlink is not allowed in public source")
            continue
        if not path.exists():
            continue  # Tracked worktree deletions, including archived planning files.
        if not path.is_file():
            problems.append(f"{relative}: expected a regular file")
            continue
        contents = path.read_bytes()
        for label, pattern in SECRET_PATTERNS:
            for match in pattern.finditer(contents):
                if (
                    label == "platform API key"
                    and relative.as_posix() == "tests/unit/test_api_authorization.py"
                    and match.group() in TEST_API_KEYS
                ):
                    continue
                line = contents.count(b"\n", 0, match.start()) + 1
                problems.append(f"{relative}:{line}: possible {label}")
        decoded = contents.decode("utf-8", errors="replace").casefold()
        for index, value in enumerate(forbidden, 1):
            if value.casefold() in decoded:
                problems.append(f"{relative}: forbidden private text rule {index}")
        files[relative] = contents
    for required in ("README.md", "LICENSE", "pyproject.toml"):
        if PurePosixPath(required) not in files:
            problems.append(f"{required}: required public release file is missing")
    if any(path.parts[0] == "dashboard" for path in files):
        for required in sorted(DASHBOARD_REQUIRED):
            if PurePosixPath(required) not in files:
                problems.append(f"{required}: required dashboard distribution file is missing")
    if problems:
        raise ValueError("Public source audit failed:\n" + "\n".join(problems))
    return files


def write_archive(root: Path, files: dict[PurePosixPath, bytes], output: Path) -> None:
    """Write deterministic tar/gzip contents, omitting owner names and timestamps."""
    output.parent.mkdir(parents=True, exist_ok=True)
    # Exclusive creation keeps repeated runs from replacing an existing artifact.
    with output.open("xb") as destination:
        with gzip.GzipFile(fileobj=destination, mode="wb", filename="", mtime=0) as compressed:
            with tarfile.open(fileobj=compressed, mode="w") as archive:
                for relative, contents in sorted(files.items()):
                    member = tarfile.TarInfo(f"agent-context-platform/{relative}")
                    member.size = len(contents)
                    member.mode = 0o755 if (root / relative).stat().st_mode & 0o111 else 0o644
                    archive.addfile(member, io.BytesIO(contents))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository-root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--check", action="store_true", help="Audit without writing an archive")
    parser.add_argument(
        "--output", type=Path, default=Path("dist/agent-context-platform-source.tar.gz")
    )
    parser.add_argument(
        "--forbid-text",
        action="append",
        default=[],
        metavar="TEXT",
        help="Reject a private organization name/path (repeatable; matches are never printed)",
    )
    arguments = parser.parse_args()
    root = arguments.repository_root.resolve()
    try:
        files = audited_files(root, tuple(arguments.forbid_text))
        print(
            f"Public source audit passed: {len(files)} files; "
            "no local state or Git history included."
        )
        if not arguments.check:
            write_archive(root, files, arguments.output.resolve())
            print(f"Source archive: {arguments.output}")
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        # subprocess stderr is deliberately not printed; only our path-only
        # diagnostics and ordinary OS errors can reach the operator.
        print(str(error))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
