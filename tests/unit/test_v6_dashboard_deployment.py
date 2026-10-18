"""Distribution contracts and local-only provider wiring; real TLS has its own gate."""

from pathlib import Path, PurePosixPath

import pytest

from app.identity.crypto import AuthRejected
from app.identity.providers import Registry
from scripts import prepare_open_source_release as release


def test_explicit_empty_registry_supports_local_only_and_has_no_selectable_provider(tmp_path):
    path = tmp_path / "approved.json"
    path.write_text("[]")
    registry = Registry.from_file(path, "https://console.example.test")
    assert registry.registrations == {}
    with pytest.raises(AuthRejected):
        registry.get("unapproved")


def test_console_build_is_pinned_and_static_files_are_from_the_actual_lockfile():
    docker = Path("Dockerfile").read_text()
    assert "node:24.22.0-bookworm-slim@sha256:" in docker
    assert "nginx-unprivileged:1.28-alpine@sha256:" in docker
    assert "npm ci --ignore-scripts" in docker
    assert "COPY --from=console-builder /build/dashboard/dist/" in docker
    assert "VITE_" not in docker
    overlay = Path("deploy/console/compose.yml").read_text()
    assert "ports: !reset []" in overlay
    assert "MCP_REMOTE_ALLOW_LOOPBACK_HTTP: 'false'" in overlay
    assert "API_FORWARDED_ALLOW_IPS: ${CONSOLE_PROXY_IP" in overlay
    assert "CONSOLE_KEY_FILE" in overlay and "V6_MFA_KEYRING_SECRET" in overlay


def test_distribution_excludes_private_generated_and_browser_state_at_any_depth():
    for path in (
        "dashboard/node_modules/x",
        "dashboard/dist/index.html",
        "dashboard/test-results/a.zip",
        "dashboard/.private/plan.md",
        "deploy/.private/plan.md",
        "dashboard/blob-report/a.zip",
        "dashboard/.vite/cache",
        "dashboard/.env.production",
        "deploy/secrets/token.key",
    ):
        assert not release.eligible(PurePosixPath(path)), path
    for path in (
        "dashboard/package-lock.json",
        "dashboard/THIRD_PARTY_NOTICES.txt",
        "deploy/console/compose.yml",
        "deploy/console/nginx.conf.template",
        "deploy/keycloak/compose.yml",
        "docs/dashboard-deployment.md",
    ):
        assert release.eligible(PurePosixPath(path)), path


def test_public_dashboard_doc_registry_contains_only_existing_maintained_guides():
    import json

    sources = json.loads(Path("docs/guide/public-sources.json").read_text())
    assert "docs/dashboard-deployment.md" in sources
    assert "docs/dashboard-monitoring.md" in sources
    assert not any(".private" in item or "plan.md" in item for item in sources)
    assert all(Path(item).is_file() for item in sources)
