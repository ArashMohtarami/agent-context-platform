"""Only delivery DTO can contain a token; persisted receipts are explicitly safe."""

from datetime import timedelta

import pytest
from pydantic import ValidationError

from app.api.routes.managed_agents import CredentialDTO, DeliveryDTO
from app.managed_agents.models import CredentialIssue
from app.managed_agents.schema import VALIDATORS
from app.managed_agents.service import receipt
from app.models.common import utc_now


def record():
    return {
        "org_id": "org-a",
        "client_id": "worker",
        "key_id": "1234567890abcdef",
        "recipient_id": "member",
        "issuance_id": "attempt-1",
        "status": "active",
        "revision": 1,
        "expires_at": utc_now() + timedelta(hours=1),
        "replaces_key_id": None,
    }


def test_safe_receipt_and_inspection_never_reveal_token_or_hash():
    expected = record()
    raw = {**expected, "token": "secret", "key_hash": "private", "command_hash": "private"}
    assert receipt(raw) == expected
    assert set(CredentialDTO.model_validate(raw).model_dump()) == set(record())
    assert "token" not in VALIDATORS["managed_credentials"]["$jsonSchema"]["properties"]
    assert "key_hash" not in VALIDATORS["managed_credentials"]["$jsonSchema"]["properties"]
    assert "secret" not in repr(DeliveryDTO(**record(), delivered=True, token="secret"))


@pytest.mark.parametrize(
    "extra",
    [{"token": "secret"}, {"key_hash": "hash"}, {"recipient_id": "other"}, {"org_id": "org-b"}],
)
def test_issue_command_cannot_choose_secret_recipient_or_org(extra):
    with pytest.raises(ValidationError):
        CredentialIssue(
            issuance_id="one",
            expected_client_revision=1,
            expires_at=utc_now() + timedelta(days=1),
            **extra,
        )


def test_retry_receipt_index_is_tenant_client_recipient_and_command_bound():
    from app.managed_agents.schema import INDEXES

    index = INDEXES["managed_credentials"][1].document
    assert (
        list(index["key"]) == ["org_id", "client_id", "recipient_id", "issuance_id"]
        and index["unique"]
    )


@pytest.mark.asyncio
async def test_idempotency_refuses_credential_material_before_persistence():
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from app.security.keys import mint
    from app.security.tenant import TenantContext, tenant_scope
    from app.services.gateway_idempotency_service import (
        GatewayIdempotencyService,
        IdempotencyConflictError,
    )

    repository = SimpleNamespace(complete=AsyncMock())
    with tenant_scope(TenantContext("org-a")):
        service = GatewayIdempotencyService(repository, retention_seconds=60)
        with pytest.raises(IdempotencyConflictError):
            await service.complete(
                None, result_text="safe", result_data={"nested": {"credential": mint().token}}
            )
    repository.complete.assert_not_called()


@pytest.mark.asyncio
async def test_limit_leases_are_single_use_and_policy_context_does_not_reset_quota():
    from dataclasses import replace

    from app.mcp.gateway_controls import (
        ClientLimitPolicy,
        InMemoryClientLimitBackend,
        LimitRequest,
        RateLimitExceededError,
    )

    backend = InMemoryClientLimitBackend()
    budget = ClientLimitPolicy(2, 2, 2, 2, 2)
    request = LimitRequest(
        "org-a", "worker", False, "fast", False, subject_id="member", policy_context="policy-one"
    )
    lease = await backend.acquire(request, budget)
    await backend.release(replace(lease, policy_context="foreign"))
    assert backend._windows[("org-a", "worker")].active_calls == 1
    await backend.release(lease)
    await backend.release(lease)
    assert backend._windows[("org-a", "worker")].active_calls == 0
    await backend.acquire(replace(request, policy_context="policy-two"), budget)
    with pytest.raises(RateLimitExceededError):
        await backend.acquire(replace(request, policy_context="policy-three"), budget)
