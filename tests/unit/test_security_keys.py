"""The credential itself: how one is made, and what a presented one has to be.

This is the smallest layer, and the one everything else trusts. If parsing let
a value through that is not shaped like a key, that value would become a
database lookup; if hashing were reversible, a dump of the collection would be
a set of working credentials.
"""

import pytest

from app.security.keys import (
    KEY_ID_LENGTH,
    MIN_SECRET_LENGTH,
    PREFIX,
    MalformedKeyError,
    hash_secret,
    matches,
    mint,
    parse,
)


def test_a_minted_key_is_the_identifier_the_hash_and_the_token() -> None:
    minted = mint()

    assert minted.token == f"{PREFIX}_{minted.key_id}_{parse(minted.token).secret}"
    assert len(minted.key_id) == KEY_ID_LENGTH
    assert minted.key_hash == hash_secret(parse(minted.token).secret)


def test_the_stored_hash_is_not_the_secret() -> None:
    minted = mint()
    secret = parse(minted.token).secret

    # The obvious failure this rules out: storing the credential under another
    # name and calling it a hash.
    assert secret not in minted.key_hash
    assert minted.token not in minted.key_hash
    assert len(minted.key_hash) == 64


def test_every_key_is_different() -> None:
    minted = [mint() for _ in range(64)]

    assert len({key.key_id for key in minted}) == 64
    assert len({key.token for key in minted}) == 64


def test_the_secret_carries_enough_entropy_to_be_unguessable() -> None:
    # 32 random bytes, URL-safe base64: what makes a plain SHA-256 the right
    # store rather than a password KDF.
    secret = parse(mint().token).secret

    assert len(secret) >= MIN_SECRET_LENGTH


def test_a_minted_key_round_trips_through_parsing() -> None:
    minted = mint()

    presented = parse(minted.token)

    assert presented.key_id == minted.key_id
    assert matches(presented.secret, minted.key_hash)


def test_a_secret_containing_a_separator_survives_parsing() -> None:
    # The alphabet the secret is drawn from includes `_`, which is also the
    # field separator. Splitting from the right would cut a valid key in the
    # wrong place and refuse roughly one credential in three.
    token = f"{PREFIX}_{'a' * KEY_ID_LENGTH}_{'x' * 20}_{'y' * 20}"

    presented = parse(token)

    assert presented.secret == f"{'x' * 20}_{'y' * 20}"


def test_surrounding_whitespace_is_tolerated() -> None:
    minted = mint()

    assert parse(f"  {minted.token}\n").key_id == minted.key_id


@pytest.mark.parametrize(
    "token",
    [
        "",
        "   ",
        "nonsense",
        "acp",
        "acp_",
        "acp_0123456789abcdef",
        # Wrong prefix, right shape.
        f"xyz_{'a' * KEY_ID_LENGTH}_{'s' * 40}",
        # Identifier too short, too long, and not hexadecimal.
        f"{PREFIX}_{'a' * (KEY_ID_LENGTH - 1)}_{'s' * 40}",
        f"{PREFIX}_{'a' * (KEY_ID_LENGTH + 1)}_{'s' * 40}",
        f"{PREFIX}_{'g' * KEY_ID_LENGTH}_{'s' * 40}",
        f"{PREFIX}_{'A' * KEY_ID_LENGTH}_{'s' * 40}",
        # Secret too short to be one.
        f"{PREFIX}_{'a' * KEY_ID_LENGTH}_short",
        f"{PREFIX}_{'a' * KEY_ID_LENGTH}_",
    ],
)
def test_anything_not_shaped_like_a_key_is_refused_before_a_lookup(token: str) -> None:
    with pytest.raises(MalformedKeyError):
        parse(token)


@pytest.mark.parametrize(
    "injection",
    [
        '{"$ne": null}',
        "acp_{'$gt': ''}_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        f"{PREFIX}_{{'$ne':null}}_{'s' * 40}",
        f"{PREFIX}_../../etc/passwd_{'s' * 40}",
        f"{PREFIX}_0123456789abcde*_{'s' * 40}",
    ],
)
def test_a_crafted_identifier_never_becomes_a_query(injection: str) -> None:
    # The identifier is what a lookup filters on. Requiring exactly 16 lowercase
    # hexadecimal characters means an operator, a shell, or an attacker cannot
    # put an object, an operator expression, or a path into that position.
    with pytest.raises(MalformedKeyError):
        parse(injection)


def test_a_wrong_secret_does_not_match() -> None:
    minted = mint()

    assert not matches("not the secret", minted.key_hash)
    assert not matches("", minted.key_hash)


def test_a_secret_matching_another_key_does_not_match_this_one() -> None:
    first, second = mint(), mint()

    assert not matches(parse(first.token).secret, second.key_hash)


def test_hashing_is_deterministic_and_specific() -> None:
    assert hash_secret("a secret") == hash_secret("a secret")
    assert hash_secret("a secret") != hash_secret("a secreu")
