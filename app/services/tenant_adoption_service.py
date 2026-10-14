"""Explicit resumable operator adoption; Phase 1 never enables domain traffic."""

from collections.abc import Sequence
from dataclasses import asdict, dataclass
from hashlib import sha256
from pathlib import Path
from uuid import uuid4

from bson import BSON
from pydantic import ValidationError

from app.backup.manifest import BackupManifest, digest
from app.db.client_types import MongoDocument
from app.db.tenant_inventory import MODEL_BY_COLLECTION, references
from app.db.tenant_schema import INSTALLATION_COLLECTIONS, TENANT_COLLECTIONS, TENANT_SCHEMA_VERSION
from app.models.common import validate_identifier
from app.repositories.organization_repository import MongoOrganizationRepository
from app.repositories.tenant_adoption_repository import (
    AdoptionConflict,
    MongoTenantAdoptionRepository,
)
from app.security.tenant import TenantContext, tenant_scope


@dataclass(frozen=True)
class AdoptionIssue:
    collection: str
    document_id: str
    code: str
    reference_path: str | None = None


@dataclass(frozen=True)
class AdoptionReport:
    org_id: str
    adoption_id: str
    counts: dict[str, int]
    issues: tuple[AdoptionIssue, ...]
    unresolved_quarantine: int
    stage: str = "dry_run"
    domain_traffic_enabled: bool = False

    @property
    def compatible(self) -> bool:
        return not self.issues and self.unresolved_quarantine == 0

    def describe(self) -> dict[str, object]:
        return {**asdict(self), "compatible": self.compatible}


def check_workers(*, writers_stopped: bool, worker_schema_versions: Sequence[int]) -> None:
    if not writers_stopped:
        raise AdoptionConflict("explicit stopped-writer maintenance acknowledgment is required")
    if not worker_schema_versions or any(
        v != TENANT_SCHEMA_VERSION for v in worker_schema_versions
    ):
        raise AdoptionConflict("mixed or unknown worker schema versions are refused")


def adoption_candidate(raw: MongoDocument, org_id: str, adoption_id: str) -> MongoDocument:
    if "org_id" in raw:
        if raw.get("org_id") != org_id or raw.get("identity_version") != 1:
            raise AdoptionConflict("conflicting or null existing ownership is refused")
        if raw.get("legacy_adoption_id") != adoption_id:
            raise AdoptionConflict("legacy record belongs to another adoption")
        return dict(raw)
    if "identity_version" in raw or "legacy_adoption_id" in raw:
        raise AdoptionConflict("partial tenant identity is refused")
    return {**raw, "org_id": org_id, "identity_version": 1, "legacy_adoption_id": adoption_id}


