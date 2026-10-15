"""Explicit installation wiring, shared by the API and trusted host CLI."""

from pymongo.asynchronous.database import AsyncDatabase

from app.core.config import Settings
from app.db.client_types import MongoDocument
from app.identity.crypto import SeedVault
from app.identity.mail import LocalMailSink
from app.identity.repository import IdentityStore
from app.identity.service import LocalIdentity


def build_local_identity(
    settings: Settings, database: AsyncDatabase[MongoDocument]
) -> LocalIdentity:
    if (
        not settings.v6_local_identity
        or settings.v6_mfa_keyring_file is None
        or settings.v6_mail_sink_directory is None
    ):
        raise ValueError("local identity is not configured")
    return LocalIdentity(
        IdentityStore(database),
        SeedVault.from_file(settings.v6_mfa_keyring_file),
        LocalMailSink(settings.v6_mail_sink_directory),
    )
