"""Shared test configuration."""

from pathlib import Path

import pytest

from app.core.config import Settings


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
