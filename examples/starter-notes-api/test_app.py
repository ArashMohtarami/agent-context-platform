"""Tests prove the local example's contracts, not production multi-tenant safety."""

# These are behavioral tests, where assertion statements are intentional.
# ruff: noqa: S101

import uuid

import pytest
from fastapi.testclient import TestClient
from notes_app import create_app


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(tmp_path / "notes.sqlite3")) as client:
        yield client


def create(client, key="first-note", **changes):
    return client.post(
        "/notes",
        headers={"Idempotency-Key": key},
        json={"title": "First note", "body": "A small example", **changes},
    )


def test_create_and_read(client):
    response = create(client)
    assert response.status_code == 201
    assert client.get("/notes/" + response.json()["id"]).json() == response.json()
    assert set(response.json()) == {"id", "title", "body"}


def test_same_key_and_payload_replays(client):
    first, second = create(client), create(client)
    assert first.status_code == 201
    assert second.status_code == 200
    assert first.json() == second.json()


def test_changed_payload_with_same_key_conflicts(client):
    original = create(client)
    assert create(client, body="Changed body").status_code == 409
    assert client.get("/notes/" + original.json()["id"]).json() == original.json()


def test_missing_key_is_rejected(client):
    assert client.post("/notes", json={"title": "A", "body": "B"}).status_code == 422


def test_unknown_and_empty_fields_are_rejected(client):
    assert create(client, unexpected="value").status_code == 422
    assert create(client, title="   ").status_code == 422


def test_missing_note_is_not_found(client):
    assert client.get(f"/notes/{uuid.uuid4()}").status_code == 404
