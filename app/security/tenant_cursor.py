"""Authenticated, tenant/filter/subject-bound keyset pagination cursors."""

from base64 import urlsafe_b64decode, urlsafe_b64encode
from collections.abc import Mapping
from datetime import datetime
from hashlib import sha256
from hmac import compare_digest, digest
from json import dumps, loads

from bson import ObjectId, json_util

from app.models.common import InvalidCursorError
from app.security.tenant import TenantContextRequired, require_tenant
from app.security.tenant_policy import current_action, current_policy


def binding(collection: str, filters: Mapping[str, object]) -> str:
    policy = current_policy()
    value = json_util.dumps(
        {
            "org": require_tenant().org_id,
            "collection": collection,
            "action": current_action(),
            "filters": dict(filters),
            "policy": policy.fingerprint if policy else "operator",
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return sha256(value.encode()).hexdigest()


def _seal(value: Mapping[str, object]) -> str:
    secret = require_tenant().cursor_secret
    if secret is None or len(secret) < 32:
        raise TenantContextRequired("tenant cursor signing is not configured")
    payload = dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return urlsafe_b64encode(payload + digest(secret, payload, "sha256")).decode().rstrip("=")


def _unseal(cursor: str, scope: str) -> dict[str, object]:
    secret = require_tenant().cursor_secret
    if secret is None or len(secret) < 32:
        raise TenantContextRequired("tenant cursor signing is not configured")
    try:
        if len(cursor) > 2048:
            raise ValueError("oversized cursor")
        encoded = urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4))
        payload, mac = encoded[:-32], encoded[-32:]
        if not compare_digest(digest(secret, payload, "sha256"), mac):
            raise ValueError("invalid cursor signature")
        value = loads(payload)
        if not isinstance(value, dict) or value.get("v") != 2 or value.get("binding") != scope:
            raise ValueError("cursor context mismatch")
        return value
    except (ValueError, TypeError, KeyError) as error:
        raise InvalidCursorError("cursor is invalid for this tenant and query") from error


def encode_cursor(when: datetime, identifier: ObjectId, scope: str) -> str:
    return _seal({"v": 2, "at": when.isoformat(), "id": str(identifier), "binding": scope})


def decode_cursor(cursor: str, scope: str) -> tuple[datetime, ObjectId]:
    try:
        value = _unseal(cursor, scope)
        when = datetime.fromisoformat(str(value["at"]))
        if when.tzinfo is None:
            raise ValueError("cursor timestamp requires timezone")
        return when, ObjectId(str(value["id"]))
    except (ValueError, TypeError, KeyError) as error:
        raise InvalidCursorError("cursor is invalid for this tenant and query") from error


def encode_chunk_cursor(index: int, identifier: ObjectId, scope: str) -> str:
    return _seal({"v": 2, "index": index, "id": str(identifier), "binding": scope})


def decode_chunk_cursor(cursor: str, scope: str) -> tuple[int, ObjectId]:
    try:
        value = _unseal(cursor, scope)
        index = value["index"]
        if isinstance(index, bool) or not isinstance(index, int) or index < 0:
            raise ValueError("invalid chunk position")
        return index, ObjectId(str(value["id"]))
    except (ValueError, TypeError, KeyError) as error:
        raise InvalidCursorError("cursor is invalid for this tenant and query") from error


def encode_directory_cursor(identifier: str, scope: str) -> str:
    return _seal({"v": 2, "id": identifier, "binding": scope})


def decode_directory_cursor(cursor: str, scope: str) -> str:
    value = _unseal(cursor, scope).get("id")
    if not isinstance(value, str) or not 1 <= len(value) <= 128:
        raise InvalidCursorError("cursor is invalid for this tenant and query")
    return value
