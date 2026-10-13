"""Unit-test isolation from ambient deployment configuration."""

import os

import pytest

ISOLATED_PREFIXES = ("EMBEDDING_", "SKILL_", "RETRIEVAL_", "VECTOR_SEARCH_")


@pytest.fixture(autouse=True)
def clean_settings_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep unit tests independent of the surrounding environment.

    The Compose test service and the container image both export application
    settings, so a unit test asserting a configuration default must not read
    whatever the deployment happens to set. Tests that want a value set it
    themselves, which still wins because this runs first.
    """
    for name in [key for key in os.environ if key.startswith(ISOLATED_PREFIXES)]:
        monkeypatch.delenv(name, raising=False)
