"""No driver operation can omit the tenant boundary, including projections."""

from unittest.mock import AsyncMock, Mock

import pytest
from bson import ObjectId

from app.db.tenant_collection import TenantCollection
from app.models.common import InvalidCursorError, utc_now
from app.security.tenant import TenantContext, TenantContextRequired, tenant_scope
from app.security.tenant_cursor import binding, decode_cursor, encode_cursor


class Cursor:
    def __init__(self, rows):
        self.rows = rows

    def sort(self, *args, **kwargs):
        return self

    def limit(self, count):
        return self

    async def to_list(self, length=None):
        return self.rows


def adapter(rows=()):
    raw = Mock()
    raw.find.return_value = Cursor(list(rows))
    raw.find_one = AsyncMock(return_value=None)
    raw.count_documents = AsyncMock(return_value=0)
    raw.distinct = AsyncMock(return_value=[])
    raw.delete_one = AsyncMock()
    raw.delete_many = AsyncMock()
    raw.update_one = AsyncMock()
    return TenantCollection(raw, "projects", lambda: TenantContext("org-a")), raw


async def test_each_driver_query_keeps_an_immutable_outer_envelope():
    guarded, raw = adapter()
    with tenant_scope(TenantContext("org-a")):
        malicious = {"$or": [{"org_id": "org-b"}, {}]}
        await guarded.find(malicious).to_list(length=1)
        await guarded.find_one(malicious)
        await guarded.count_documents(malicious)
        await guarded.distinct("project_id", malicious)
        await guarded.delete_one(malicious)
        await guarded.delete_many(malicious)
        await guarded.update_one(malicious, {"$set": {"name": "allowed"}})
        for method in (
            "find",
            "find_one",
            "count_documents",
            "distinct",
            "delete_one",
            "delete_many",
            "update_one",
        ):
            query = getattr(raw, method).call_args.args[1 if method == "distinct" else 0]
            assert query["org_id"] == "org-a", method
        with pytest.raises(TenantContextRequired):
            await guarded.find_one({"org_id": "org-b"})
        with pytest.raises(ValueError, match="upsert"):
            await guarded.update_one({}, {"$set": {"name": "x"}}, upsert=True)
        with pytest.raises(ValueError, match="immutable"):
            await guarded.update_one({}, {"$set": {"org_id": "org-b"}})


async def test_projection_and_iteration_defend_against_foreign_driver_results():
    guarded, _ = adapter([{"_id": ObjectId(), "org_id": "org-b", "project_id": "same-id"}])
    with tenant_scope(TenantContext("org-a")):
        with pytest.raises(TenantContextRequired):
            await guarded.find({}, {"project_id": 1}).to_list(length=1)


def test_signed_cursors_bind_org_collection_filter_and_subject():
    secret = b"test-only-cursor-secret-at-least32"
    with tenant_scope(TenantContext("org-a", secret)):
        scope = binding("projects", {"status": "active"})
        cursor = encode_cursor(utc_now(), ObjectId(), scope)
        decode_cursor(cursor, scope)
        for other in (
            binding("skills", {"status": "active"}),
            binding("projects", {"status": "archived"}),
        ):
            with pytest.raises(InvalidCursorError):
                decode_cursor(cursor, other)
        with pytest.raises(InvalidCursorError):
            decode_cursor(cursor[:-8] + "forged00", scope)
    with tenant_scope(TenantContext("org-b", secret)):
        with pytest.raises(InvalidCursorError):
            decode_cursor(cursor, binding("projects", {"status": "active"}))


def test_all_served_routes_have_authentication_or_explicit_gateway_preflight():
    from fastapi.routing import APIRoute

    from app.api.routes.metrics import installation_operator
    from app.api.security import current_principal
    from app.identity.browser import current_browser, identity_boundary, preauth
    from app.main import create_app

    def dependencies(dependant):
        return {dependant.call} | {
            call for child in dependant.dependencies for call in dependencies(child)
        }

    gateway = {
        "identity",
        "acquire_idempotency_reservation",
        "complete_idempotency_reservation",
        "abandon_idempotency_reservation",
        "activity",
        "begin_session",
        "get_session",
        "update_session",
        "end_session",
    }
    seen = set()
    for route in create_app().routes:
        if not isinstance(route, APIRoute):
            continue
        if route.path.startswith("/api/v6/"):
            boundary = dependencies(route.dependant)
            assert identity_boundary in boundary, route.path
            if route.path in {"/api/v6/auth/oidc/callback", "/api/v6/auth/oidc/logged-out"}:
                from app.api.routes.sso_identity import sso_boundary

                assert sso_boundary in boundary, route.path
                assert route.methods == {"GET"}
            elif route.path != "/api/v6/auth/csrf":
                assert current_browser in boundary or preauth in boundary, route.path
        elif route.path.startswith("/api/v4/gateway"):
            assert route.name in gateway
            seen.add(route.name)
        elif route.path.startswith("/api"):
            assert current_principal in dependencies(route.dependant), route.path
        elif route.path == "/metrics":
            assert installation_operator in dependencies(route.dependant)
    assert seen == gateway


def test_domain_repository_raw_driver_paths_have_no_unreviewed_bypass():
    import ast
    from pathlib import Path

    from app.db.tenant_inventory import MODEL_BY_COLLECTION
    from app.db.tenant_schema import TENANT_COLLECTIONS

    assert set(MODEL_BY_COLLECTION) == set(TENANT_COLLECTIONS)
    exceptions = {
        "organization_repository.py": "installation operator registry",
        "tenant_adoption_repository.py": "installation operator preservation/adoption",
        "backup_repository.py": "installation operator whole-database backup",
        "credential_repository.py": "fixed public key ID before tenant resolution",
    }
    observed = set()
    for path in Path("app/repositories").glob("*.py"):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Subscript) and isinstance(node.value, ast.Name | ast.Attribute):
                name = node.value.id if isinstance(node.value, ast.Name) else node.value.attr
                if name in {"database", "_database"}:
                    assert path.name in exceptions, (path, node.lineno)
                    observed.add(path.name)
    assert observed == set(exceptions)
    # Auxiliary chunk/version handles use the same mandatory adapter.
    for module in ("skill_repository", "content_repository"):
        source = Path("app/repositories/" + module + ".py").read_text()
        assert "scoped_collection(" in source
    source = Path("app/repositories/skill_chunk_repository.py").read_text()
    assert "decode_chunk_cursor(cursor, cursor_scope)" in source
    assert "b64decode" not in source
