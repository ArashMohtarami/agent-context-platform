"""Pure refusal/intent guards; doubles are not commissioning or activation proof."""

import copy
import hashlib
import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from scripts import evaluation_agent_value as handoff
from scripts import evaluation_lifecycle as lifecycle
from scripts.evaluation_operator import agent_value_policy
from scripts.focused_process import FocusedError


def reviewed():
    org, owner, worker = "eval-" + "a" * 32, "owner-id", "worker-id"
    expiry = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
    tuples = (
        [handoff.grant("grants:read", "member", owner)]
        + [
            handoff.grant(a, "member", worker)
            for a in ("grants:read", "grants:delegate", "members:read")
        ]
        + [
            handoff.grant(a, "organization", org)
            for a in ("clients:manage:org", "clients:read:org")
        ]
    )
    policy = {
        "tuples": tuples,
        "ceiling": handoff.domain(org) + handoff.own(worker),
        "targets": {"mode": "include", "ids": [worker]},
        "allowed_roles": ["contributor"],
        "expires_at": expiry,
    }
    state = {
        "schema": 42,
        "members": {
            r: {"user_id": v, "active": True, "mfa": True}
            for r, v in (("owner", owner), ("worker", worker))
        },
    }
    review = {
        "version": 1,
        "org_id": org,
        "owner_id": owner,
        "worker_id": worker,
        "projects": [handoff.PROJECT, handoff.OTHER],
        "clients": list(handoff.CLIENTS),
        "approval_reference": "fictional-v8-agent-value-unit-only",
        "policy_sha256": "",
    }
    review["policy_sha256"] = hashlib.sha256(handoff.canonical(policy)).hexdigest()
    return {"org_id": org, "expires_at": expiry}, state, review, policy


def test_handoff_intent_is_exact_bounded_and_recipient_delivery_is_once_only(tmp_path):
    intent, state, review, policy = reviewed()
    assert len(agent_value_policy(intent, state, review, policy).ceiling) == 12
    for changed in ("foreign-client", "all-org", "missing", "admin", "target", "expiry"):
        value = copy.deepcopy(policy)
        if changed == "foreign-client":
            next(r for r in value["ceiling"] if r["resource_kind"] == "client_memory")["ids"] += [
                "foreign"
            ]
        elif changed == "all-org":
            value["ceiling"][0].update(selector="all_in_org", ids=[])
        elif changed == "missing":
            value["ceiling"].pop()
        elif changed == "admin":
            value["tuples"][0]["ids"] = ["foreign-owner"]
        elif changed == "target":
            value["targets"]["ids"] = ["foreign-worker"]
        else:
            value["expires_at"] = (datetime.now(UTC) + timedelta(days=5)).isoformat()
        updated = {**review, "policy_sha256": hashlib.sha256(handoff.canonical(value)).hexdigest()}
        with pytest.raises(ValueError):
            agent_value_policy(intent, state, updated, value)
    with pytest.raises(ValueError):
        agent_value_policy(intent, state, {**review, "owner_id": review["worker_id"]}, policy)
    calls = []

    class Recipient:
        active = False

        def request(self, method, path, body=None):
            calls.append(method)
            if method == "GET":
                return {
                    "credentials": [{"status": "active"}] if self.active else [],
                    "client": {"revision": 2, "tuples": [], "expires_at": policy["expires_at"]},
                }
            self.active = True
            return {
                "token": "fictional-unit-token",
                "delivered": True,
                "key_id": "unit-key",
                "revision": 1,
            }

    recipient = Recipient()
    evaluation = SimpleNamespace(instance="a" * 32)
    target = tmp_path / "recipient"
    receipt = handoff.issue_client(evaluation, recipient, target, handoff.CLIENTS[0])
    assert receipt["saved_once"] and "token" not in receipt and target.stat().st_mode & 0o077 == 0
    with pytest.raises(FocusedError):
        handoff.issue_client(evaluation, recipient, target, handoff.CLIENTS[0])
    with pytest.raises(FocusedError, match="existing issuance"):
        handoff.issue_client(evaluation, recipient, tmp_path / "uncertain", handoff.CLIENTS[0])
    assert (tmp_path / "uncertain").read_bytes() == b"" and calls == ["GET", "POST", "GET"]
    with pytest.raises(FocusedError):
        handoff.issue_client(evaluation, recipient, tmp_path / "bad", "foreign")

    # Audit concrete credential values, never public placeholders such as [].
    state = tmp_path / "owned-control"
    (state / "secrets").mkdir(mode=0o700, parents=True)
    state.chmod(0o700)
    (state / "tls").mkdir(mode=0o700)
    for name in ("root", "app", "sync", "keyfile"):
        (state / "secrets" / name).write_text(
            "fictional-unit-control-" + name + "-secret-value-000"
        )
    mfa_value = "fictional-unit-encryption-key-value-000"
    (state / "secrets/mfa").write_text(
        json.dumps({"active": "eval-1", "keys": {"eval-1": mfa_value}})
    )
    (state / "secrets/oidc").write_text("[]")
    for name in ("ca.key", "server.key"):
        (state / "tls" / name).write_text("fictional-unit-tls-control-value-" + name)
    values = handoff.retained_control_values(state)
    assert len(values) == 7 and "[]" not in values and "eval-1" not in values
    artifact = tmp_path / "safe-metadata.json"
    artifact.write_text('{"items": []}')
    assert handoff.audit_artifacts([artifact], values)["credential_values_absent"]
    artifact.write_text(json.dumps({"must_refuse": mfa_value}))
    with pytest.raises(FocusedError, match="retained owned credential"):
        handoff.audit_artifacts([artifact], values)
    (state / "secrets/oidc").write_text('["undeclared-provider"]')
    with pytest.raises(FocusedError, match="declares no external"):
        handoff.retained_control_values(state)


def test_activation_selection_and_model_setup_refuse_widened_inputs(tmp_path, monkeypatch):
    monkeypatch.setattr(lifecycle, "ROOT", tmp_path)
    path = tmp_path / ".private/v8-agent-value-planning/phase-3/activation-selection.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"run": "unverified", "skip": True}))
    path.chmod(0o600)
    with pytest.raises(FocusedError, match="exact activation"):
        handoff.selected_activation()
    path.write_text(json.dumps({"run": "unverified"}))

    def refused(value):
        raise FocusedError("genuine ordered evidence required")

    monkeypatch.setattr(lifecycle, "activation_evidence", refused)
    with pytest.raises(FocusedError, match="genuine ordered"):
        handoff.selected_activation()
    path.chmod(0o644)
    with pytest.raises(FocusedError, match="private"):
        handoff.selected_activation()
    with pytest.raises(ValueError, match="explicit canonical"):
        handoff.enable_local_model(None, str(tmp_path / "absent-model"))
