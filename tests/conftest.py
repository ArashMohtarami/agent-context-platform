"""Shared test configuration."""

from pathlib import Path

import pytest

from app.core.config import Settings

pytest_plugins = (
    "v6_isolation_fixtures",
    "v6_oidc_fixtures",
    "v6_management_fixtures",
    "v6_managed_agent_fixtures",
    "v6_summary_fixtures",
    "v6_performance_fixtures",
)


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    """Return isolated valid application settings."""
    password_file = tmp_path / "mongodb-password"
    password_file.write_text("test-only-password", encoding="utf-8")
    return Settings(
        _env_file=None,
        mongodb_uri="mongodb://mongodb:27017",
        mongodb_database="agent_context_test",
        mongodb_username="application_test_user",
        mongodb_password_file=password_file,
        mongodb_replica_set="rs0",
        mongodb_app_name="agent-context-platform-tests",
    )


@pytest.fixture(scope="session", autouse=True)
def bind_isolation_evidence_to_source(record_testsuite_property):
    """Reports identify the exact public runtime and test source they exercised."""
    from app.services.tenant_isolation_service import source_fingerprint, suite_fingerprint

    record_testsuite_property("v6_source_sha256", source_fingerprint())
    record_testsuite_property("v6_tests_sha256", suite_fingerprint())
