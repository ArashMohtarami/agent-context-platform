"""Bounded Argon2id work and authenticated, identity-bound MFA encryption."""

import asyncio
import base64
import hashlib
import json
import secrets
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from argon2 import PasswordHasher, Type, extract_parameters
from argon2.exceptions import InvalidHashError, VerificationError
from cryptography.hazmat.primitives.ciphers.aead import AESGCM


class AuthRejected(ValueError):
    """A deliberately generic authentication refusal."""


class AuthBusy(ValueError):
    """The bounded authentication work budget is exhausted."""


def random_token() -> str:
    return secrets.token_urlsafe(32)


def digest(purpose: str, value: str) -> str:
    return hashlib.sha256(f"acp-human-v1\0{purpose}\0{value}".encode()).hexdigest()


def csrf(token: str) -> str:
    return digest("csrf", token)


def valid_token(value: str) -> bool:
    return 32 <= len(value) <= 128 and all(c.isalnum() or c in "-_" for c in value)


def validate_password(value: str) -> None:
    if not 12 <= len(value.encode()) <= 1024:
        raise AuthRejected("credentials were not accepted")


class PasswordWork:
    """Four hashes maximum, no waiting queue; cancellation cannot free a live slot."""

    def __init__(self) -> None:
        self.hasher = PasswordHasher(
            time_cost=3, memory_cost=65536, parallelism=4, hash_len=32, salt_len=16, type=Type.ID
        )
        self._pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="local-auth")
        self._active = 0
        self.dummy = self.hasher.hash(random_token())

    async def _work[T](self, function: Callable[..., T], *args: str) -> T:
        if self._active >= 4:
            raise AuthBusy("authentication is temporarily busy")
        self._active += 1
        loop = asyncio.get_running_loop()
        future = loop.run_in_executor(self._pool, function, *args)
        future.add_done_callback(lambda _: setattr(self, "_active", self._active - 1))
        return await asyncio.shield(future)

    async def hash(self, password: str) -> str:
        validate_password(password)
        return await self._work(self.hasher.hash, password)

    async def verify(self, encoded: str | None, password: str) -> bool:
        # Only installation-controlled, bounded PHC parameters reach native code.
        target = encoded or self.dummy
        try:
            parameters = extract_parameters(target)
            if (
                parameters.type != Type.ID
                or parameters.memory_cost != 65536
                or parameters.time_cost != 3
                or parameters.parallelism != 4
                or parameters.hash_len != 32
                or parameters.salt_len != 16
            ):
                target = self.dummy
                encoded = None
            result = await self._work(self.hasher.verify, target, password[:1024])
            return bool(result and encoded and len(password.encode()) <= 1024)
        except (VerificationError, InvalidHashError):
            return False

    def close(self) -> None:
        self._pool.shutdown(wait=False)


class SeedVault:
    """Versioned AES-256-GCM keyring, mounted by the installation operator."""

    def __init__(self, active: str, keys: dict[str, bytes]) -> None:
        if active not in keys or not keys or len(keys) > 8:
            raise ValueError("a bounded active MFA keyring is required")
        if any(len(key) != 32 for key in keys.values()):
            raise ValueError("MFA encryption keys must be 32 bytes")
        self.active = active
        self._keys = {name: AESGCM(key) for name, key in keys.items()}

    @classmethod
    def from_file(cls, path: Path) -> "SeedVault":
        if path.stat().st_size > 16384:
            raise ValueError("MFA keyring is too large")
        data = json.loads(path.read_text())
        return cls(
            data["active"],
            {name: base64.urlsafe_b64decode(key) for name, key in data["keys"].items()},
        )

    @staticmethod
    def _aad(user_id: str, factor_id: str) -> bytes:
        return json.dumps(["acp-mfa", 1, user_id, factor_id], separators=(",", ":")).encode()

    def encrypt(self, seed: str, user_id: str, factor_id: str) -> dict[str, str]:
        nonce = secrets.token_bytes(12)
        ciphertext = self._keys[self.active].encrypt(
            nonce, seed.encode(), self._aad(user_id, factor_id)
        )
        return {
            "key_id": self.active,
            "ciphertext": base64.urlsafe_b64encode(nonce + ciphertext).decode(),
        }

    def decrypt(self, value: dict[str, str], user_id: str, factor_id: str) -> str:
        try:
            raw = base64.urlsafe_b64decode(value["ciphertext"])
            return (
                self._keys[value["key_id"]]
                .decrypt(raw[:12], raw[12:], self._aad(user_id, factor_id))
                .decode()
            )
        except Exception:
            raise AuthRejected("credentials were not accepted") from None
