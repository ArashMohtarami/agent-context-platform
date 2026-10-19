"""Non-secret recovery commitments; encryption keys never enter the DB archive."""

import base64
import hashlib
import hmac
import json
from pathlib import Path

from app.core.config import Settings
from app.identity.crypto import SeedVault


def key_commitments(path: Path) -> dict[str, str]:
    """Validate a mounted keyring and commit to its random 256-bit key bytes."""
    try:
        SeedVault.from_file(path)
        data = json.loads(path.read_text())
        return {
            name: hashlib.sha256(base64.urlsafe_b64decode(value)).hexdigest()
            for name, value in data["keys"].items()
        }
    except Exception:
        raise ValueError("recovery requires the approved mounted MFA keyring") from None


def required_keys(settings: Settings) -> dict[str, str]:
    if not settings.v6_local_identity:
        return {}
    if settings.v6_mfa_keyring_file is None:
        raise ValueError("recovery requires the approved mounted MFA keyring")
    return key_commitments(settings.v6_mfa_keyring_file)


def check_keys(settings: Settings, expected: dict[str, str]) -> None:
    if not expected:
        return
    actual = required_keys(settings)
    if any(not hmac.compare_digest(actual.get(key, ""), value) for key, value in expected.items()):
        raise ValueError("recovery keyring does not match the backup's required encryption keys")
