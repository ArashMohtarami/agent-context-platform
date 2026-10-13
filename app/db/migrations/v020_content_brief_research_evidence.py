"""Phase 13 migration for research-backed V2 content-brief references."""

from copy import deepcopy
from typing import Final, cast

from pymongo.asynchronous.database import AsyncDatabase

from app.db.client_types import MongoDocument
from app.db.migrations.base import IrreversibleMigrationError
from app.db.schema import COLLECTION_VALIDATORS

_CONTENT_BRIEF_FIELDS: Final = (
    "research_project_id",
    "claim_refs",
    "citation_refs",
    "factual_review_status",
)


def _intermediate_validator() -> MongoDocument:
    """Allow legacy brief rows while their additive defaults are backfilled."""
    validator = deepcopy(COLLECTION_VALIDATORS["content_briefs"])
    schema = cast(MongoDocument, validator["$jsonSchema"])
    required = cast(list[str], schema["required"])
    schema["required"] = [field for field in required if field not in _CONTENT_BRIEF_FIELDS]
    return validator


class ContentBriefResearchEvidenceMigration:
    """Backfill empty V3 evidence references without changing any content prose."""

    @property
    def version(self) -> int:
        return 20

    @property
    def description(self) -> str:
        return "add V3 research evidence references to content briefs"

    async def up(self, database: AsyncDatabase[MongoDocument]) -> None:
        """Add inert defaults, then restore the final strict collection validator."""
        await database.command(
            {
                "collMod": "content_briefs",
                "validator": _intermediate_validator(),
                "validationLevel": "strict",
                "validationAction": "error",
            }
        )
        defaults: Final[dict[str, object]] = {
            "research_project_id": None,
            "claim_refs": [],
            "citation_refs": [],
            "factual_review_status": "not_reviewed",
        }
        for field, value in defaults.items():
            await database["content_briefs"].update_many(
                {field: {"$exists": False}},
                {"$set": {field: value}},
            )
        await database.command(
            {
                "collMod": "content_briefs",
                "validator": COLLECTION_VALIDATORS["content_briefs"],
                "validationLevel": "strict",
                "validationAction": "error",
            }
        )

    async def down(self, database: AsyncDatabase[MongoDocument]) -> None:
        """Do not discard V3 citation provenance stored on existing briefs."""
        _ = database
        raise IrreversibleMigrationError(
            "content brief research references cannot be safely removed"
        )
