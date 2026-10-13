"""Apply the V4 actor contract to the V3 Phase 11 proposal collection."""

from pymongo.asynchronous.database import AsyncDatabase

from app.db.client_types import MongoDocument
from app.db.migrations.base import IrreversibleMigrationError
from app.db.schema import COLLECTION_VALIDATORS
from app.models.actor import system_actor

_COLLECTION = "skill_update_proposals"
_LEGACY_ACTOR: MongoDocument = system_actor().model_dump(mode="python")


class SkillUpdateProposalActorMigration:
    """Backfill provenance for a V3 collection added after migration 13.

    The broader actor migration intentionally derives its collection list from
    the Phase 0 V3 schema.  Phase 11 added skill-update proposals later, so
    this follow-up migration reconciles its strict validator and preserves any
    pre-provenance records as system-authored history.
    """

    @property
    def version(self) -> int:
        return 32

    @property
    def description(self) -> str:
        return "backfill actor provenance for V3 skill update proposals"

    async def up(self, database: AsyncDatabase[MongoDocument]) -> None:
        """Allow and require actor provenance, then backfill legacy proposals."""
        await database.command(
            {
                "collMod": _COLLECTION,
                "validator": COLLECTION_VALIDATORS[_COLLECTION],
                "validationLevel": "strict",
                "validationAction": "error",
            }
        )
        await database[_COLLECTION].update_many(
            {"actor": {"$exists": False}}, {"$set": {"actor": _LEGACY_ACTOR}}
        )

    async def down(self, database: AsyncDatabase[MongoDocument]) -> None:
        """Do not erase actor history during rollback."""
        _ = database
        raise IrreversibleMigrationError(
            "skill update proposal actor provenance cannot be removed safely"
        )
