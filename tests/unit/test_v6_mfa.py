"""Authenticated seed encryption, TOTP counters and hashed recovery codes."""

from unittest.mock import AsyncMock, Mock

import pyotp
import pytest
from pydantic import ValidationError

from app.identity.crypto import AuthRejected, SeedVault, digest, random_token
from app.identity.mfa import LocalMFA
from app.models.actor import ActorProvenance, ActorType


def test_seed_encryption_is_random_identity_bound_and_key_versioned():
    vault = SeedVault("one", {"one": b"a" * 32})
    seed = pyotp.random_base32()
    one = vault.encrypt(seed, "user_a", "factor_a")
    two = vault.encrypt(seed, "user_a", "factor_a")
    assert one != two and seed not in repr(one)
    rotated = SeedVault("two", {"one": b"a" * 32, "two": b"b" * 32})
    assert rotated.decrypt(one, "user_a", "factor_a") == seed
    for user, factor in (("user_b", "factor_a"), ("user_a", "factor_b")):
        with pytest.raises(AuthRejected):
            vault.decrypt(one, user, factor)
    with pytest.raises(AuthRejected):
        SeedVault("one", {"one": b"b" * 32}).decrypt(one, "user_a", "factor_a")


async def test_totp_uses_persisted_monotonic_counter_and_recovery_codes_are_atomic_hashes():
    vault = SeedVault("test", {"test": b"a" * 32})
    seed = pyotp.random_base32()
    factor = {
        "_id": "user_a",
        "user_id": "user_a",
        "factor_id": "factor_a",
        "status": "active",
        "encrypted_seed": vault.encrypt(seed, "user_a", "factor_a"),
        "last_step": -1,
        "revision": 1,
    }
    store = Mock(find=AsyncMock(return_value=factor), update=AsyncMock(return_value=factor))
    mfa = LocalMFA(store, vault)
    await mfa.consume("user_a", pyotp.TOTP(seed).now(), Mock())
    query, update = store.update.call_args.args[1:3]
    accepted = update["$set"]["last_step"]
    assert query["last_step"] == {"$lt": accepted}
    factor["last_step"] = accepted
    with pytest.raises(AuthRejected):
        await mfa.consume("user_a", pyotp.TOTP(seed).at(accepted * 30), Mock())
    code = random_token()
    await mfa.consume("user_a", code, Mock(), recovery=True)
    query, update = store.update.call_args.args[1:3]
    assert update["$pull"]["recovery_hashes"] == digest("recovery:user_a", code)
    assert code not in repr(store.update.call_args)
    store.update.return_value = None
    with pytest.raises(AuthRejected):
        await mfa.consume("user_a", code, Mock(), recovery=True)


def test_verified_human_actor_never_fabricates_machine_identity():
    actor = ActorProvenance(
        actor_type=ActorType.HUMAN,
        user_id="user_a",
        org_id="org-a",
        auth_method="local",
        assurance="mfa",
    )
    data = actor.model_dump()
    assert data["user_id"] == "user_a" and "key_id" not in data
    assert data["client_id"] is None and data["client_type"] is None
    with pytest.raises(ValidationError):
        ActorProvenance(actor_type=ActorType.HUMAN, user_id="user_a")
    assert ActorProvenance(actor_type=ActorType.HUMAN).model_dump() == {
        "actor_type": "human",
        "client_id": None,
        "client_type": None,
    }
