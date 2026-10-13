"""V3 Phase 11 additive persistence for generated skill update proposals."""

from typing import Final

from pymongo.asynchronous.database import AsyncDatabase

from app.db.client_types import MongoDocument
from app.db.migrations.base import IrreversibleMigrationError
from app.db.schema import COLLECTION_VALIDATORS

SKILL_UPDATE_PROPOSAL_COLLECTION: Final = "skill_update_proposals"


class SkillUpdateProposalMigration:
    """Create the review-only proposal collection with a strict validator.

    The migration deliberately never reads or writes canonical Markdown, Git,
    the V1 ``skills`` collection, or the V1 importer.
    """

    @property
    def version(self) -> int:
        return 19

    @property
    def description(self) -> str:
        return "create V3 generated skill update proposals"

    async def up(self, database: AsyncDatabase[MongoDocument]) -> None:
        existing = set(await database.list_collection_names())
        validator = COLLECTION_VALIDATORS[SKILL_UPDATE_PROPOSAL_COLLECTION]
        if SKILL_UPDATE_PROPOSAL_COLLECTION in existing:
            await database.command(
                {
                    "collMod": SKILL_UPDATE_PROPOSAL_COLLECTION,
                    "validator": validator,
                    "validationLevel": "strict",
                    "validationAction": "error",
                }
            )
            return
        await database.create_collection(
            SKILL_UPDATE_PROPOSAL_COLLECTION,
            validator=validator,
            validationLevel="strict",
            validationAction="error",
        )

    async def down(self, database: AsyncDatabase[MongoDocument]) -> None:
        _ = database
        raise IrreversibleMigrationError(
            "skill update proposal rollback would discard generated review evidence"
        )
