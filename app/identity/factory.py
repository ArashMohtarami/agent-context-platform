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
    vault = SeedVault.from_file(settings.v6_mfa_keyring_file)
    service = LocalIdentity(
        IdentityStore(database),
        vault,
        LocalMailSink(settings.v6_mail_sink_directory),
    )
    if settings.v6_sso:
        from app.identity.providers import Registry
        from app.identity.sso import SSOIdentity

        if settings.v6_oidc_registrations_file is None or settings.v6_browser_origin is None:
            raise ValueError("SSO requires approved registrations and browser origin")
        service.sso = SSOIdentity(
            service,
            Registry.from_file(settings.v6_oidc_registrations_file, settings.v6_browser_origin),
            vault,
        )
    if settings.v6_management_policy:
        from app.security.policy.engine import PolicyEngine

        service.policy_engine = PolicyEngine(
            service.store,
            human_management=settings.v6_human_management,
            managed_agents=settings.v6_managed_agents,
        )
        service.sessions.policy_engine = service.policy_engine
    return service
