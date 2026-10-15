"""Narrow private identity store; never exposed by tenant corpus repositories."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any, cast

from pymongo import ReturnDocument
from pymongo.asynchronous.client_session import AsyncClientSession
from pymongo.asynchronous.collection import AsyncCollection
from pymongo.asynchronous.database import AsyncDatabase

from app.db.client_types import MongoDocument

type Row = dict[str, Any]

COLLECTION_OWNERSHIP = {
    "users": "private global human identity",
    "user_identities": "private global human identity",
    "local_credentials": "private global human identity",
    "mfa_factors": "private global human identity",
    "browser_sessions": "private global human identity",
    "auth_challenges": "private global human identity",
    "auth_throttles": "private global human security state",
    "identity_audit_events": "private global human security history",
    "organization_memberships": "tenant owned; self-only identity boundary or explicit operator",
    "_local_identity_control": "installation metadata",
}


class IdentityStore:
    def __init__(self, database: AsyncDatabase[MongoDocument]) -> None:
        self.database = database

    def _collection(self, name: str) -> AsyncCollection[Row]:
        if name not in COLLECTION_OWNERSHIP and name not in {"organizations", "_tenant_control"}:
            raise ValueError("unclassified identity collection")
        return cast(AsyncCollection[Row], self.database[name])

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[AsyncClientSession]:
        async with self.database.client.start_session() as session:
            async with await session.start_transaction():
                yield session

    async def find(
        self, name: str, query: Row, session: AsyncClientSession | None = None
    ) -> Row | None:
        return await self._collection(name).find_one(query, session=session)

    async def insert(self, name: str, row: Row, session: AsyncClientSession | None = None) -> None:
        await self._collection(name).insert_one(row, session=session)

    async def update(
        self, name: str, query: Row, change: Row, session: AsyncClientSession | None = None
    ) -> Row | None:
        return await self._collection(name).find_one_and_update(
            query, change, session=session, return_document=ReturnDocument.AFTER
        )

    async def list(
        self, name: str, query: Row, session: AsyncClientSession | None = None
    ) -> list[Row]:
        return await self._collection(name).find(query, session=session).limit(101).to_list(101)
