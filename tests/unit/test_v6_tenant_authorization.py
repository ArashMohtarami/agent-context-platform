"""Trusted tenant/action contracts; selectors never establish authority."""

from dataclasses import replace

import pytest

from app.main import start_authentication
from app.mcp.middleware.client_context import ClientRequestContext
from app.security.principal import AuthenticationError, Principal
from app.security.tenant import TenantContext, TenantContextRequired, require_tenant, tenant_scope
from app.security.tenant_policy import (
    ResourceGrant,
    TenantPolicy,
    bind_principal,
    policy_filter,
    policy_scope,
)
from app.services.auth_service import AuthService
from app.services.project_service import ProjectService
from tests.security_doubles import FakeApiKeyRepository, issued_key


def test_missing_context_and_missing_identity_fail_closed():
    with pytest.raises(TenantContextRequired):
        require_tenant()
    with pytest.raises(AuthenticationError):
        bind_principal(Principal(key_id="a" * 16, name="unowned", scopes=("search:read",)))
    with pytest.raises(ValueError):
        ClientRequestContext.from_identity({"scopes": ["search:read"]}, request_id="request")


async def test_service_cannot_run_without_context_or_switch_org():
    service = ProjectService(object())
    with pytest.raises(TenantContextRequired):
        await service.list_projects()
    with tenant_scope(TenantContext("org-a")):
        bound = ProjectService(object())
    with tenant_scope(TenantContext("org-b")):
        with pytest.raises(TenantContextRequired):
            await bound.list_projects()


def test_complete_grants_never_cross_product_actions_and_resource_ids():
    policy = TenantPolicy(
        "org-a",
        "key",
        (
            ResourceGrant("context:read", "project", "include", ("readable",)),
            ResourceGrant("context:write", "project", "include", ("writable",)),
        ),
    )
    with tenant_scope(TenantContext("org-a")), policy_scope(policy, "context:read"):
        assert "readable" in str(policy_filter("project_context"))
        assert "writable" not in str(policy_filter("project_context"))
    with tenant_scope(TenantContext("org-a")), policy_scope(policy, "context:write"):
        assert "readable" not in str(policy_filter("project_context"))
    assert policy.selector("memory:read", "project") == ()
    assert replace(policy, org_id="org-b").fingerprint != policy.fingerprint


async def test_unowned_credentials_cannot_be_admitted():
    record, token = issued_key()
    legacy = record.model_copy(update={"org_id": None})
    with pytest.raises(AuthenticationError):
        await AuthService(FakeApiKeyRepository(legacy)).authenticate(token)


def test_authentication_disabled_multitenant_startup_is_refused():
    class Settings:
        api_auth_enabled = False

    with pytest.raises(RuntimeError, match="authentication"):
        start_authentication(Settings(), object())


def test_cutover_evidence_rejects_missing_modules_stale_source_and_skips(tmp_path):
    from app.services.tenant_isolation_service import (
        source_fingerprint,
        suite_fingerprint,
        verify_evidence,
    )

    path = tmp_path / "unit-contract-only.xml"
    path.write_text(
        "<testsuites><testsuite>"
        '<testcase classname="test_v6_tenant_authorization"/></testsuite></testsuites>'
    )
    with pytest.raises(ValueError, match="source tree"):
        verify_evidence([path])

    def report(case):
        path.write_text(
            '<testsuites><testsuite><properties><property name="v6_source_sha256" value="'
            + source_fingerprint()
            + '"/><property name="v6_tests_sha256" value="'
            + str(suite_fingerprint())
            + '"/></properties>'
            + case
            + "</testsuite></testsuites>"
        )

    report('<testcase classname="test_v6_tenant_authorization"/>')
    with pytest.raises(ValueError, match="all six"):
        verify_evidence([path])
    report('<testcase classname="test_v6_tenant_authorization"><skipped/></testcase>')
    with pytest.raises(ValueError, match="skips"):
        verify_evidence([path])


async def test_operator_cutover_refuses_tenant_and_mixed_worker_authority():
    from app.services.tenant_isolation_service import TenantIsolationService

    service = TenantIsolationService(object())
    with tenant_scope(TenantContext("org-a")):
        with pytest.raises(ValueError, match="workers"):
            await service.prepare(writers_stopped=True, worker_schema_versions=[36, 37])
        with policy_scope(TenantPolicy("org-a", "bearer", ()), "research:write"):
            with pytest.raises(ValueError, match="operator"):
                await service.prepare(writers_stopped=True, worker_schema_versions=[37])


def test_narrowed_admin_cannot_mint_all_in_org_authority():
    from app.security.tenant_policy import policy_allows

    policy = TenantPolicy(
        "org-a", "admin", (ResourceGrant("agent:manage", "project", "include", ("p1",)),)
    )
    with tenant_scope(TenantContext("org-a")), policy_scope(policy, "agent:manage"):
        assert not policy_allows(
            {"org_id": "org-a", "project_ids": [], "project_mode": "all_in_org"}, "agent_clients"
        )
        assert policy_allows(
            {"org_id": "org-a", "project_ids": ["p1"], "project_mode": "include"}, "agent_clients"
        )
        assert not policy_allows(
            {"org_id": "org-a", "project_ids": ["p2"], "project_mode": "include"}, "agent_clients"
        )


def test_evidence_runtime_fingerprint_survives_production_image_layout(tmp_path, monkeypatch):
    import shutil
    from pathlib import Path

    from app.services import tenant_isolation_service as isolation

    expected = isolation.source_fingerprint()
    runtime = tmp_path / "runtime"
    shutil.copytree(
        Path(isolation.__file__).resolve().parents[1],
        runtime / "app",
        ignore=shutil.ignore_patterns("__pycache__"),
    )
    monkeypatch.setattr(
        isolation, "__file__", str(runtime / "app/services/tenant_isolation_service.py")
    )
    assert isolation.source_fingerprint() == expected
    assert isolation.suite_fingerprint() is None


def test_sync_export_refuses_a_foreign_owned_document_before_formatting():
    from app.models.content import ContentItemDocument
    from app.services.content_export_service import ContentExportService

    with tenant_scope(TenantContext("org-b")):
        foreign = ContentItemDocument(
            profile_id="same-profile",
            content_id="same-id",
            platform="linkedin",
            content_type="post",
            title="Private",
            body="FOREIGN_PRIVATE_SENTINEL",
        )
    with tenant_scope(TenantContext("org-a")):
        with pytest.raises(TenantContextRequired, match="export"):
            ContentExportService({}).export_content(foreign, mode="markdown")
