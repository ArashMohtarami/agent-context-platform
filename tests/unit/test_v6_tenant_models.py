"""Phase 1 storage identities, explicit context and guarded ownership."""

import asyncio
from dataclasses import FrozenInstanceError
from hashlib import sha256

import pytest
from bson import ObjectId
from pydantic import ValidationError
from v6_fixtures import legacy_fixture

from app.db.tenant_inventory import MODEL_BY_COLLECTION
from app.db.tenant_schema import TENANT_COLLECTIONS, TENANT_INDEX_DEFINITIONS, tenant_validator
from app.models.common import deterministic_object_id, tenant_object_id
from app.models.organization import OrganizationDocument
from app.models.project import ProjectDocument
from app.security.tenant import TenantContext, current_tenant, require_tenant, tenant_scope


@pytest.mark.parametrize("collection", TENANT_COLLECTIONS)
def test_every_family_supports_adopted_and_new_identity(collection):
    raw = legacy_fixture()[collection][0]
    model = MODEL_BY_COLLECTION[collection]
    adopted = {
        **raw,
        "org_id": "org-a",
        "identity_version": 1,
        "legacy_adoption_id": "adoption-one",
    }
    with pytest.raises(ValidationError, match="only adoption"):
        model.model_validate(adopted)
    document = model.model_validate(adopted, context={"from_storage": True})
    assert document.id == raw["_id"] and document.revision == raw["revision"]
    document.revision += 1
    with pytest.raises(ValidationError, match="frozen"):
        document.org_id = "org-b"
    new = {key: value for key, value in raw.items() if key != "_id"}
    with tenant_scope(TenantContext("org-a")):
        a = model.model_validate(new)
    with tenant_scope(TenantContext("org-b")):
        b = model.model_validate(new)
    assert a.org_id == "org-a" and b.org_id == "org-b"
    assert a.identity_version == b.identity_version == 2 and a.id != b.id


def test_identity_encoding_is_fixed_and_legacy_is_unchanged():
    assert deterministic_object_id("project", "same") == ObjectId(
        sha256(b"project\0same").digest()[:12]
    )
    assert tenant_object_id("org-a", "project", "same") == ObjectId(
        sha256(b'["acp-id",2,"project","org-a","same"]').digest()[:12]
    )
    assert tenant_object_id("org-a", "memory", "project", "same", "external") == ObjectId(
        sha256(b'["acp-id",2,"memory","org-a","project","same","","external"]').digest()[:12]
    )


@pytest.mark.parametrize(
    "metadata",
    [
        {"org_id": None},
        {"identity_version": 1},
        {"org_id": "org-a", "identity_version": None},
        {"org_id": "org-a", "identity_version": True},
        {"org_id": "org-a", "identity_version": 2, "legacy_adoption_id": "fake"},
    ],
)
def test_partial_or_client_selected_legacy_metadata_is_refused(metadata):
    raw = legacy_fixture()["projects"][0]
    with pytest.raises(ValidationError):
        ProjectDocument.model_validate({**raw, **metadata})


async def test_context_is_explicit_immutable_and_task_local():
    assert current_tenant() is None
    with pytest.raises(ValueError):
        require_tenant()
    with pytest.raises(ValueError):
        TenantContext("")
    context = TenantContext("org-a")
    with pytest.raises(FrozenInstanceError):
        context.org_id = "org-b"

    async def selected(org):
        with tenant_scope(TenantContext(org)):
            await asyncio.sleep(0)
            return require_tenant().org_id

    assert await asyncio.gather(selected("org-a"), selected("org-b")) == ["org-a", "org-b"]
    assert current_tenant() is None


def test_complete_storage_catalog_and_compound_indexes():
    assert set(MODEL_BY_COLLECTION) == set(TENANT_COLLECTIONS) == set(TENANT_INDEX_DEFINITIONS)
    for name, definitions in TENANT_INDEX_DEFINITIONS.items():
        assert tenant_validator(name, org_id="org-a", adoption_id="one")
        for definition in definitions:
            index = definition.document
            if "expireAfterSeconds" in index or (
                name == "api_keys" and list(index["key"]) == ["key_id"]
            ):
                continue
            assert next(iter(index["key"])) == "org_id"


def test_registry_has_no_activation_or_owner_claim():
    with pytest.raises(ValidationError):
        OrganizationDocument(org_id="org-a", slug="a", name="A", status="active")
    with pytest.raises(ValidationError):
        OrganizationDocument(org_id="org-a", slug="a", name="A", owner_email="owner@example.test")


@pytest.mark.parametrize(
    "field,value",
    [
        ("org_id", "org-a"),
        ("identity_version", 1),
        ("legacy_adoption_id", "forged"),
        ("actor", {"actor_type": "human"}),
    ],
)
def test_request_dto_cannot_choose_owner_legacy_or_actor(field, value):
    from app.schemas.project import ProjectCreateRequest

    raw = legacy_fixture()["projects"][0]
    allowed = {key: value for key, value in raw.items() if key in ProjectCreateRequest.model_fields}
    with pytest.raises(ValidationError, match="Extra inputs"):
        ProjectCreateRequest.model_validate({**allowed, field: value})
