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


@pytest.fixture
def tenant_runtime(monkeypatch):
    """One admitted tenant for domain service/repository regression fixtures."""
    from unittest.mock import Mock

    from app.db import tenant_resources
    from app.db.tenant_collection import TenantCollection
    from app.security.tenant import TenantContext, tenant_scope

    original_references = TenantCollection._references
    original_projects = tenant_resources.content_projects
    original_envelope = tenant_resources.content_envelope

    async def references_for_domain_unit_double(self, document, session=None):
        if isinstance(self.raw, Mock):
            return
        return await original_references(self, document, session)

    async def projects_for_domain_unit_double(
        database, collection, row, session=None, seen=frozenset()
    ):
        if isinstance(database, Mock):
            return sorted(tenant_resources.direct_projects(row))
        return await original_projects(database, collection, row, session, seen)

    async def envelope_for_domain_unit_double(
        database, collection, row, session=None, seen=frozenset()
    ):
        if isinstance(database, Mock):
            return sorted(tenant_resources.direct_projects(row)), []
        return await original_envelope(database, collection, row, session, seen)

    # Historical driver doubles exercise repository query/serialization contracts,
    # not reference graphs. Keep that seam explicit. V6 focused tests do not use
    # this fixture; reference/ACL enforcement is exercised on the real replica set.
    monkeypatch.setattr(TenantCollection, "_references", references_for_domain_unit_double)
    monkeypatch.setattr(tenant_resources, "content_projects", projects_for_domain_unit_double)
    monkeypatch.setattr(tenant_resources, "content_envelope", envelope_for_domain_unit_double)
    from app.security.tenant_policy import policy_request_boundary

    with (
        tenant_scope(TenantContext("org-test", b"test-only-cursor-secret-at-least32")),
        policy_request_boundary(),
    ):
        yield
