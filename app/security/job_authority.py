"""Recheck original authority before work, every persistence step and replay."""

from collections.abc import Awaitable, Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

from app.models.agent import AgentClientDocument
from app.models.api_key import ApiKeyDocument
from app.models.common import utc_now
from app.models.job_authority import JobAuthority
from app.security.tenant import require_tenant
from app.security.tenant_policy import current_policy
from app.services.auth_service import principal_of


class JobAuthorityError(ValueError):
    """No initiating authority remains for this job and target."""


_recheck: ContextVar[Callable[[], Awaitable[None]] | None] = ContextVar(
    "job_authority_recheck", default=None
)


async def recheck_job_authority() -> None:
    checker = _recheck.get()
    if checker is not None:
        await checker()


@contextmanager
def job_authority_scope(checker: Callable[[], Awaitable[None]]) -> Iterator[None]:
    token = _recheck.set(checker)
    try:
        yield
    finally:
        _recheck.reset(token)


class MongoJobAuthorityChecker:
    def __init__(
        self, database: Any, *, worker_schema_version: int | None = None, human_sessions: Any = None
    ) -> None:
        if worker_schema_version not in {None, 37, 40, 41, 42}:
            raise JobAuthorityError("mixed or unscoped worker schema is refused")
        self._database = database
        self._human_sessions = human_sessions
        self._worker_schema_version = worker_schema_version

    async def check(self, authority: JobAuthority) -> None:
        if authority.org_id != require_tenant().org_id:
            raise JobAuthorityError("job context is unavailable")
        control = await self._database["_tenant_control"].find_one({"_id": "installation"})
        managed = bool(control and control.get("stage") == "managed")
        versions = control.get("worker_schema_versions") if control else None
        expected = (
            42
            if managed and versions == [42]
            else 41
            if managed and versions == [41]
            else 40
            if managed and versions == [40]
            else 37
        )
        if managed and versions not in ([40], [41], [42]):
            raise JobAuthorityError("mixed policy workers are refused")
        if authority.worker_schema_version != expected or (
            self._worker_schema_version is not None and self._worker_schema_version != expected
        ):
            raise JobAuthorityError("mixed policy workers are refused")
        if authority.subject_kind == "human":
            await self._human(authority)
            return
        if authority.key_id is None:
            policy = current_policy()
            if policy is not None and not policy.operator:
                raise JobAuthorityError("operator authority is unavailable")
            return
        org = {"org_id": authority.org_id}
        key = await self._database["api_keys"].find_one({**org, "key_id": authority.key_id})
        control = await self._database["_tenant_control"].find_one({"_id": "installation"})
        organization = await self._database["organizations"].find_one(org)
        if (
            not key
            or key.get("status") != "active"
            or not control
            or control.get("schema_version") != 37
            or control.get("stage") != ("managed" if managed else "isolated")
            or control.get("domain_traffic_enabled") is not True
            or not organization
            or organization.get("status") != "active"
        ):
            raise JobAuthorityError("initiating authority is unavailable")
        record = ApiKeyDocument.model_validate(key, context={"from_storage": True})
        if record.is_expired(utc_now()):
            raise JobAuthorityError("initiating authority expired")
        client = None
        if record.client_id is not None:
            raw = await self._database["agent_clients"].find_one(
                {**org, "client_id": record.client_id}
            )
            if raw is None or raw.get("status") != "active":
                raise JobAuthorityError("initiating client is unavailable")
            client = AgentClientDocument.model_validate(raw, context={"from_storage": True})
        principal = principal_of(record, client)
        if managed:
            from app.identity.repository import IdentityStore
            from app.security.policy.engine import PolicyEngine
            from app.security.policy.evaluation import PolicyDenied

            try:
                principal = await PolicyEngine(
                    IdentityStore(self._database),
                    human_management=expected >= 41,
                    managed_agents=expected == 42,
                ).credential(principal)
            except PolicyDenied:
                raise JobAuthorityError("initiating sponsorship is unavailable") from None
        if expected == 42:
            policy = principal.managed_policy
            if (
                authority.subject_kind != "machine"
                or policy is None
                or authority.sponsor_id != policy.subject_id
                or authority.client_policy_revision != policy.sponsorship_revision
                or authority.policy_context != policy.fingerprint
            ):
                raise JobAuthorityError("initiating machine policy context changed")
        if authority.action not in principal.scopes or principal.client_id != authority.client_id:
            raise JobAuthorityError("initiating capability was revoked")
        source = await self._database["research_sources"].find_one(
            {
                **org,
                "research_project_id": authority.research_project_id,
                "source_id": authority.source_id,
                "status": "active",
            }
        )
        if source is None:
            raise JobAuthorityError("job source is unavailable")
        if principal.managed_policy is not None and principal.managed_policy.managed:
            from app.security.policy.resources import resource_allows

            if not resource_allows(
                principal.managed_policy, authority.action, source, "research_sources"
            ):
                raise JobAuthorityError("current machine policy no longer covers the job source")
        projects = source.get("authorization", {}).get("project_ids", [])
        if (principal.projects is not None and not set(projects).issubset(principal.projects)) or (
            authority.project_ids is not None and not set(projects).issubset(authority.project_ids)
        ):
            raise JobAuthorityError("job source is outside initiating resource authority")

    async def _human(self, authority: JobAuthority) -> None:
        from app.identity.crypto import AuthRejected
        from app.identity.repository import IdentityStore
        from app.identity.sessions import BrowserSessions
        from app.security.policy.engine import PolicyEngine
        from app.security.policy.evaluation import PolicyDenied
        from app.security.policy.resources import resource_allows

        store = IdentityStore(self._database)
        row = await store.find(
            "browser_sessions",
            {
                "public_id": authority.session_id,
                "org_id": authority.org_id,
                "user_id": authority.user_id,
                "kind": "human",
                "revoked": False,
                "membership_revision": authority.membership_revision,
                "expires_at": {"$gt": utc_now()},
                "idle_expires_at": {"$gt": utc_now()},
            },
        )
        if not row:
            raise JobAuthorityError("initiating human session is unavailable")
        # Use the same mounted provider registry as browser requests. A
        # standalone checker without it deliberately refuses SSO authority.
        try:
            await (self._human_sessions or BrowserSessions(store)).authority(row)
            engine = PolicyEngine(
                store, human_management=True, managed_agents=authority.worker_schema_version == 42
            )
            snapshot = await engine.snapshot(authority.org_id, str(authority.user_id))
            source = await self._database["research_sources"].find_one(
                {
                    "org_id": authority.org_id,
                    "research_project_id": authority.research_project_id,
                    "source_id": authority.source_id,
                    "status": "active",
                }
            )
            if (
                not source
                or (
                    authority.project_ids is not None
                    and not set(source.get("authorization", {}).get("project_ids", [])).issubset(
                        authority.project_ids
                    )
                )
                or not resource_allows(
                    engine.policy(snapshot), authority.action, source, "research_sources"
                )
            ):
                raise JobAuthorityError("initiating human policy no longer covers the job source")
        except (AuthRejected, PolicyDenied):
            raise JobAuthorityError("initiating human authority is unavailable") from None
