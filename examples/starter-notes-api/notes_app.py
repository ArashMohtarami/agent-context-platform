"""A local teaching API: repeat a keyed create safely or receive a payload conflict."""

import asyncio
import hashlib
import json
import sqlite3
import uuid
from contextlib import asynccontextmanager, closing
from pathlib import Path
from typing import Annotated

from fastapi import FastAPI, Header, HTTPException, Response
from pydantic import BaseModel, ConfigDict, StringConstraints


class NoteInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]
    body: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)]


class Note(NoteInput):
    id: str


def initialize(database: Path) -> None:
    with closing(sqlite3.connect(database)) as connection:
        connection.execute(
            "CREATE TABLE IF NOT EXISTS notes "
            "(id TEXT PRIMARY KEY, operation_key TEXT UNIQUE NOT NULL, "
            "payload_hash TEXT NOT NULL, title TEXT NOT NULL, body TEXT NOT NULL)"
        )
        connection.commit()


def create_app(database: Path | None = None) -> FastAPI:
    database = database or Path(__file__).resolve().parent / "notes.sqlite3"

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        await asyncio.to_thread(initialize, database)
        yield

    app = FastAPI(title="Teaching Notes API", lifespan=lifespan)

    @app.post("/notes", response_model=Note, status_code=201)
    def create_note(
        note: NoteInput,
        response: Response,
        idempotency_key: Annotated[str, Header(min_length=1, max_length=128)],
    ):
        digest = hashlib.sha256(json.dumps(note.model_dump(), sort_keys=True).encode()).hexdigest()
        with closing(sqlite3.connect(database, timeout=5)) as connection:
            connection.row_factory = sqlite3.Row
            connection.execute("BEGIN IMMEDIATE")
            previous = connection.execute(
                "SELECT id, payload_hash, title, body FROM notes WHERE operation_key = ?",
                (idempotency_key,),
            ).fetchone()
            if previous is not None:
                if previous["payload_hash"] != digest:
                    raise HTTPException(409, "The key already belongs to a different payload")
                response.status_code = 200
                return {field: previous[field] for field in ("id", "title", "body")}
            identifier = str(uuid.uuid4())
            connection.execute(
                "INSERT INTO notes (id, operation_key, payload_hash, title, body) "
                "VALUES (?, ?, ?, ?, ?)",
                (identifier, idempotency_key, digest, note.title, note.body),
            )
            connection.commit()
        return {"id": identifier, **note.model_dump()}

    @app.get("/notes/{note_id}", response_model=Note)
    def get_note(note_id: uuid.UUID):
        with closing(sqlite3.connect(database)) as connection:
            connection.row_factory = sqlite3.Row
            row = connection.execute(
                "SELECT id, title, body FROM notes WHERE id = ?", (str(note_id),)
            ).fetchone()
        if row is None:
            raise HTTPException(404, "Note not found")
        return dict(row)

    return app


app = create_app()
