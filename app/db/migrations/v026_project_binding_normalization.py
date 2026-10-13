"""Canonicalize existing V4 repository bindings before resolution is enabled."""

from datetime import UTC, datetime

from pymongo.asynchronous.database import AsyncDatabase

from app.db.client_types import MongoDocument
from app.db.migrations.base import IrreversibleMigrationError
from app.db.schema import COLLECTION_VALIDATORS
from app.models.agent import ProjectBindingType
from app.projects.bindings import ProjectBindingValueError, normalize_binding_value


class ProjectBindingNormalizationMigration:
    """Normalize stored SSH/HTTPS forms without choosing through a conflict."""

    @property
    def version(self) -> int:
        return 26

    @property
    def description(self) -> str:
        return "canonicalize V4 project binding values for deterministic resolution"

    async def up(self, database: AsyncDatabase[MongoDocument]) -> None:
        """Preflight every value, then update only non-conflicting rows.

        The Phase 0 collection existed before repository locator normalization.
        Two previously distinct spellings can now refer to the same repository,
        so this migration fails closed for either duplicates or cross-project
        conflicts.  It never selects, merges, or deletes a binding for an
        operator.
        """
        collection = database["project_bindings"]
        await database.command(
            {
                "collMod": "project_bindings",
                "validator": COLLECTION_VALIDATORS["project_bindings"],
                "validationLevel": "strict",
                "validationAction": "error",
            }
        )
        cursor = collection.find(
            {},
            {
                "_id": 1,
                "binding_id": 1,
                "project_id": 1,
                "binding_type": 1,
                "value": 1,
                "repository_locator": 1,
            },
        )
        documents = await cursor.to_list(length=None)
        planned: list[tuple[MongoDocument, ProjectBindingType, str, str | None]] = []
        mappings: dict[tuple[str, str], set[str]] = {}
        repository_counts: dict[str, int] = {}

        for raw in documents:
            binding_type = ProjectBindingType(str(raw["binding_type"]))
            value = raw.get("value")
            project_id = raw.get("project_id")
            if not isinstance(value, str) or not isinstance(project_id, str):
                raise IrreversibleMigrationError("project binding has an invalid persisted value")
            try:
                normalized = normalize_binding_value(binding_type.value, value)
            except ProjectBindingValueError as error:
                raise IrreversibleMigrationError(
                    "project binding requires operator repair before normalization"
                ) from error
            repository_locator = (
                normalized
                if binding_type
                in {ProjectBindingType.GIT_REMOTE, ProjectBindingType.REPOSITORY_URL}
                else None
            )
            persisted_locator = raw.get("repository_locator")
            if persisted_locator not in {None, repository_locator}:
                raise IrreversibleMigrationError(
                    "project binding has an invalid persisted repository locator"
                )
            planned.append((raw, binding_type, normalized, repository_locator))
            mappings.setdefault((binding_type.value, normalized), set()).add(project_id)
            if repository_locator is not None:
                repository_counts[repository_locator] = (
                    repository_counts.get(repository_locator, 0) + 1
                )

        if any(len(project_ids) > 1 for project_ids in mappings.values()):
            raise IrreversibleMigrationError(
                "canonical project bindings conflict; repair mappings before enabling resolution"
            )
        if any(count > 1 for count in repository_counts.values()):
            raise IrreversibleMigrationError(
                "SSH and HTTPS project bindings duplicate or conflict; "
                "repair mappings before enabling resolution"
            )
        # Same-project rows which normalize to the same `(type, value)` would
        # collide with the existing unique index.  Keeping both would have no
        # independent meaning, but deleting one is still an operator decision.
        counts: dict[tuple[str, str], int] = {}
        for _, binding_type, normalized, _ in planned:
            key = (binding_type.value, normalized)
            counts[key] = counts.get(key, 0) + 1
        if any(count > 1 for count in counts.values()):
            raise IrreversibleMigrationError(
                "canonical project bindings duplicate; repair mappings before enabling resolution"
            )

        now = datetime.now(UTC)
        for raw, _, normalized, repository_locator in planned:
            value = raw["value"]
            if value == normalized and raw.get("repository_locator") == repository_locator:
                continue
            await collection.update_one(
                {"_id": raw["_id"], "value": value},
                {
                    "$set": {
                        "value": normalized,
                        "repository_locator": repository_locator,
                        "updated_at": now,
                    },
                    "$inc": {"revision": 1},
                },
            )

    async def down(self, database: AsyncDatabase[MongoDocument]) -> None:
        """Do not restore unsafe raw spellings or discard updated history."""
        _ = database
        raise IrreversibleMigrationError(
            "canonical project binding values cannot be safely converted back"
        )