class TenantAdoptionService:
    def __init__(self, repository: MongoTenantAdoptionRepository) -> None:
        self._repository = repository
        self._organizations = MongoOrganizationRepository(repository.database)

    async def dry_run(self, *, org_id: str, adoption_id: str) -> AdoptionReport:
        validate_identifier(org_id)
        validate_identifier(adoption_id)
        counts = await self._repository.counts()
        issues: list[AdoptionIssue] = []
        state = await self._repository.state()
        installed = state is not None and state.get("schema_version") == TENANT_SCHEMA_VERSION
        if not installed:
            state = None
        installation_collections = (
            INSTALLATION_COLLECTIONS
            if installed
            else ("_schema_migrations", "_mongot_health_probe")
        )
        if state and state.get("adoption_org_id", org_id) != org_id:
            raise AdoptionConflict("installation already belongs to a different adoption context")
        for collection in sorted(counts):
            if collection in installation_collections:
                continue
            async for raw in self._repository.documents(collection):
                identifier = str(raw["_id"])
                if collection not in TENANT_COLLECTIONS:
                    issues.append(AdoptionIssue(collection, identifier, "unknown_collection"))
                    continue
                # After preparation, new tenant records are validated in their
                # registered sealed org; they are never re-adopted into another org.
                prepared = bool(state and state.get("stage") == "prepared")
                if prepared and raw.get("identity_version") == 2:
                    selected_org = raw.get("org_id")
                    organization = await self._organizations.get(str(selected_org))
                    if organization is None:
                        issues.append(AdoptionIssue(collection, identifier, "unregistered_org"))
                        continue
                    candidate = raw
                else:
                    try:
                        candidate = adoption_candidate(raw, org_id, adoption_id)
                    except AdoptionConflict:
                        issues.append(AdoptionIssue(collection, identifier, "invalid_ownership"))
                        continue
                if not await self._repository.valid_candidate(
                    collection, candidate, org_id, adoption_id
                ):
                    issues.append(AdoptionIssue(collection, identifier, "invalid_persisted_schema"))
                    continue
                try:
                    # An operator's whole-installation rehearsal validates each
                    # registered record in its own context. This never adopts
                    # a legacy record into a second org or enables its traffic.
                    with tenant_scope(TenantContext(str(candidate["org_id"]))):
                        MODEL_BY_COLLECTION[collection].model_validate(
                            candidate, context={"from_storage": True}
                        )
                except (ValidationError, ValueError):
                    issues.append(AdoptionIssue(collection, identifier, "invalid_document"))
                    continue
                if raw.get("identity_version") == 1:
                    manifest = await self._repository.preservation(collection, raw["_id"])
                    if manifest is None or manifest.get("adoption_id") != adoption_id:
                        issues.append(AdoptionIssue(collection, identifier, "missing_preservation"))
                        continue
                    if not prepared:
                        preserved_bytes = manifest.get("original")
                        if not isinstance(preserved_bytes, bytes):
                            issues.append(
                                AdoptionIssue(collection, identifier, "invalid_preservation")
                            )
                            continue
                        original = BSON(preserved_bytes).decode(
                            codec_options=self._repository.database.codec_options
                        )
                        stripped = {
                            key: item
                            for key, item in raw.items()
                            if key not in {"org_id", "identity_version", "legacy_adoption_id"}
                        }
                        if (
                            original != stripped
                            or sha256(preserved_bytes).hexdigest() != manifest["sha256"]
                        ):
                            issues.append(
                                AdoptionIssue(collection, identifier, "preservation_mismatch")
                            )
                            continue
                for reference in references(collection, candidate):
                    # All unowned legacy records have the one explicitly selected
                    # adoption owner. Existing foreign/null owners never satisfy a link.
                    ownership: MongoDocument = {"org_id": candidate["org_id"]}
                    if not prepared:
                        ownership = {"$or": [ownership, {"org_id": {"$exists": False}}]}
                    query: MongoDocument = {"$and": [reference.query, ownership]}
                    matched = await self._repository.referenced(reference.target, query)
                    if len(matched) != 1:
                        issues.append(
                            AdoptionIssue(
                                collection, identifier, "unresolved_reference", reference.path
                            )
                        )
        return AdoptionReport(
            org_id,
            adoption_id,
            counts,
            tuple(issues),
            await self._repository.unresolved_quarantine(),
        )

    async def apply(
        self,
        *,
        org_id: str,
        adoption_id: str,
        archive: Path,
        manifest_path: Path,
        writers_stopped: bool,
        worker_schema_versions: Sequence[int],
    ) -> AdoptionReport:
        check_workers(
            writers_stopped=writers_stopped, worker_schema_versions=worker_schema_versions
        )
        organization = await self._organizations.get(org_id)
        if organization is None or organization.status != "sealed":
            raise AdoptionConflict("provision the explicit sealed adoption organization first")
        proof = BackupManifest.model_validate_json(manifest_path.read_text())
        if (
            proof.database != self._repository.database.name
            or proof.bytes != archive.stat().st_size
        ):
            raise AdoptionConflict("backup metadata does not match this database/archive")
        if digest(archive) != proof.sha256:
            raise AdoptionConflict("verified backup checksum is required")
        initial = await self.dry_run(org_id=org_id, adoption_id=adoption_id)
        state = await self._repository.state()
        if state and state.get("backup_sha256", proof.sha256) != proof.sha256:
            raise AdoptionConflict("resume must use the original verified backup")
        if state and state.get("stage") == "prepared":
            if not initial.compatible or state.get("adoption_id") != adoption_id:
                raise AdoptionConflict("prepared installation failed closure recheck")
            return AdoptionReport(
                org_id,
                adoption_id,
                initial.counts,
                initial.issues,
                initial.unresolved_quarantine,
                "prepared",
            )
        if state and state.get("stage") == "not_adopted":
            for collection, count in initial.counts.items():
                if (
                    collection not in INSTALLATION_COLLECTIONS
                    and proof.collections.get(collection) != count
                ):
                    raise AdoptionConflict(
                        "verified whole-database backup coverage/counts do not match"
                    )
        lease = str(uuid4())
        await self._repository.acquire(org_id, adoption_id, lease, proof.sha256)
        try:
            await self._repository.widen_for_adoption(org_id, adoption_id)
            bad = {(issue.collection, issue.document_id): issue.code for issue in initial.issues}
            for collection in initial.counts:
                if collection in INSTALLATION_COLLECTIONS:
                    continue
                await self._repository.heartbeat(lease)
                async for raw in self._repository.documents(collection):
                    await self._repository.heartbeat(lease)
                    reason = bad.get((collection, str(raw["_id"])))
                    if reason is not None:
                        await self._repository.quarantine(
                            collection, raw, reason, adoption_id, lease
                        )
                    elif "org_id" not in raw:
                        await self._repository.preserve_and_adopt(
                            collection, raw, org_id, adoption_id, lease
                        )
            verified = await self.dry_run(org_id=org_id, adoption_id=adoption_id)
            preservation_issues = []
            async for preserved_row in self._repository.documents("_tenant_preservation"):
                if preserved_row["adoption_id"] != adoption_id:
                    continue
                collection = str(preserved_row["collection"])
                present = await self._repository.database[collection].find_one(
                    {
                        "_id": preserved_row["document_id"],
                        "org_id": org_id,
                        "identity_version": 1,
                        "legacy_adoption_id": adoption_id,
                    },
                    {"_id": 1},
                )
                if present is None:
                    preservation_issues.append(
                        AdoptionIssue(
                            collection,
                            str(preserved_row["document_id"]),
                            "preserved_source_missing",
                        )
                    )
            for collection, expected_count in proof.collections.items():
                if collection in INSTALLATION_COLLECTIONS:
                    continue
                preserved = await self._repository.database["_tenant_preservation"].distinct(
                    "_id", {"collection": collection, "adoption_id": adoption_id}
                )
                quarantined = await self._repository.database["_tenant_quarantine"].distinct(
                    "_id", {"collection": collection, "adoption_id": adoption_id}
                )
                if len(set(preserved) | set(quarantined)) != expected_count:
                    preservation_issues.append(
                        AdoptionIssue(collection, "*", "backup_count_mismatch")
                    )
            if preservation_issues:
                verified = AdoptionReport(
                    org_id,
                    adoption_id,
                    verified.counts,
                    (*verified.issues, *preservation_issues),
                    verified.unresolved_quarantine,
                )
            stage = "prepared" if verified.compatible else "blocked"
            if verified.compatible:
                await self._repository.install_contracts(org_id, adoption_id)
            await self._repository.finish(lease, stage, verified.counts)
            return AdoptionReport(
                org_id,
                adoption_id,
                verified.counts,
                verified.issues,
                verified.unresolved_quarantine,
                stage,
            )
        finally:
            await self._repository.release(lease)
