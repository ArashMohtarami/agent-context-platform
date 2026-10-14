"""Operator-only maintenance persistence; no access through domain repositories."""

from collections.abc import AsyncIterator
from datetime import timedelta
from hashlib import sha256

from bson import BSON
from bson.binary import Binary
from pymongo import ReturnDocument
from pymongo.asynchronous.client_session import AsyncClientSession
from pymongo.asynchronous.database import AsyncDatabase
from pymongo.read_concern import ReadConcern
from pymongo.write_concern import WriteConcern

from app.db.client_types import MongoDocument
from app.db.tenant_schema import TENANT_COLLECTIONS, TENANT_INDEX_DEFINITIONS, tenant_validator
from app.models.common import utc_now


class AdoptionConflict(RuntimeError):
    """A maintenance lease, ownership or preserved source no longer matches."""


def preservation_id(collection: str, document_id: object) -> str:
    return sha256(BSON.encode({"collection": collection, "id": document_id})).hexdigest()


class MongoTenantAdoptionRepository:
    def __init__(self, database: AsyncDatabase[MongoDocument]) -> None:
        self.database = database
        self.control = database["_tenant_control"]

    async def state(self) -> MongoDocument | None:
        return await self.control.find_one({"_id": "installation"})

    async def counts(self) -> dict[str, int]:
        return {
            name: await self.database[name].count_documents({})
            for name in sorted(await self.database.list_collection_names())
        }

    async def documents(self, collection: str) -> AsyncIterator[MongoDocument]:
        async for raw in self.database[collection].find({}).sort("_id", 1):
            yield raw

    async def referenced(self, collection: str, query: MongoDocument) -> list[MongoDocument]:
        return await self.database[collection].find(query).limit(2).to_list(length=2)

    async def valid_candidate(
        self, collection: str, candidate: MongoDocument, org_id: str, adoption_id: str
    ) -> bool:
        # collMod does not retroactively validate old rows, and model defaults
        # must never invent missing persisted timestamps/revisions for adoption.
        cursor = await self.database[collection].aggregate(
            [
                {"$match": {"_id": candidate["_id"]}},
                {"$replaceWith": {"$literal": candidate}},
                {"$match": tenant_validator(collection, org_id=org_id, adoption_id=adoption_id)},
                {"$project": {"_id": 1}},
                {"$limit": 1},
            ]
        )
        return bool(await cursor.to_list(length=1))

    async def preservation(self, collection: str, document_id: object) -> MongoDocument | None:
        return await self.database["_tenant_preservation"].find_one(
            {"_id": preservation_id(collection, document_id)}
        )

    async def unresolved_quarantine(self) -> int:
        return await self.database["_tenant_quarantine"].count_documents({"status": "unresolved"})

    async def acquire(self, org_id: str, adoption_id: str, lease: str, backup_sha256: str) -> None:
        now = utc_now()
        raw = await self.control.find_one_and_update(
            {
                "_id": "installation",
                "stage": {"$in": ["not_adopted", "adopting", "blocked"]},
                "$and": [
                    {"$or": [{"adoption_org_id": org_id}, {"adoption_org_id": {"$exists": False}}]},
                    {"$or": [{"adoption_id": adoption_id}, {"adoption_id": {"$exists": False}}]},
                    {"$or": [{"lease_until": {"$lte": now}}, {"lease_until": {"$exists": False}}]},
                ],
            },
            {
                "$set": {
                    "stage": "adopting",
                    "adoption_org_id": org_id,
                    "adoption_id": adoption_id,
                    "lease": lease,
                    "lease_until": now + timedelta(minutes=5),
                    "backup_sha256": backup_sha256,
                    "domain_traffic_enabled": False,
                }
            },
            return_document=ReturnDocument.AFTER,
        )
        if raw is None:
            raise AdoptionConflict("adoption context or maintenance lease conflicts")

    async def heartbeat(self, lease: str) -> None:
        result = await self.control.update_one(
            {"_id": "installation", "lease": lease},
            {"$set": {"lease_until": utc_now() + timedelta(minutes=5)}},
        )
        if result.matched_count != 1:
            raise AdoptionConflict("maintenance lease was lost")

    async def finish(self, lease: str, stage: str, counts: dict[str, int]) -> None:
        result = await self.control.update_one(
            {"_id": "installation", "lease": lease},
            {
                "$set": {"stage": stage, "counts": counts, "domain_traffic_enabled": False},
                "$unset": {"lease": "", "lease_until": ""},
            },
        )
        if result.matched_count != 1:
            raise AdoptionConflict("maintenance lease was lost")

    async def release(self, lease: str) -> None:
        await self.control.update_one(
            {"_id": "installation", "lease": lease}, {"$unset": {"lease": "", "lease_until": ""}}
        )

    async def widen_for_adoption(self, org_id: str, adoption_id: str) -> None:
        from app.db.schema import COLLECTION_VALIDATORS

        for name in TENANT_COLLECTIONS:
            await self.database.command(
                {
                    "collMod": name,
                    "validator": {
                        "$or": [
                            COLLECTION_VALIDATORS[name],
                            tenant_validator(name, org_id=org_id, adoption_id=adoption_id),
                        ]
                    },
                    "validationLevel": "strict",
                    "validationAction": "error",
                }
            )

    async def _adopt_write(
        self,
        collection: str,
        raw: MongoDocument,
        changes: MongoDocument,
        session: AsyncClientSession,
    ) -> None:
        result = await self.database[collection].update_one(
            {"_id": raw["_id"], "$expr": {"$eq": ["$$ROOT", {"$literal": raw}]}},
            {"$set": changes},
            session=session,
        )
        if result.matched_count != 1:
            raise AdoptionConflict("source changed while adoption was running")

    async def preserve_and_adopt(
        self, collection: str, raw: MongoDocument, org_id: str, adoption_id: str, lease: str
    ) -> None:
        encoded = BSON.encode(raw)
        identifier = preservation_id(collection, raw["_id"])

        async def transaction(session: AsyncClientSession) -> None:
            # This shared anchor write conflicts with another maintenance owner.
            anchor = await self.control.update_one(
                {"_id": "installation", "lease": lease, "stage": "adopting"},
                {"$inc": {"fence": 1}},
                session=session,
            )
            if anchor.matched_count != 1:
                raise AdoptionConflict("maintenance lease was lost")
            existing = await self.database["_tenant_preservation"].find_one(
                {"_id": identifier}, session=session
            )
            if existing is None:
                modes: dict[str, str] = {}
                if collection == "api_keys":
                    modes = {
                        key: "include" if raw.get(key) else "all_in_org"
                        for key in ("projects", "profiles")
                    }
                elif collection == "agent_clients":
                    modes = {"projects": "include" if raw.get("project_ids") else "all_in_org"}
                await self.database["_tenant_preservation"].insert_one(
                    {
                        "_id": identifier,
                        "collection": collection,
                        "document_id": raw["_id"],
                        "org_id": org_id,
                        "adoption_id": adoption_id,
                        "original": Binary(encoded),
                        "sha256": sha256(encoded).hexdigest(),
                        "legacy_resource_modes": modes,
                    },
                    session=session,
                )
            elif (
                existing.get("adoption_id") != adoption_id
                or existing.get("original") != encoded
                or existing.get("sha256") != sha256(encoded).hexdigest()
            ):
                raise AdoptionConflict("preservation manifest does not match this adoption/source")
            await self._adopt_write(
                collection,
                raw,
                {
                    "org_id": org_id,
                    "identity_version": 1,
                    "legacy_adoption_id": adoption_id,
                },
                session,
            )

        async with self.database.client.start_session() as session:
            await session.with_transaction(
                transaction,
                read_concern=ReadConcern("snapshot"),
                write_concern=WriteConcern("majority"),
            )

    async def quarantine(
        self, collection: str, raw: MongoDocument, reason: str, adoption_id: str, lease: str
    ) -> None:
        encoded = BSON.encode(raw)
        identifier = preservation_id(collection, raw["_id"])

        async def transaction(session: AsyncClientSession) -> None:
            anchor = await self.control.update_one(
                {"_id": "installation", "lease": lease}, {"$inc": {"fence": 1}}, session=session
            )
            if anchor.matched_count != 1:
                raise AdoptionConflict("maintenance lease was lost")
            existing = await self.database["_tenant_quarantine"].find_one(
                {"_id": identifier}, session=session
            )
            if existing is not None and (
                existing.get("original") != encoded or existing.get("adoption_id") != adoption_id
            ):
                raise AdoptionConflict("existing quarantine does not match this source")
            await self.database["_tenant_quarantine"].update_one(
                {"_id": identifier},
                {
                    "$setOnInsert": {
                        "collection": collection,
                        "document_id": raw["_id"],
                        "adoption_id": adoption_id,
                        "original": Binary(encoded),
                        "sha256": sha256(encoded).hexdigest(),
                        "reason": reason,
                        "status": "unresolved",
                    }
                },
                upsert=True,
                session=session,
            )
            result = await self.database[collection].delete_one(
                {"_id": raw["_id"], "$expr": {"$eq": ["$$ROOT", {"$literal": raw}]}},
                session=session,
            )
            if result.deleted_count != 1:
                raise AdoptionConflict("quarantine source changed")

        async with self.database.client.start_session() as session:
            await session.with_transaction(
                transaction,
                read_concern=ReadConcern("snapshot"),
                write_concern=WriteConcern("majority"),
            )

    async def install_contracts(self, org_id: str, adoption_id: str) -> None:
        from app.db.indexes import INDEX_DEFINITIONS

        for name, definitions in TENANT_INDEX_DEFINITIONS.items():
            await self.database.command(
                {
                    "collMod": name,
                    "validator": tenant_validator(name, org_id=org_id, adoption_id=adoption_id),
                    "validationLevel": "strict",
                    "validationAction": "error",
                }
            )
            await self.database[name].create_indexes(list(definitions))
            information = await self.database[name].index_information()
            preserved = {str(index.document["name"]) for index in definitions}
            for old in INDEX_DEFINITIONS[name]:
                old_name = str(old.document["name"])
                if old_name in information and old_name not in preserved:
                    await self.database[name].drop_index(old_name)
