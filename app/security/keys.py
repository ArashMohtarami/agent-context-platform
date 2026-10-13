"""Minting and verifying API keys.

A key is shown once, at creation, and never again: what is stored is a hash, so
a copy of the database is not a set of working credentials.

Format:

    acp_<key_id>_<secret>

The identifier is public and is what a lookup is done by, so verification is
one indexed read rather than a scan over every key. The secret is 256 bits of
CSPRNG output.

On hashing: this is SHA-256, not a password KDF, and that is deliberate. A KDF
exists to make guessing a *low-entropy* human-chosen secret expensive. There is
nothing to guess here — the secret is 256 random bits, so no dictionary, rainbow
table, or brute-force search is feasible at any iteration count. What a KDF
would add is tens of milliseconds to every single authenticated request. The
comparison is constant-time, which is the property that actually matters.
"""

import hashlib
import hmac
import secrets
from dataclasses import dataclass
from typing import Final

# Recognizable on sight and greppable by a secret scanner, which is the point
# of a prefix: a key pasted into an issue should be findable.
PREFIX: Final = "acp"
_SEPARATOR: Final = "_"
KEY_ID_BYTES: Final = 8
SECRET_BYTES: Final = 32
# 16 hex characters from 8 random bytes.
KEY_ID_LENGTH: Final = KEY_ID_BYTES * 2
# The shortest a presented key could legitimately be, used to reject nonsense
# before any work is done.
MIN_SECRET_LENGTH: Final = 32


class MalformedKeyError(ValueError):
    """The presented credential is not shaped like a key at all."""


@dataclass(frozen=True, slots=True)
class MintedKey:
    """A newly created key: the identifier, the hash to store, and the secret.

    `token` is the only time the usable credential exists. It is returned so it
    can be shown once and is never written anywhere by this module.
    """

    key_id: str
    key_hash: str
    token: str


@dataclass(frozen=True, slots=True)
class PresentedKey:
    """A credential a caller sent, split into the part used to find it and the
    part used to prove it."""

    key_id: str
    secret: str


def mint() -> MintedKey:
    """Create one key, returning the secret exactly once."""
    key_id = secrets.token_hex(KEY_ID_BYTES)
    secret = secrets.token_urlsafe(SECRET_BYTES)
    return MintedKey(
        key_id=key_id,
        key_hash=hash_secret(secret),
        token=f"{PREFIX}{_SEPARATOR}{key_id}{_SEPARATOR}{secret}",
    )


def hash_secret(secret: str) -> str:
    """Hash the secret half of a key for storage."""
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()


def parse(token: str) -> PresentedKey:
    """Split a presented credential, without deciding whether it is valid.

    A malformed credential is refused here rather than turned into a database
    query, so nothing a caller sends reaches storage as a lookup value until it
    has the shape of a key.
    """
    candidate = token.strip()
    parts = candidate.split(_SEPARATOR, 2)
    if len(parts) != 3:
        raise MalformedKeyError("an API key looks like acp_<id>_<secret>")
    prefix, key_id, secret = parts
    if prefix != PREFIX:
        raise MalformedKeyError("an API key looks like acp_<id>_<secret>")
    if len(key_id) != KEY_ID_LENGTH or not _is_hex(key_id):
        raise MalformedKeyError("an API key looks like acp_<id>_<secret>")
    if len(secret) < MIN_SECRET_LENGTH:
        raise MalformedKeyError("an API key looks like acp_<id>_<secret>")
    return PresentedKey(key_id=key_id, secret=secret)


def matches(secret: str, key_hash: str) -> bool:
    """Report whether a secret hashes to the stored value, in constant time.

    Constant time so the comparison cannot be turned into an oracle that leaks
    the stored hash one byte at a time.
    """
    return hmac.compare_digest(hash_secret(secret), key_hash)


def _is_hex(value: str) -> bool:
    """Report whether every character is a lowercase hexadecimal digit."""
    return all(character in "0123456789abcdef" for character in value)
